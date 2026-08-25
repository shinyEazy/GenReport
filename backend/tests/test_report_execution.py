import asyncio
import inspect
import json
import unittest

from app.contracts.report_execution import (
    ExecutionFileRequest,
    ReportExecutionRequest,
    SelectedReportInput,
)
from app.services.axiom_tool_executor import AxiomToolExecutor
from app.services.report_events import ReportEventFactory
from app.services.report_execution import ReportExecutionService
from app.services.report_input_preparation import PreparedReportInputs
from tests.test_report_contract import valid_payload


class FakeInputPreparer:
    async def prepare(self, **kwargs):
        primary_source_id = kwargs.get("primary_source_id")
        files = kwargs["existing_files"]
        return PreparedReportInputs(
            files=files,
            selected_inputs=[
                SelectedReportInput(
                    source_id=item.source_id or item.artifact_id,
                    document_id=item.document_id,
                    object_key=item.source_object_key or item.artifact_id,
                    filename=item.filename,
                    content_type=item.content_type,
                    role=(
                        "primary"
                        if item.source_id == primary_source_id
                        else "related"
                    ),
                )
                for item in files
            ],
        )


class FakeExecutor:
    def __init__(
        self,
        artifact_ref: str = "artifact://report-1",
        artifacts: list[dict] | None = None,
        image_parts: list[dict] | None = None,
    ) -> None:
        self.artifact_ref = artifact_ref
        self.artifacts = artifacts
        self.image_parts = image_parts or []
        self.image_part_calls = 0
        self.closed = False
        self.todos = []

    async def materialize_assets(self):
        return None

    def get_available_files_prompt(self):
        return "AVAILABLE INPUT FILES:\n- input.csv"

    def get_tool_definitions(self):
        return [{"type": "function", "function": {"name": "read_file"}}]

    async def get_multimodal_image_parts(self):
        self.image_part_calls += 1
        return self.image_parts

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


class ImageExecutionClient:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.read_paths: list[str] = []

    async def read_file(self, path: str) -> bytes:
        self.read_paths.append(path)
        return self.files[path]


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


def make_service(
    llm,
    executor,
    runtime_gateway_client=None,
    multimodal_models: list[str] | None = None,
    trace_operation=None,
):
    tracing_kwargs = (
        {"trace_operation": trace_operation} if trace_operation is not None else {}
    )
    return ReportExecutionService(
        llm_service=llm,
        input_preparer=FakeInputPreparer(),
        executor_factory=lambda request: executor,
        event_factory_builder=ReportEventFactory,
        max_iterations=3,
        runtime_gateway_client=runtime_gateway_client,
        multimodal_models=multimodal_models or [],
        **tracing_kwargs,
    )


async def collect(stream):
    return [event async for event in stream]


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


class ReportExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_workflow_traces_only_model_and_tool_rounds(self):
        trace_calls = []
        events = await collect(
            make_service(
                ToolCallingLLM(),
                FakeExecutor(),
                runtime_gateway_client=RecordingRuntimeGatewayClient(),
                trace_operation=recording_trace_operation(trace_calls),
            ).stream(make_request())
        )

        self.assertEqual(events[-1].type, "report.completed")
        self.assertEqual(
            [call[0] for call in trace_calls],
            [
                "genreport-report-workflow",
                "model",
                "tools",
                "model",
            ],
        )

    async def test_streams_delta_usage_and_completion(self):
        executor = FakeExecutor()
        events = await collect(make_service(FakeLLM(), executor).stream(make_request()))

        self.assertEqual(
            [event.type for event in events],
            [
                "report.status",
                "report.inputs.selected",
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

    async def test_multimodal_model_attaches_declared_image_parts(self):
        image_part = {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64,aW1hZ2U=",
                "detail": "high",
            },
        }
        llm = FakeLLM()
        executor = FakeExecutor(image_parts=[image_part])
        request = make_request().model_copy(update={"model": "qwen/qwen3.7-flash"})

        await collect(
            make_service(
                llm,
                executor,
                multimodal_models=["qwen/qwen3.7-flash"],
            ).stream(request)
        )

        self.assertEqual(executor.image_part_calls, 1)
        self.assertEqual(llm.messages[-1]["content"][0]["type"], "text")
        self.assertEqual(llm.messages[-1]["content"][1], image_part)

    async def test_text_only_model_does_not_read_declared_image_parts(self):
        llm = FakeLLM()
        executor = FakeExecutor(
            image_parts=[
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,aW1hZ2U=",
                        "detail": "high",
                    },
                }
            ]
        )
        request = make_request().model_copy(update={"model": "text-only-model"})

        await collect(
            make_service(
                llm,
                executor,
                multimodal_models=["qwen/qwen3.7-flash"],
            ).stream(request)
        )

        self.assertEqual(executor.image_part_calls, 0)
        self.assertIsInstance(llm.messages[-1]["content"], str)

    async def test_executor_encodes_only_bounded_declared_images(self):
        image_path = "/workspace/runs/run_1/inputs/chart.png"
        client = ImageExecutionClient({image_path: b"image"})
        executor = AxiomToolExecutor(
            client=client,
            files=[
                ExecutionFileRequest(
                    artifact_id="image",
                    filename="chart.png",
                    sandbox_path=image_path,
                    content_type="image/png",
                    size=5,
                ),
                ExecutionFileRequest(
                    artifact_id="text",
                    filename="notes.txt",
                    sandbox_path="/workspace/runs/run_1/inputs/notes.txt",
                    content_type="text/plain",
                    size=5,
                ),
                ExecutionFileRequest(
                    artifact_id="large",
                    filename="large.png",
                    sandbox_path="/workspace/runs/run_1/inputs/large.png",
                    content_type="image/png",
                    size=6,
                ),
                ExecutionFileRequest(
                    artifact_id="missing",
                    filename="missing.png",
                    sandbox_path="/workspace/runs/run_1/inputs/missing.png",
                    content_type="image/png",
                    size=5,
                ),
            ],
            input_path="/workspace/runs/run_1/inputs",
            work_path="/workspace/runs/run_1/work",
            output_path="/workspace/runs/run_1/outputs",
            multimodal_image_detail="low",
            multimodal_image_max_bytes=5,
        )

        parts = await executor.get_multimodal_image_parts()

        self.assertEqual(
            client.read_paths,
            [image_path, "/workspace/runs/run_1/inputs/missing.png"],
        )
        self.assertEqual(
            parts,
            [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,aW1hZ2U=",
                        "detail": "low",
                    },
                }
            ],
        )

    async def test_emits_selected_primary_input_before_report_completion(self):
        payload = valid_payload()
        payload["primary_source_id"] = "source-primary"
        payload["execution_files"][0].update(
            {
                "source_id": "source-primary",
                "document_id": "document-primary",
                "source_object_key": "organizations/org-1/sources/latest.csv",
            }
        )
        request = ReportExecutionRequest.model_validate(payload)

        events = await collect(
            make_service(FakeLLM(), FakeExecutor()).stream(request)
        )

        selected = next(event for event in events if event.type == "report.inputs.selected")
        self.assertEqual(selected.payload["inputs"][0]["role"], "primary")
        self.assertEqual([event.type for event in events][-2:], ["report.usage", "report.completed"])

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
