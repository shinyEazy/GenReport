from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.contracts.local_report import LocalReportConfig
from app.services.local_report_runner import LocalReportRunError, LocalReportRunner


def recording_trace_operation(calls):
    def trace_operation(function, *, name, run_type="chain", tags=None):
        if inspect.isasyncgenfunction(function):

            async def traced(*args, **kwargs):
                calls.append((name, run_type, tags, kwargs))
                async for item in function(*args, **kwargs):
                    yield item

            return traced
        if inspect.iscoroutinefunction(function):

            async def traced(*args, **kwargs):
                calls.append((name, run_type, tags, kwargs))
                return await function(*args, **kwargs)

            return traced

        def traced(*args, **kwargs):
            calls.append((name, run_type, tags, kwargs))
            return function(*args, **kwargs)

        return traced

    return trace_operation


class ToolCallingLLM:
    default_model = "default-model"

    def __init__(self) -> None:
        self.calls = 0
        self.messages: list[dict] | None = None
        self.models: list[str | None] = []

    async def stream_chat(self, messages, model=None, tool_definitions=None):
        self.calls += 1
        self.messages = messages
        self.models.append(model)
        if self.calls == 1:
            yield {
                "type": "done",
                "content": "",
                "tool_calls": [
                    {
                        "id": "write-report",
                        "function": {
                            "name": "execute_python",
                            "arguments": (
                                '{"code": "from pathlib import Path; '
                                "Path('report.txt').write_text('done')\"}"
                            ),
                        },
                    }
                ],
            }
            return
        yield {"type": "delta", "content": "Report ready."}
        yield {"type": "done", "content": "Report ready.", "tool_calls": []}


class ErrorLLM:
    default_model = "default-model"

    async def stream_chat(self, *args, **kwargs):
        yield {"type": "error", "content": "provider unavailable"}


class RecoveringToolLLM:
    default_model = "default-model"

    def __init__(self) -> None:
        self.calls = 0
        self.tool_result: dict | None = None

    async def stream_chat(self, *args, **kwargs):
        self.calls += 1
        messages = args[0]
        if self.calls == 1:
            yield {
                "type": "done",
                "content": "",
                "tool_calls": [
                    {
                        "id": "broken-tool",
                        "function": {
                            "name": "execute_python",
                            "arguments": '{"code": "raise RuntimeError(\'tool stderr\')"}',
                        },
                    }
                ],
            }
            return
        self.tool_result = messages[-1]
        yield {"type": "delta", "content": "Used a fallback."}
        yield {"type": "done", "content": "Used a fallback.", "tool_calls": []}


class LocalReportRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.source = root / "source.csv"
        self.source.write_text("value\n42\n", encoding="utf-8")
        self.config = LocalReportConfig(
            query="Create the report",
            files=[self.source],
            model="test-model",
            openai_api_key="config-key",
            openai_base_url="https://provider.example/v1",
            language="en",
            run_id="run_1",
        )
        self.settings = SimpleNamespace(
            LOCAL_MODE=True,
            LOCAL_WORKSPACE_ROOT=root / "workspaces",
            LOCAL_EXECUTION_TIMEOUT_SECONDS=10,
            MAX_AGENT_ITERATIONS=3,
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_local_workflow_traces_root_and_each_report_stage(self) -> None:
        trace_calls = []

        result = await LocalReportRunner(
            settings=self.settings,
            llm_service=ToolCallingLLM(),
            trace_operation=recording_trace_operation(trace_calls),
        ).run(self.config)

        self.assertEqual(result.output_text, "Report ready.")
        self.assertEqual(trace_calls[0][3], {"local_config": self.config})
        self.assertEqual(
            [call[0] for call in trace_calls],
            [
                "genreport-local-report-workflow",
                "local-workspace-preparation",
                "local-asset-materialization",
                "local-prompt-construction",
                "local-llm-round",
                "local-tool-execution",
                "local-llm-round",
                "local-artifact-finalization",
            ],
        )

    async def test_runs_tools_and_finalizes_local_artifacts(self) -> None:
        llm = ToolCallingLLM()
        result = await LocalReportRunner(settings=self.settings, llm_service=llm).run(
            self.config
        )

        self.assertEqual(result.output_text, "Report ready.")
        self.assertEqual(
            [item["filename"] for item in result.artifacts], ["report.txt"]
        )
        self.assertTrue((result.workspace.outputs_dir / "report.txt").is_file())
        self.assertIn("source.csv", llm.messages[0]["content"])
        self.assertEqual(llm.models, ["test-model", "test-model"])

    async def test_requires_enabled_local_mode_before_creating_workspace(self) -> None:
        self.settings.LOCAL_MODE = False

        with self.assertRaisesRegex(LocalReportRunError, "LOCAL_MODE=true"):
            await LocalReportRunner(
                settings=self.settings, llm_service=ToolCallingLLM()
            ).run(self.config)

        self.assertFalse((self.settings.LOCAL_WORKSPACE_ROOT / "run_1").exists())

    async def test_wraps_model_errors(self) -> None:
        with self.assertRaisesRegex(LocalReportRunError, "provider unavailable"):
            await LocalReportRunner(settings=self.settings, llm_service=ErrorLLM()).run(
                self.config
            )

    async def test_returns_tool_failure_to_the_model_for_recovery(self) -> None:
        llm = RecoveringToolLLM()

        result = await LocalReportRunner(settings=self.settings, llm_service=llm).run(
            self.config
        )

        self.assertEqual(result.output_text, "Used a fallback.")
        self.assertEqual(llm.calls, 2)
        self.assertEqual(llm.tool_result["role"], "tool")
        self.assertIn("RuntimeError: tool stderr", llm.tool_result["content"])


if __name__ == "__main__":
    unittest.main()
