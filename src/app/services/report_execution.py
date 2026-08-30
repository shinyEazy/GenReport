from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from math import ceil
from typing import Any, Literal

from app.contracts.report_execution import (
    ReportCompletion,
    ReportEvent,
    ReportExecutionRequest,
    ReportFailure,
    ReportInputsSelected,
    ReportUsage,
)
from app.services.axiom_tool_executor import AxiomToolExecutor
from app.services.report_events import ReportEventFactory
from app.services.report_input_preparation import (
    ReportInputPreparationError,
    ReportInputPreparationService,
)
from app.services.report_prompt import build_report_messages
from app.services.report_tracing import (
    REMOTE_WORKFLOW_TAGS,
    trace_operation as default_trace_operation,
)
from app.services.runtime_gateway_client import RuntimeGatewayClient

logger = logging.getLogger(__name__)


FailurePhase = Literal[
    "validation",
    "discovery",
    "model",
    "tool",
    "artifact",
    "authorization",
    "cancellation",
    "internal",
]
TraceOperation = Callable[..., Callable[..., Any]]


class _ReportPhaseError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        phase: FailurePhase,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.safe_message = message
        self.retryable = retryable


def _file_descriptors(files: list[Any]) -> list[str]:
    return [
        f"{getattr(item, 'filename', 'unknown')}"
        f"[{getattr(item, 'content_type', None) or 'unknown'}]"
        for item in files
    ]


def _selected_input_descriptors(inputs: list[Any]) -> list[str]:
    return [
        f"{getattr(item, 'filename', 'unknown')}[{getattr(item, 'role', 'unknown')}]"
        for item in inputs
    ]


