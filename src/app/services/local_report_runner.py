from __future__ import annotations

import json
import mimetypes
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.contracts.local_report import LocalReportConfig
from app.contracts.report_execution import ExecutionFileRequest
from app.services.axiom_tool_executor import AxiomToolExecutor
from app.services.local_execution_client import LocalExecutionClient
from app.services.local_workspace import LocalWorkspace
from app.services.report_citations import citation_requirements
from app.services.report_prompt import render_system_prompt
from app.services.report_tracing import (
    LOCAL_WORKFLOW_TAGS,
    trace_operation as default_trace_operation,
)


TraceOperation = Callable[..., Callable[..., Any]]


class LocalReportRunError(RuntimeError):
    """Raised when a local report run cannot complete."""


@dataclass(frozen=True)
class LocalReportResult:
    workspace: LocalWorkspace
    output_text: str
    artifacts: list[dict[str, Any]]


class LocalReportRunner:
    def __init__(
        self,
        *,
        settings: Any,
        llm_service: Any,
        trace_operation: TraceOperation = default_trace_operation,
    ) -> None:
        self.settings = settings
        self.llm_service = llm_service
        self._run_traced = trace_operation(
            self._run_impl,
            name="genreport-local-report-workflow",
            run_type="chain",
            tags=list(LOCAL_WORKFLOW_TAGS),
        )
        self._prepare_workspace_traced = trace_operation(
            self._prepare_workspace_impl,
            name="local-workspace-preparation",
            run_type="chain",
            tags=[*LOCAL_WORKFLOW_TAGS, "workspace"],
        )
        self._materialize_assets_traced = trace_operation(
            self._materialize_assets_impl,
            name="local-asset-materialization",
            run_type="chain",
            tags=[*LOCAL_WORKFLOW_TAGS, "materialization"],
        )
        self._build_messages_traced = trace_operation(
            self._build_messages_impl,
            name="local-prompt-construction",
            run_type="chain",
            tags=[*LOCAL_WORKFLOW_TAGS, "prompt"],
        )
        self._stream_llm_round_traced = trace_operation(
            self._stream_llm_round_impl,
            name="local-llm-round",
            run_type="llm",
            tags=[*LOCAL_WORKFLOW_TAGS, "llm"],
        )
        self._execute_tool_traced = trace_operation(
            self._execute_tool_impl,
            name="local-tool-execution",
            run_type="tool",
            tags=[*LOCAL_WORKFLOW_TAGS, "tool"],
        )
        self._finalize_artifacts_traced = trace_operation(
            self._finalize_artifacts_impl,
            name="local-artifact-finalization",
            run_type="chain",
            tags=[*LOCAL_WORKFLOW_TAGS, "artifact"],
        )

    async def run(self, config: LocalReportConfig) -> LocalReportResult:
        return await self._run_traced(local_config=config)

    async def _run_impl(
        self,
        *,
        local_config: LocalReportConfig,
    ) -> LocalReportResult:
        if not self.settings.LOCAL_MODE:
            raise LocalReportRunError("Local reports require LOCAL_MODE=true.")

        run_id = local_config.run_id or f"local-{uuid4().hex}"
        workspace = self._prepare_workspace_traced(
            local_config=local_config,
            run_id=run_id,
        )
        files = self._execution_files(workspace, local_config.files)
        client = LocalExecutionClient(
            workspace,
            timeout_seconds=self.settings.LOCAL_EXECUTION_TIMEOUT_SECONDS,
        )
        executor = AxiomToolExecutor(
            client=client,
            files=files,
            input_path=workspace.virtual_inputs_path,
            work_path=workspace.virtual_work_path,
            output_path=workspace.virtual_outputs_path,
        )
        try:
            await self._materialize_assets_traced(executor=executor)
            messages = self._build_messages_traced(
                local_config=local_config,
                workspace=workspace,
                available_files=executor.get_available_files_prompt(),
                citation_instructions=citation_requirements(files),
            )
            generated_files: list[dict[str, Any]] = []
            output_parts: list[str] = []

            for _ in range(self.settings.MAX_AGENT_ITERATIONS):
                tool_calls: list[dict[str, Any]] = []
                round_content = ""
                async for chunk in self._stream_llm_round_traced(
                    messages=messages,
                    model=local_config.model,
                    tool_definitions=executor.get_tool_definitions(),
                ):
                    chunk_type = chunk.get("type")
                    if chunk_type == "delta":
                        delta = str(chunk.get("content") or "")
                        round_content += delta
                        output_parts.append(delta)
                    elif chunk_type == "tool_call" and isinstance(
                        chunk.get("tool_call"), dict
                    ):
                        tool_calls.append(chunk["tool_call"])
                    elif chunk_type == "done":
                        done_content = str(chunk.get("content") or "")
                        if done_content and not round_content:
                            round_content = done_content
                            output_parts.append(done_content)
                        done_calls = chunk.get("tool_calls")
                        if isinstance(done_calls, list):
                            tool_calls = [
                                item for item in done_calls if isinstance(item, dict)
                            ]
                    elif chunk_type == "error":
                        raise LocalReportRunError(
                            str(chunk.get("content") or "Model failed.")
                        )

                if not tool_calls:
                    artifacts = await self._finalize_artifacts_traced(
                        executor=executor,
                        generated_files=generated_files,
                    )
                    return LocalReportResult(
                        workspace=workspace,
                        output_text="".join(output_parts),
                        artifacts=artifacts,
                    )

                messages.append(
                    {
                        "role": "assistant",
                        "content": round_content or None,
                        "tool_calls": tool_calls,
                    }
                )
                for tool_call in tool_calls:
                    name, arguments = self._parse_tool_call(tool_call)
                    result = await self._execute_tool_traced(
                        executor=executor,
                        name=name,
                        arguments=arguments,
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
                            "name": name,
                            "content": json.dumps(result, default=str),
                        }
                    )
            raise LocalReportRunError("The report model exceeded its execution limit.")
        except LocalReportRunError:
            raise
        except Exception as exc:
            raise LocalReportRunError(str(exc)) from exc
        finally:
            await executor.close()

    def _prepare_workspace_impl(
        self,
        *,
        local_config: LocalReportConfig,
        run_id: str,
    ) -> LocalWorkspace:
        return LocalWorkspace.create(
            Path(self.settings.LOCAL_WORKSPACE_ROOT), run_id, local_config.files
        )

    @staticmethod
    async def _materialize_assets_impl(*, executor: AxiomToolExecutor) -> None:
        await executor.materialize_assets()

    @staticmethod
    def _build_messages_impl(
        *,
        local_config: LocalReportConfig,
        workspace: LocalWorkspace,
        available_files: str,
        citation_instructions: str = "",
    ) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": render_system_prompt(
                    language=local_config.language,
                    input_path=workspace.virtual_inputs_path,
                    work_path=workspace.virtual_work_path,
                    output_path=workspace.virtual_outputs_path,
                    available_files=available_files,
                    citation_instructions=citation_instructions,
                ),
            },
            {
                "role": "user",
                "content": f"{local_config.query}",
            },
        ]

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
    async def _finalize_artifacts_impl(
        *,
        executor: AxiomToolExecutor,
        generated_files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return await executor.finalize_generated_files(generated_files)

    @staticmethod
    def _execution_files(
        workspace: LocalWorkspace, source_files: list[Path]
    ) -> list[ExecutionFileRequest]:
        return [
            ExecutionFileRequest(
                artifact_id=f"local:{source.name}",
                filename=source.name,
                sandbox_path=workspace.virtual_input_path(source.name),
                content_type=mimetypes.guess_type(source.name)[0]
                or "application/octet-stream",
                size=(workspace.inputs_dir / source.name).stat().st_size,
            )
            for source in source_files
        ]

    @staticmethod
    def _parse_tool_call(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            raise LocalReportRunError("The report model emitted an invalid tool call.")
        name = str(function.get("name") or "")
        try:
            arguments = json.loads(str(function.get("arguments") or "{}"))
        except json.JSONDecodeError as exc:
            raise LocalReportRunError(
                "The report model emitted invalid tool arguments."
            ) from exc
        if not name or not isinstance(arguments, dict):
            raise LocalReportRunError(
                "The report model emitted invalid tool arguments."
            )
        return name, arguments
