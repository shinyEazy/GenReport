from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.local_execution_client import LocalExecutionClient
from app.services.local_workspace import LocalWorkspace


class LocalExecutionClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        source = root / "source.csv"
        source.write_text("value\n42\n", encoding="utf-8")
        self.workspace = LocalWorkspace.create(root / "workspaces", "run_1", [source])
        self.client = LocalExecutionClient(self.workspace, timeout_seconds=1)

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.temp_dir.cleanup()

    async def test_reads_writes_and_executes_inside_the_workspace(self) -> None:
        output_path = f"{self.workspace.virtual_outputs_path}/note.txt"
        await self.client.write_file(output_path, "hello")

        self.assertEqual(await self.client.read_file(output_path), b"hello")
        result = await self.client.execute(
            language="python",
            code="from pathlib import Path; Path('report.txt').write_text('done')",
            cwd=self.workspace.virtual_outputs_path,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(
            (self.workspace.outputs_dir / "report.txt").read_text(encoding="utf-8"),
            "done",
        )

    async def test_translates_virtual_paths_and_lists_files(self) -> None:
        result = await self.client.execute(
            language="python",
            code=(
                "from pathlib import Path\n"
                f"print(Path('{self.workspace.virtual_input_path('source.csv')}').read_text())"
            ),
            cwd=self.workspace.virtual_outputs_path,
        )
        (self.workspace.outputs_dir / "nested").mkdir()
        (self.workspace.outputs_dir / "nested" / "chart.png").write_bytes(b"png")

        entries = await self.client.list_files(self.workspace.virtual_outputs_path)

        self.assertTrue(result["success"])
        self.assertIn("42", result["stdout"])
        self.assertEqual(
            entries,
            [
                {
                    "path": f"{self.workspace.virtual_outputs_path}/nested",
                    "kind": "directory",
                    "size_bytes": 0,
                }
            ],
        )

    async def test_returns_artifacts_and_rejects_invalid_paths(self) -> None:
        (self.workspace.outputs_dir / "report.txt").write_text("done", encoding="utf-8")

        artifacts = await self.client.finalize([])

        self.assertEqual(artifacts[0]["filename"], "report.txt")
        with self.assertRaises(ValueError):
            await self.client.read_file("/workspace/runs/run_2/outputs/report.txt")
        with self.assertRaises(ValueError):
            await self.client.write_file(
                "/workspace/runs/run_1/outputs/../inputs/nope.txt", "nope"
            )

    async def test_lists_directory_contents_when_model_reads_a_directory(self) -> None:
        skills_dir = self.workspace.work_dir / ".skills"
        skills_dir.mkdir()
        (skills_dir / "latex_skill.md").write_text("latex", encoding="utf-8")

        content = await self.client.read_file(
            f"{self.workspace.virtual_work_path}/.skills"
        )

        self.assertEqual(content, b"Directory contents:\n- latex_skill.md\n")

    async def test_reports_command_timeout(self) -> None:
        client = LocalExecutionClient(self.workspace, timeout_seconds=0.01)

        result = await client.execute(
            language="python",
            code="import time; time.sleep(1)",
            cwd=self.workspace.virtual_outputs_path,
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("timed out", result["stderr"])


if __name__ == "__main__":
    unittest.main()
