import asyncio
import json
import unittest

from app.contracts.report_execution import ReportExecutionRequest
from app.services.report_events import ReportEventFactory
from app.services.report_execution import ReportExecutionService
from tests.test_report_contract import valid_payload


class FakeInputPreparer:
    async def prepare(self, **kwargs):
        return kwargs["existing_files"]


class FakeExecutor:
    def __init__(
        self,
        artifact_ref: str = "artifact://report-1",
        artifacts: list[dict] | None = None,
    ) -> None:
        self.artifact_ref = artifact_ref
        self.artifacts = artifacts
        self.closed = False
        self.todos = []

    async def materialize_assets(self):
        return None

    def get_available_files_prompt(self):
        return "AVAILABLE INPUT FILES:\n- input.csv"

    def get_tool_definitions(self):
        return [{"type": "function", "function": {"name": "read_file"}}]

    async def execute_tool(self, tool_name, tool_input):
        return {"success": True, "output": "ok", "generated_files": []}

    async def finalize_generated_files(self, generated_files, workspace_id=None):
        if self.artifacts is not None:
            return self.artifacts
        return [
            {
                "artifact_ref": self.artifact_ref,
                "filename": "report.pdf",
            }
        ]

    async def close(self):
        self.closed = True


class FakeLLM:
    default_model = "test-model"

    def __init__(self, output: str = "Report ready.") -> None:
        self.output = output
        self.messages = None

    async def stream_chat(self, messages, model=None, tool_definitions=None):
        self.messages = messages
        yield {"type": "delta", "content": self.output}
        yield {
            "type": "done",
            "content": self.output,
            "tool_calls": [],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "reasoning_tokens": 0,
                "total_tokens": 14,
            },
        }


class FailingLLM:
    default_model = "test-model"

    async def stream_chat(self, messages, model=None, tool_definitions=None):
        if False:
            yield None
        raise RuntimeError("provider unavailable")


class ToolCallingLLM:
    default_model = "test-model"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_chat(self, messages, model=None, tool_definitions=None):
        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "done",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_read_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"input.csv"}',
                        },
                    }
                ],
            }
            return
        yield {"type": "delta", "content": "Report ready."}
        yield {
            "type": "done",
            "content": "Report ready.",
            "tool_calls": [],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "reasoning_tokens": 0,
                "total_tokens": 14,
            },
        }


class RecordingRuntimeGatewayClient:
    def __init__(self) -> None:
        self.events = []

    async def record_event(self, gateway, event_type, payload, *, status):
        self.events.append((event_type, payload, status))


def make_request(run_id: str = "run_1", history: str = "Earlier question"):
    payload = valid_payload()
    payload["run_id"] = run_id
    payload["response_id"] = f"resp_{run_id}"
    payload["history"][0]["content"] = history
    payload["execution_context"].update(
        {
            "run_id": run_id,
            "input_path": f"/workspace/runs/{run_id}/inputs",
            "work_path": f"/workspace/runs/{run_id}/work",
            "output_path": f"/workspace/runs/{run_id}/outputs",
        }
    )
    payload["execution_files"][0]["sandbox_path"] = (
        f"/workspace/runs/{run_id}/inputs/input.csv"
    )
    payload["runtime_gateway"]["run_id"] = run_id
    return ReportExecutionRequest.model_validate(payload)


def make_service(llm, executor, runtime_gateway_client=None):
    return ReportExecutionService(
        llm_service=llm,
        input_preparer=FakeInputPreparer(),
        executor_factory=lambda request: executor,
        event_factory_builder=ReportEventFactory,
        max_iterations=3,
        runtime_gateway_client=runtime_gateway_client,
    )


async def collect(stream):
    return [event async for event in stream]


class ReportExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_delta_usage_and_completion(self):
        executor = FakeExecutor()
        events = await collect(make_service(FakeLLM(), executor).stream(make_request()))

        self.assertEqual(
            [event.type for event in events],
            [
                "report.status",
                "report.output_text.delta",
                "report.usage",
                "report.completed",
            ],
        )
        self.assertEqual(
            events[-1].payload["artifacts"],
            [{"artifact_ref": "artifact://report-1", "filename": "report.pdf"}],
        )
        self.assertTrue(executor.closed)

    async def test_streams_tool_lifecycle_while_preserving_gateway_recording(self):
        executor = FakeExecutor()
        gateway = RecordingRuntimeGatewayClient()

        events = await collect(
            make_service(
                ToolCallingLLM(),
                executor,
                runtime_gateway_client=gateway,
            ).stream(make_request())
        )

        tool_events = [event for event in events if event.type.startswith("report.tool.")]
        self.assertEqual(
            [event.type for event in tool_events],
            ["report.tool.started", "report.tool.completed"],
        )
        self.assertEqual(tool_events[0].payload["tool_call_id"], "call_read_1")
        self.assertEqual(tool_events[0].payload["tool_name"], "read_file")
        self.assertEqual(tool_events[0].payload["inputs"], {"path": "input.csv"})
        self.assertEqual(tool_events[0].payload["status"], "started")
        self.assertEqual(tool_events[1].payload["status"], "completed")
        self.assertEqual(tool_events[1].payload.get("inputs"), {"path": "input.csv"})
        self.assertEqual(
            tool_events[1].payload.get("outputs"),
            {"success": True, "output": "ok", "generated_files": []},
        )
        self.assertEqual(
            [event_type for event_type, _, _ in gateway.events],
            ["report.tool.started", "report.tool.completed"],
        )
        self.assertEqual(gateway.events[0][1].get("inputs"), {"path": "input.csv"})
        self.assertEqual(gateway.events[1][1].get("outputs", {}).get("output"), "ok")

    async def test_concurrent_runs_do_not_share_messages_todos_or_artifacts(self):
        first_llm = FakeLLM("first history-1")
        second_llm = FakeLLM("second history-2")
        first_executor = FakeExecutor("artifact://first")
        second_executor = FakeExecutor("artifact://second")

        first, second = await asyncio.gather(
            collect(
                make_service(first_llm, first_executor).stream(
                    make_request("run-1", "history-1")
                )
            ),
            collect(
                make_service(second_llm, second_executor).stream(
                    make_request("run-2", "history-2")
                )
            ),
        )

        first_json = json.dumps([event.model_dump(mode="json") for event in first])
        second_json = json.dumps([event.model_dump(mode="json") for event in second])
        self.assertNotIn("history-2", first_json)
        self.assertNotIn("artifact://second", first_json)
        self.assertNotIn("history-1", second_json)
        self.assertNotIn("artifact://first", second_json)
        self.assertIn("history-1", json.dumps(first_llm.messages))
        self.assertIn("history-2", json.dumps(second_llm.messages))

    async def test_executor_closes_after_model_failure(self):
        executor = FakeExecutor()
        events = await collect(
            make_service(FailingLLM(), executor).stream(make_request())
        )

        self.assertEqual(events[-1].type, "report.failed")
        self.assertEqual(events[-1].payload["phase"], "model")
        self.assertTrue(executor.closed)

    async def test_invalid_finalized_artifact_is_reported_as_artifact_failure(self):
        executor = FakeExecutor(artifacts=[{"filename": "report.pdf"}])

        events = await collect(
            make_service(FakeLLM(), executor).stream(make_request())
        )

        self.assertEqual(events[-1].type, "report.failed")
        self.assertEqual(events[-1].payload["code"], "artifact_finalization_failed")
        self.assertEqual(events[-1].payload["phase"], "artifact")
        self.assertTrue(executor.closed)


if __name__ == "__main__":
    unittest.main()
