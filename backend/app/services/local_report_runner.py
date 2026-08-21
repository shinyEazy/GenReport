from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.contracts.local_report import LocalReportConfig
from app.contracts.report_execution import ExecutionFileRequest
from app.services.axiom_tool_executor import AxiomToolExecutor
from app.services.local_execution_client import LocalExecutionClient
from app.services.local_workspace import LocalWorkspace
from app.services.report_prompt import render_system_prompt


class LocalReportRunError(RuntimeError):
    """Raised when a local report run cannot complete."""


@dataclass(frozen=True)
class LocalReportResult:
    workspace: LocalWorkspace
    output_text: str
    artifacts: list[dict[str, Any]]


class LocalReportRunner:
    def __init__(self, *, settings: Any, llm_service: Any) -> None:
        self.settings = settings
        self.llm_service = llm_service

    async def run(self, config: LocalReportConfig) -> LocalReportResult:
        if not self.settings.LOCAL_MODE:
            raise LocalReportRunError("Local reports require LOCAL_MODE=true.")

        run_id = config.run_id or f"local-{uuid4().hex}"
        workspace = LocalWorkspace.create(
            Path(self.settings.LOCAL_WORKSPACE_ROOT), run_id, config.files
        )
        files = self._execution_files(workspace, config.files)
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
            await executor.materialize_assets()
            messages = [
                {
                    "role": "system",
                    "content": render_system_prompt(
                        language=config.language,
                        input_path=workspace.virtual_inputs_path,
                        work_path=workspace.virtual_work_path,
                        output_path=workspace.virtual_outputs_path,
                        available_files=executor.get_available_files_prompt(),
                    ),
                },
                {
                    "role": "user",
                    "content": f"{config.query}",
                },
            ]
            generated_files: list[dict[str, Any]] = []
            output_parts: list[str] = []

            for _ in range(self.settings.MAX_AGENT_ITERATIONS):
                tool_calls: list[dict[str, Any]] = []
                round_content = ""
                async for chunk in self.llm_service.stream_chat(
                    messages,
                    model=config.model,
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
                        raise LocalReportRunError(str(chunk.get("content") or "Model failed."))

                if not tool_calls:
                    artifacts = await executor.finalize_generated_files(generated_files)
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
                    result = await executor.execute_tool(name, arguments)
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
            raise LocalReportRunError("The report model emitted invalid tool arguments.") from exc
        if not name or not isinstance(arguments, dict):
            raise LocalReportRunError("The report model emitted invalid tool arguments.")
        return name, arguments
