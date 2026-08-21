import unittest

from app.contracts.report_execution import ExecutionFileRequest
from app.services.axiom_tool_executor import AxiomToolExecutor


class FakeExecutionClient:
    def __init__(self, artifacts=None, files=None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.artifacts = artifacts or []
        self.files = files or {}

    async def execute(self, **kwargs):
        self.calls.append(("execute", kwargs))
        return {"success": True, "stdout": "", "stderr": "", "exit_code": 0}

    async def write_file(self, path, content):
        self.calls.append(("write_file", {"path": path, "content": content}))
        self.files[path] = content if isinstance(content, bytes) else content.encode()
        return len(content)

    async def read_file(self, path):
        self.calls.append(("read_file", {"path": path}))
        return self.files[path]

    async def close(self):
        return None

    async def finalize(self, entries, *, workspace_id=None):
        self.calls.append(
            ("finalize", {"entries": entries, "workspace_id": workspace_id})
        )
        return self.artifacts


class AxiomToolExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_file_does_not_return_raw_binary_image_bytes(self):
        image_path = "/workspace/runs/resp_1/inputs/chart.png"
        client = FakeExecutionClient(files={image_path: b"\x89PNG\r\n\x1a\n\x00raw"})
        executor = AxiomToolExecutor(
            client=client,
            files=[
                ExecutionFileRequest(
                    artifact_id="image-1",
                    filename="chart.png",
                    sandbox_path=image_path,
                    content_type="image/png",
                    size=13,
                )
            ],
            input_path="/workspace/runs/resp_1/inputs",
            work_path="/workspace/runs/resp_1/work",
            output_path="/workspace/runs/resp_1/outputs",
        )

        result = await executor.execute_tool("read_file", {"path": image_path})

        self.assertTrue(result["success"])
        self.assertIn("attached to the report prompt", result["output"])
        self.assertNotIn("content", result)
        self.assertNotIn("\x00", result["output"])

    async def test_materialize_assets_creates_skill_directories_before_writing(self):
        client = FakeExecutionClient()
        executor = AxiomToolExecutor(
            client=client,
            files=[],
            input_path="/workspace/runs/resp_1/inputs",
            work_path="/workspace/runs/resp_1/work",
            output_path="/workspace/runs/resp_1/outputs",
        )

        await executor.materialize_assets()

        self.assertGreater(len(client.calls), 1)
        operation, payload = client.calls[0]
        self.assertEqual(operation, "execute")
        self.assertIn(".skills/res", payload["code"])
        self.assertEqual(payload["cwd"], "/workspace/runs/resp_1/work")
        self.assertEqual(client.calls[1][0], "write_file")

    async def test_finalize_normalizes_axiom_artifact_id_to_report_artifact_ref(self):
        client = FakeExecutionClient(
            artifacts=[
                {
                    "asset_id": "asset-1",
                    "artifact_id": "asset-1",
                    "filename": "report.pdf",
                    "content_type": "application/pdf",
                    "size": 123,
                    "url": "http://axiom/assets/asset-1",
                }
            ]
        )
        executor = AxiomToolExecutor(
            client=client,
            files=[],
            input_path="/workspace/runs/resp_1/inputs",
            work_path="/workspace/runs/resp_1/work",
            output_path="/workspace/runs/resp_1/outputs",
        )

        artifacts = await executor.finalize_generated_files(
            [{"sandbox_path": "/workspace/runs/resp_1/outputs/report.pdf"}],
            workspace_id="workspace-1",
        )

        self.assertEqual(artifacts[0]["artifact_ref"], "asset-1")
        self.assertEqual(artifacts[0]["artifact_id"], "asset-1")
        self.assertEqual(artifacts[0]["url"], "http://axiom/assets/asset-1")

    async def test_finalize_inlines_relative_html_images(self):
        report_path = "/workspace/runs/resp_1/outputs/report.html"
        image_path = "/workspace/runs/resp_1/outputs/chart.png"
        client = FakeExecutionClient(
            artifacts=[],
            files={
                report_path: b'<html><img src="chart.png" alt="Chart"></html>',
                image_path: b"png-bytes",
            },
        )
        executor = AxiomToolExecutor(
            client=client,
            files=[],
            input_path="/workspace/runs/resp_1/inputs",
            work_path="/workspace/runs/resp_1/work",
            output_path="/workspace/runs/resp_1/outputs",
        )

        await executor.finalize_generated_files(
            [
                {"sandbox_path": report_path},
                {"sandbox_path": image_path},
            ],
            workspace_id="workspace-1",
        )

        self.assertIn(
            b'src="data:image/png;base64,cG5nLWJ5dGVz"',
            client.files[report_path],
        )


if __name__ == "__main__":
    unittest.main()
