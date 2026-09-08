from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from app.local_report import main
from app.services.local_report_runner import LocalReportResult
from app.services.local_workspace import LocalWorkspace


class FakeRunner:
    async def run(self, config):
        workspace = LocalWorkspace.create(
            Path(self.settings.LOCAL_WORKSPACE_ROOT), "run_1", config.files
        )
        artifact = workspace.outputs_dir / "report.txt"
        artifact.write_text("done", encoding="utf-8")
        return LocalReportResult(
            workspace=workspace,
            output_text="Report ready.",
            artifacts=[{"filename": "report.txt", "artifact_ref": str(artifact)}],
        )


class LocalReportCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.csv"
        self.source.write_text("value\n42\n", encoding="utf-8")
        self.config = self.root / "config.yaml"
        self.config.write_text(
            """query: Report
files: [source.csv]
openai_api_key: config-key
openai_base_url: https://provider.example/v1
""",
            encoding="utf-8",
        )
        self.settings = SimpleNamespace(
            LOCAL_MODE=True,
            LOCAL_WORKSPACE_ROOT=self.root / "workspaces",
            LOCAL_EXECUTION_TIMEOUT_SECONDS=10,
            MAX_AGENT_ITERATIONS=3,
            DEFAULT_MODEL="env-model",
            OPENAI_API_KEY="",
            OPENAI_BASE_URL="https://provider.example/v1",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_prints_workspace_and_artifacts_on_success(self) -> None:
        stdout = StringIO()

        def runner_factory(**kwargs):
            runner = FakeRunner()
            runner.settings = kwargs["settings"]
            return runner

        received_llm_options = {}

        def llm_factory(**kwargs):
            received_llm_options.update(kwargs)
            return object()

        with redirect_stdout(stdout):
            exit_code = main(
                ["--config", str(self.config)],
                runner_factory=runner_factory,
                settings_value=self.settings,
                llm_factory=llm_factory,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Workspace:", stdout.getvalue())
        self.assertIn("report.txt", stdout.getvalue())
        self.assertEqual(
            received_llm_options,
            {
                "api_key": "config-key",
                "base_url": "https://provider.example/v1",
            },
        )

    def test_returns_usage_error_when_local_mode_is_disabled(self) -> None:
        self.settings.LOCAL_MODE = False
        stderr = StringIO()

        with redirect_stderr(stderr):
            exit_code = main(
                ["--config", str(self.config)],
                settings_value=self.settings,
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("LOCAL_MODE=true", stderr.getvalue())

    def test_folder_mode_builds_sorted_non_recursive_config(self) -> None:
        folder = self.root / "inputs"
        folder.mkdir()
        (folder / "z-last.csv").write_text("z\n", encoding="utf-8")
        (folder / "a-first.csv").write_text("a\n", encoding="utf-8")
        nested = folder / "nested"
        nested.mkdir()
        (nested / "ignored.csv").write_text("ignored\n", encoding="utf-8")

        received = {}
        test_case = self

        def runner_factory(**kwargs):
            received["settings"] = kwargs["settings"]
            received["llm_service"] = kwargs["llm_service"]

            class CapturingRunner:
                async def run(self, config):
                    received["config"] = config
                    return LocalReportResult(
                        workspace=SimpleNamespace(run_root=test_case.root),
                        output_text="Report ready.",
                        artifacts=[],
                    )

            return CapturingRunner()

        with redirect_stdout(StringIO()):
            exit_code = main(
                [
                    "--folder-path",
                    str(folder),
                    "--query",
                    "Analyze these files",
                ],
                runner_factory=runner_factory,
                settings_value=self.settings,
                llm_factory=lambda **kwargs: kwargs,
            )

        self.assertEqual(exit_code, 0)
        config = received["config"]
        self.assertEqual(config.query, "Analyze these files")
        self.assertEqual(
            [path.name for path in config.files],
            ["a-first.csv", "z-last.csv"],
        )
        self.assertEqual(config.model, "env-model")
        self.assertEqual(config.openai_api_key, "")
        self.assertEqual(config.openai_base_url, "https://provider.example/v1")
        self.assertEqual(config.language, "auto")
        self.assertIsNone(config.run_id)


if __name__ == "__main__":
    unittest.main()
