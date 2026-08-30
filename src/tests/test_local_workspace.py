from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.local_workspace import LocalWorkspace


class LocalWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "workspaces"
        self.source = Path(self.temp_dir.name) / "source.csv"
        self.source.write_text("a,b\n1,2\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_creates_layout_copies_inputs_and_maps_virtual_paths(self) -> None:
        workspace = LocalWorkspace.create(self.root, "run_1", [self.source])

        self.assertEqual(workspace.inputs_dir, self.root / "run_1" / "inputs")
        self.assertTrue(workspace.work_dir.is_dir())
        self.assertTrue(workspace.outputs_dir.is_dir())
        self.assertEqual(
            workspace.virtual_input_path("source.csv"),
            "/workspace/runs/run_1/inputs/source.csv",
        )
        self.assertEqual(
            workspace.resolve_virtual_path("/workspace/runs/run_1/inputs/source.csv"),
            workspace.inputs_dir / "source.csv",
        )
        self.assertEqual(
            (workspace.inputs_dir / "source.csv").read_text(encoding="utf-8"),
            "a,b\n1,2\n",
        )

    def test_rejects_existing_run_and_paths_outside_the_run(self) -> None:
        workspace = LocalWorkspace.create(self.root, "run_1", [self.source])
        output = workspace.outputs_dir / "report.txt"
        output.write_text("first", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            LocalWorkspace.create(self.root, "run_1", [self.source])
        self.assertEqual(output.read_text(encoding="utf-8"), "first")
        with self.assertRaises(ValueError):
            workspace.resolve_virtual_path("/workspace/runs/run_2/outputs/report.txt")
        with self.assertRaises(ValueError):
            workspace.resolve_virtual_path(
                "/workspace/runs/run_1/outputs/../inputs/a.csv"
            )

    def test_lists_only_output_files_as_artifacts(self) -> None:
        workspace = LocalWorkspace.create(self.root, "run_1", [self.source])
        (workspace.outputs_dir / "report.txt").write_text("done", encoding="utf-8")
        nested = workspace.outputs_dir / "charts"
        nested.mkdir()
        (nested / "chart.png").write_bytes(b"png")
        (workspace.work_dir / "scratch.txt").write_text("ignore", encoding="utf-8")

        artifacts = workspace.artifact_entries()

        self.assertEqual(
            [item["filename"] for item in artifacts], ["chart.png", "report.txt"]
        )
        self.assertEqual(
            artifacts[0]["path"],
            "/workspace/runs/run_1/outputs/charts/chart.png",
        )
        self.assertEqual(
            artifacts[1]["artifact_ref"],
            str((workspace.outputs_dir / "report.txt").resolve()),
        )


if __name__ == "__main__":
    unittest.main()
