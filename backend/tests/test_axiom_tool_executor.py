import unittest

from app.services.axiom_tool_executor import AxiomToolExecutor


class FakeExecutionClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def execute(self, **kwargs):
        self.calls.append(("execute", kwargs))
        return {"success": True, "stdout": "", "stderr": "", "exit_code": 0}

    async def write_file(self, path, content):
        self.calls.append(("write_file", {"path": path, "content": content}))
        return len(content)

    async def close(self):
        return None


class AxiomToolExecutorTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