def _tool_call_names(tool_calls: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in tool_calls:
        function = item.get("function")
        names.append(
            str(function.get("name") or "unknown")
            if isinstance(function, dict)
            else "unknown"
        )
    return names


def _generated_file_descriptors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    descriptors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename") or item.get("name") or "unknown"
        content_type = item.get("content_type") or "unknown"
        descriptors.append(f"{filename}[{content_type}]")
    return descriptors


class ReportExecutionService:
    def __init__(
        self,
        *,
        llm_service: Any,
        input_preparer: ReportInputPreparationService,
        executor_factory: Callable[[ReportExecutionRequest], AxiomToolExecutor],
        event_factory_builder: Callable[[ReportExecutionRequest], ReportEventFactory],
        max_iterations: int,
        runtime_gateway_client: RuntimeGatewayClient | None = None,
        multimodal_models: list[str] | tuple[str, ...] = (),
        trace_operation: TraceOperation = default_trace_operation,
        workflow_name: str = "genreport-report-workflow",
        workflow_tags: list[str] | tuple[str, ...] | None = None,
        emit_selected_inputs: bool = True,
        preparing_message: str = "Preparing report execution.",
        input_preparation_failure_phase: FailurePhase = "discovery",
        prompt_builder: Callable[..., list[dict[str, Any]]] = build_report_messages,
        pdf_page_image_max_pages: int = 0,
    ) -> None:
        self.llm_service = llm_service
        self.input_preparer = input_preparer
        self.executor_factory = executor_factory
        self.event_factory_builder = event_factory_builder
        self.max_iterations = max(1, max_iterations)
        self.runtime_gateway_client = runtime_gateway_client or RuntimeGatewayClient()
        self.multimodal_models = {
            model.strip().casefold() for model in multimodal_models if model.strip()
        }
        self.workflow_name = workflow_name
        self.workflow_tags = list(workflow_tags or REMOTE_WORKFLOW_TAGS)
        self.emit_selected_inputs = emit_selected_inputs
        self.preparing_message = preparing_message
        self.input_preparation_failure_phase = input_preparation_failure_phase
        self.prompt_builder = prompt_builder
        self.pdf_page_image_max_pages = max(0, pdf_page_image_max_pages)
        self._stream_traced = trace_operation(
            self._stream_impl,
            name=workflow_name,
            run_type="chain",
            tags=list(self.workflow_tags),
        )
        self._stream_llm_round_traced = trace_operation(
            self._stream_llm_round_impl,
            name="model",
            run_type="llm",
            tags=[*self.workflow_tags, "model"],
        )
        self._execute_tool_traced = trace_operation(
            self._execute_tool_impl,
            name="tools",
            run_type="tool",
            tags=[*self.workflow_tags, "tools"],
        )

    async def stream(
        self,
        request: ReportExecutionRequest,
    ) -> AsyncIterator[ReportEvent]:
        event_factory = self.event_factory_builder(request)
        logger.info(
            "genreport workflow started response_id=%s run_id=%s "
            "organization_id=%s workspace_id=%s primary_source_id=%s "
            "discover_workspace_files=%s execution_file_count=%s execution_files=%s",
            request.response_id,
            request.run_id,
            request.organization_id,
            request.workspace_id,
            request.primary_source_id,
            request.discover_workspace_files,
            len(request.execution_files),
            _file_descriptors(request.execution_files),
        )
        yield event_factory.create(
            "report.status",
            {"phase": "preparing", "message": self.preparing_message},
        )
        try:
            prepared_inputs = await self._prepare_inputs_impl(request=request)
        except ReportInputPreparationError as exc:
            logger.exception(
                "genreport input preparation failed response_id=%s run_id=%s "
                "organization_id=%s workspace_id=%s",
                request.response_id,
                request.run_id,
                request.organization_id,
                request.workspace_id,
            )
            failure = ReportFailure(
                code="report_input_preparation_failed",
                phase=self.input_preparation_failure_phase,
                message=str(exc),
                retryable=True,
            )
            yield event_factory.create(
                "report.failed",
                failure.model_dump(mode="json"),
            )
            return
        except Exception as exc:
            logger.exception(
                "genreport input preparation raised unexpected error "
                "response_id=%s run_id=%s error_type=%s",
                request.response_id,
                request.run_id,
                type(exc).__name__,
            )
            failure = self._unexpected_failure(exc)
            yield event_factory.create(
                "report.failed",
                failure.model_dump(mode="json"),
            )
            return

        logger.info(
            "genreport input preparation completed response_id=%s run_id=%s "
            "selected_input_count=%s selected_inputs=%s execution_file_count=%s "
            "execution_files=%s",
            request.response_id,
            request.run_id,
            len(prepared_inputs.selected_inputs),
            _selected_input_descriptors(prepared_inputs.selected_inputs),
            len(prepared_inputs.files),
            _file_descriptors(prepared_inputs.files),
        )

        effective_request = request.model_copy(
            update={"execution_files": prepared_inputs.files}
        )
        async for event in self._stream_traced(
            request=effective_request,
            selected_inputs=prepared_inputs.selected_inputs,
        ):
            yield event

    async def _stream_impl(
        self,
        *,
        request: ReportExecutionRequest,
        selected_inputs: list[Any],
    ) -> AsyncIterator[ReportEvent]:
        event_factory = self.event_factory_builder(request)
        executor: AxiomToolExecutor | None = None
        try:
            executor = self.executor_factory(request)
            await executor.materialize_assets()
            logger.info(
                "genreport assets materialized response_id=%s run_id=%s "
                "execution_file_count=%s execution_files=%s",
                request.response_id,
                request.run_id,
                len(request.execution_files),
                _file_descriptors(request.execution_files),
            )
            if self.emit_selected_inputs:
                yield event_factory.create(
                    "report.inputs.selected",
                    ReportInputsSelected(inputs=selected_inputs).model_dump(
                        mode="json"
                    ),
                )
            selected_model = request.model or getattr(
                self.llm_service, "default_model", ""
            )
            image_parts: list[dict[str, Any]] | None = None
            if self._is_multimodal_model(selected_model):
                if self.pdf_page_image_max_pages:
                    get_pdf_page_image_parts = getattr(
                        executor, "get_pdf_page_image_parts", None
                    )
                    if callable(get_pdf_page_image_parts):
                        image_parts = await get_pdf_page_image_parts(
                            max_pages=self.pdf_page_image_max_pages
                        )
                if image_parts is None:
                    image_parts = await executor.get_multimodal_image_parts()
            messages = self.prompt_builder(
                request,
                available_files=executor.get_available_files_prompt(),
                image_parts=image_parts,
            )
            generated_files: list[dict[str, Any]] = []
            output_parts: list[str] = []
            usage_totals = {
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
            }
            provider_usage_seen = False

            for iteration in range(self.max_iterations):
                logger.info(
                    "genreport model round started response_id=%s run_id=%s "
                    "iteration=%s max_iterations=%s message_count=%s",
                    request.response_id,
                    request.run_id,
                    iteration + 1,
                    self.max_iterations,
                    len(messages),
                )
                tool_calls: list[dict[str, Any]] = []
                round_content = ""
                round_usage: dict[str, Any] | None = None
                try:
                    async for chunk in self._stream_llm_round_traced(
                        messages=messages,
                        model=request.model,
                        tool_definitions=executor.get_tool_definitions(),
                    ):
                        chunk_type = chunk.get("type")
                        if chunk_type == "delta":
                            delta = str(chunk.get("content") or "")
                            if delta:
                                round_content += delta
                                output_parts.append(delta)
                                yield event_factory.create(
                                    "report.output_text.delta",
                                    {"delta": delta},
                                )
                        elif chunk_type == "tool_call":
                            tool_call = chunk.get("tool_call")
                            if isinstance(tool_call, dict):
                                tool_calls.append(tool_call)
                        elif chunk_type == "done":
                            done_content = str(chunk.get("content") or "")
                            if done_content and not round_content:
                                round_content = done_content
                                output_parts.append(done_content)
                                yield event_factory.create(
                                    "report.output_text.delta",
                                    {"delta": done_content},
                                )
                            done_calls = chunk.get("tool_calls")
                            if isinstance(done_calls, list):
                                tool_calls = [
                                    item
                                    for item in done_calls
                                    if isinstance(item, dict)
                                ]
                            if isinstance(chunk.get("usage"), dict):
                                round_usage = chunk["usage"]
                        elif chunk_type == "error":
                            raise RuntimeError(
                                str(chunk.get("content") or "Model failed")
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception(
                        "genreport model round failed response_id=%s run_id=%s "
                        "iteration=%s",
                        request.response_id,
                        request.run_id,
                        iteration + 1,
                    )
                    raise _ReportPhaseError(
                        code="model_execution_failed",
                        phase="model",
                        message="The report model could not complete the request.",
                        retryable=True,
                    ) from exc

                if round_usage is not None:
                    provider_usage_seen = True
                    normalized = self._normalize_usage(round_usage)
                    for key in usage_totals:
                        usage_totals[key] += normalized[key]

                logger.info(
                    "genreport model round completed response_id=%s run_id=%s "
                    "iteration=%s tool_count=%s tool_names=%s output_chars=%s",
                    request.response_id,
                    request.run_id,
                    iteration + 1,
                    len(tool_calls),
                    _tool_call_names(tool_calls),
                    len(round_content),
                )

                if not tool_calls:
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": round_content or None,
                        "tool_calls": tool_calls,
                    }
                )
                for tool_call in tool_calls:
                    name, arguments = self._parse_tool_call(tool_call)
                    tool_call_id = str(tool_call.get("id") or "tool")
                    logger.info(
                        "genreport tool started response_id=%s run_id=%s "
                        "iteration=%s tool_call_id=%s tool_name=%s",
                        request.response_id,
                        request.run_id,
                        iteration + 1,
                        tool_call_id,
                        name,
                    )
                    gateway = self._runtime_gateway(request, executor)
                    started_payload = {
                        "tool_call_id": tool_call_id,
                        "tool_name": name,
                        "label": name,
                        "phase": "tool",
                        "status": "started",
                        "inputs": arguments,
                    }
                    await self.runtime_gateway_client.record_event(
                        gateway,
                        "report.tool.started",
                        started_payload,
                        status="started",
                    )
                    yield event_factory.create(
                        "report.tool.started",
                        started_payload,
                    )
                    try:
                        result = await self._execute_tool_traced(
                            executor=executor,
                            name=name,
                            arguments=arguments,
                        )
                    except Exception as exc:
                        logger.exception(
                            "genreport tool failed response_id=%s run_id=%s "
                            "tool_call_id=%s tool_name=%s",
                            request.response_id,
                            request.run_id,
                            tool_call_id,
                            name,
                        )
                        failed_payload = {
                            "tool_call_id": tool_call_id,
                            "tool_name": name,
                            "label": name,
                            "phase": "tool",
                            "status": "failed",
                            "inputs": arguments,
                            "error": "Tool execution raised an exception.",
                        }
                        await self.runtime_gateway_client.record_event(
                            self._runtime_gateway(request, executor),
                            "report.tool.failed",
                            failed_payload,
                            status="failed",
                        )
                        yield event_factory.create(
                            "report.tool.failed",
                            failed_payload,
                        )
                        raise _ReportPhaseError(
                            code="tool_execution_failed",
                            phase="tool",
                            message="A report tool failed to execute.",
                            retryable=True,
                        ) from exc
                    completed_status = (
                        "completed" if result.get("success") else "failed"
                    )
                    logger.info(
                        "genreport tool completed response_id=%s run_id=%s "
                        "tool_call_id=%s tool_name=%s status=%s success=%s "
                        "generated_files=%s",
                        request.response_id,
                        request.run_id,
                        tool_call_id,
                        name,
                        completed_status,
                        bool(result.get("success")),
                        _generated_file_descriptors(result.get("generated_files")),
                    )
                    completed_payload = {
                        "tool_call_id": tool_call_id,
                        "tool_name": name,
                        "label": name,
                        "phase": "tool",
                        "status": completed_status,
                        "success": bool(result.get("success")),
                        "inputs": arguments,
                        "outputs": result,
                    }
                    await self.runtime_gateway_client.record_event(
                        self._runtime_gateway(request, executor),
                        "report.tool.completed",
                        completed_payload,
                        status=completed_status,
                    )
                    yield event_factory.create(
                        "report.tool.completed",
                        completed_payload,
                    )
                    generated = result.get("generated_files")
                    if isinstance(generated, list):
                        generated_files.extend(
                            item for item in generated if isinstance(item, dict)
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(tool_call.get("id") or "tool"),
                            "name": str(
                                (tool_call.get("function") or {}).get("name")
                                or "unknown"
                            ),
                            "content": json.dumps(result, default=str),
                        }
                    )
            else:
                raise _ReportPhaseError(
                    code="max_agent_iterations_exceeded",
                    phase="model",
                    message="The report model exceeded its execution limit.",
                    retryable=False,
                )

            output_text = "".join(output_parts)
            usage = self._build_usage(
                request=request,
                messages=messages,
                output_text=output_text,
                totals=usage_totals,
                provider_usage_seen=provider_usage_seen,
            )
            try:
                logger.info(
                    "genreport artifact finalization started response_id=%s "
                    "run_id=%s generated_file_count=%s generated_files=%s",
                    request.response_id,
                    request.run_id,
                    len(generated_files),
                    _generated_file_descriptors(generated_files),
                )
                artifacts = await executor.finalize_generated_files(
                    generated_files,
                    workspace_id=request.workspace_id,
                )
                completion = ReportCompletion(
                    output_text=output_text,
                    artifacts=artifacts,
                    usage=usage,
                )
                logger.info(
                    "genreport artifact finalization completed response_id=%s "
                    "run_id=%s artifact_count=%s artifacts=%s",
                    request.response_id,
                    request.run_id,
                    len(artifacts),
                    _generated_file_descriptors(artifacts),
                )
            except Exception as exc:
                logger.exception(
                    "genreport artifact finalization failed response_id=%s run_id=%s",
                    request.response_id,
                    request.run_id,
                )
                raise _ReportPhaseError(
                    code="artifact_finalization_failed",
                    phase="artifact",
                    message="Generated report artifacts could not be finalized.",
                    retryable=True,
                ) from exc

            yield event_factory.create(
                "report.usage",
                usage.model_dump(mode="json"),
            )
            logger.info(
                "genreport workflow completed response_id=%s run_id=%s "
                "artifact_count=%s artifacts=%s output_text_length=%s",
                request.response_id,
                request.run_id,
                len(artifacts),
                _generated_file_descriptors(artifacts),
                len(output_text),
            )
            yield event_factory.create(
                "report.completed",
                completion.model_dump(mode="json"),
            )
        except asyncio.CancelledError:
            logger.warning(
                "genreport workflow cancelled response_id=%s run_id=%s",
                request.response_id,
                request.run_id,
            )
            raise
        except _ReportPhaseError as exc:
            logger.error(
                "genreport workflow failed response_id=%s run_id=%s "
                "code=%s phase=%s retryable=%s message=%s",
                request.response_id,
                request.run_id,
                exc.code,
                exc.phase,
                exc.retryable,
                exc.safe_message,
            )
            failure = ReportFailure(
                code=exc.code,
                phase=exc.phase,
                message=exc.safe_message,
                retryable=exc.retryable,
            )
            yield event_factory.create(
                "report.failed",
                failure.model_dump(mode="json"),
            )
        except Exception as exc:
            logger.exception(
                "genreport workflow raised unexpected error response_id=%s "
                "run_id=%s error_type=%s",
                request.response_id,
                request.run_id,
                type(exc).__name__,
            )
            failure = self._unexpected_failure(exc)
            yield event_factory.create(
                "report.failed",
                failure.model_dump(mode="json"),
            )
        finally:
            if executor is not None:
                await executor.close()

    async def _prepare_inputs_impl(
        self,
        *,
        request: ReportExecutionRequest,
    ) -> Any:
        return await self.input_preparer.prepare(
            query=request.workspace_discovery_instruction or request.instruction,
            existing_files=list(request.execution_files),
            discover_workspace_files=request.discover_workspace_files,
            organization_id=request.organization_id,
            workspace_id=request.workspace_id,
            runtime_gateway=request.runtime_gateway.model_dump(mode="json"),
            model=request.model,
            primary_source_id=request.primary_source_id,
            selected_files=request.selected_files,
        )

    async def _stream_llm_round_impl(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        tool_definitions: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        async for chunk in self.llm_service.stream_chat(
            messages,
            model=model,
            tool_definitions=tool_definitions,
        ):
            yield chunk

    @staticmethod
    async def _execute_tool_impl(
        *,
        executor: AxiomToolExecutor,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return await executor.execute_tool(name, arguments)

    @staticmethod
    def _parse_tool_call(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            raise _ReportPhaseError(
                code="invalid_tool_call",
                phase="tool",
                message="The report model emitted an invalid tool call.",
                retryable=False,
            )
        name = str(function.get("name") or "")
        try:
            arguments = json.loads(str(function.get("arguments") or "{}"))
        except json.JSONDecodeError as exc:
            raise _ReportPhaseError(
                code="invalid_tool_arguments",
                phase="tool",
                message="The report model emitted invalid tool arguments.",
                retryable=False,
            ) from exc
        if not isinstance(arguments, dict):
            raise _ReportPhaseError(
                code="invalid_tool_arguments",
                phase="tool",
                message="The report model emitted invalid tool arguments.",
                retryable=False,
            )
        return name, arguments

    @staticmethod
    def _runtime_gateway(
        request: ReportExecutionRequest,
        executor: AxiomToolExecutor,
    ) -> dict[str, Any]:
        """Use the executor's renewed capability token for gateway callbacks."""

        gateway = request.runtime_gateway.model_dump(mode="json")
        context = getattr(getattr(executor, "client", None), "context", None)
        token = getattr(context, "capability_token", None)
        if isinstance(token, str) and token:
            gateway["token"] = token
        expires_at = getattr(context, "expires_at", None)
        if isinstance(expires_at, int):
            gateway["expires_at"] = expires_at
        endpoint = getattr(context, "gateway_url", None)
        if endpoint is not None:
            gateway["endpoint"] = str(endpoint)
        return gateway

    def _is_multimodal_model(self, model: str | None) -> bool:
        return bool(model and model.strip().casefold() in self.multimodal_models)

    @staticmethod
    def _normalize_usage(value: dict[str, Any]) -> dict[str, int]:
        input_tokens = int(value.get("input_tokens") or value.get("prompt_tokens") or 0)
        output_tokens = int(
            value.get("output_tokens") or value.get("completion_tokens") or 0
        )
        reasoning_tokens = int(value.get("reasoning_tokens") or 0)
        total_tokens = int(
            value.get("total_tokens") or input_tokens + output_tokens + reasoning_tokens
        )
        return {
            "input_tokens": max(0, input_tokens),
            "output_tokens": max(0, output_tokens),
            "reasoning_tokens": max(0, reasoning_tokens),
            "total_tokens": max(0, total_tokens),
        }

    def _build_usage(
        self,
        *,
        request: ReportExecutionRequest,
        messages: list[dict[str, Any]],
        output_text: str,
        totals: dict[str, int],
        provider_usage_seen: bool,
    ) -> ReportUsage:
        model = (
            request.model
            or getattr(self.llm_service, "default_model", None)
            or "unknown"
        )
        if provider_usage_seen:
            return ReportUsage(model=model, estimated=False, **totals)
        input_characters = len(json.dumps(messages, default=str))
        input_tokens = ceil(input_characters / 3.5)
        output_tokens = ceil(len(output_text) / 3.5)
        return ReportUsage(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=0,
            total_tokens=input_tokens + output_tokens,
            estimated=True,
        )

    @staticmethod
    def _unexpected_failure(exc: Exception) -> ReportFailure:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in {401, 403}:
            return ReportFailure(
                code="runtime_authorization_failed",
                phase="authorization",
                message="AXIOM runtime authorization failed.",
                retryable=False,
            )
        return ReportFailure(
            code="internal_report_error",
            phase="internal",
            message="The report engine encountered an internal error.",
            retryable=False,
        )
