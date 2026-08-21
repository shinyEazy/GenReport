from __future__ import annotations

import asyncio
import json
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
from app.services.runtime_gateway_client import RuntimeGatewayClient


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
    ) -> None:
        self.llm_service = llm_service
        self.input_preparer = input_preparer
        self.executor_factory = executor_factory
        self.event_factory_builder = event_factory_builder
        self.max_iterations = max(1, max_iterations)
        self.runtime_gateway_client = runtime_gateway_client or RuntimeGatewayClient()
        self.multimodal_models = {
            model.strip().casefold()
            for model in multimodal_models
            if model.strip()
        }

    async def stream(
        self,
        request: ReportExecutionRequest,
    ) -> AsyncIterator[ReportEvent]:
        event_factory = self.event_factory_builder(request)
        executor: AxiomToolExecutor | None = None
        try:
            yield event_factory.create(
                "report.status",
                {"phase": "preparing", "message": "Preparing report execution."},
            )
            try:
                prepared_inputs = await self.input_preparer.prepare(
                    query=request.workspace_discovery_instruction
                    or request.instruction,
                    existing_files=list(request.execution_files),
                    discover_workspace_files=request.discover_workspace_files,
                    organization_id=request.organization_id,
                    workspace_id=request.workspace_id,
                    runtime_gateway=request.runtime_gateway.model_dump(mode="json"),
                    model=request.model,
                    primary_source_id=request.primary_source_id,
                )
            except ReportInputPreparationError as exc:
                raise _ReportPhaseError(
                    code="report_input_preparation_failed",
                    phase="discovery",
                    message=str(exc),
                    retryable=True,
                ) from exc

            effective_request = request.model_copy(
                update={"execution_files": prepared_inputs.files}
            )
            executor = self.executor_factory(effective_request)
            await executor.materialize_assets()
            yield event_factory.create(
                "report.inputs.selected",
                ReportInputsSelected(
                    inputs=prepared_inputs.selected_inputs
                ).model_dump(mode="json"),
            )
            selected_model = (
                effective_request.model
                or getattr(self.llm_service, "default_model", "")
            )
            image_parts: list[dict[str, Any]] | None = None
            if self._is_multimodal_model(selected_model):
                image_parts = await executor.get_multimodal_image_parts()
            messages = build_report_messages(
                effective_request,
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
                tool_calls: list[dict[str, Any]] = []
                round_content = ""
                round_usage: dict[str, Any] | None = None
                try:
                    async for chunk in self.llm_service.stream_chat(
                        messages,
                        model=effective_request.model,
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
                                    item for item in done_calls if isinstance(item, dict)
                                ]
                            if isinstance(chunk.get("usage"), dict):
                                round_usage = chunk["usage"]
                        elif chunk_type == "error":
                            raise RuntimeError(str(chunk.get("content") or "Model failed"))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
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
                    gateway = effective_request.runtime_gateway.model_dump(mode="json")
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
                        result = await executor.execute_tool(name, arguments)
                    except Exception as exc:
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
                            gateway,
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
                        gateway,
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
                request=effective_request,
                messages=messages,
                output_text=output_text,
                totals=usage_totals,
                provider_usage_seen=provider_usage_seen,
            )
            try:
                artifacts = await executor.finalize_generated_files(
                    generated_files,
                    workspace_id=effective_request.workspace_id,
                )
                completion = ReportCompletion(
                    output_text=output_text,
                    artifacts=artifacts,
                    usage=usage,
                )
            except Exception as exc:
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
            yield event_factory.create(
                "report.completed",
                completion.model_dump(mode="json"),
            )
        except asyncio.CancelledError:
            raise
        except _ReportPhaseError as exc:
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
            failure = self._unexpected_failure(exc)
            yield event_factory.create(
                "report.failed",
                failure.model_dump(mode="json"),
            )
        finally:
            if executor is not None:
                await executor.close()

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
            value.get("total_tokens")
            or input_tokens + output_tokens + reasoning_tokens
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
        model = request.model or getattr(self.llm_service, "default_model", None) or "unknown"
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
