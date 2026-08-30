from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.contracts.local_report import LocalReportConfigError, load_local_report_config


class LocalReportConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source_file = self.root / "source.csv"
        self.source_file.write_text("a,b\n1,2\n", encoding="utf-8")
        self.config_path = self.root / "config.yaml"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_loads_relative_files_and_optional_values(self) -> None:
        self.config_path.write_text(
            """query: Create a monthly report
files:
  - source.csv
model: test-model
openai_api_key: config-key
openai_base_url: https://provider.example/v1
language: en
run_id: monthly-2026-08
""",
            encoding="utf-8",
        )

        config = load_local_report_config(self.config_path)

        self.assertEqual(config.query, "Create a monthly report")
        self.assertEqual(config.files, [self.source_file.resolve()])
        self.assertEqual(config.model, "test-model")
        self.assertEqual(config.openai_api_key, "config-key")
        self.assertEqual(config.openai_base_url, "https://provider.example/v1")
        self.assertEqual(config.language, "en")
        self.assertEqual(config.run_id, "monthly-2026-08")

    def test_rejects_invalid_configuration(self) -> None:
        cases = {
            "empty-query.yaml": "query: ''\nfiles: [source.csv]\n",
            "empty-files.yaml": "query: Report\nfiles: []\n",
            "missing-file.yaml": "query: Report\nfiles: [missing.csv]\n",
            "directory.yaml": "query: Report\nfiles: ['.']\n",
            "duplicate.yaml": "query: Report\nfiles: [source.csv, nested/source.csv]\n",
            "invalid-run-id.yaml": "query: Report\nfiles: [source.csv]\nrun_id: ../escape\n",
            "root-list.yaml": "- query\n- files\n",
            "unknown-key.yaml": "query: Report\nfiles: [source.csv]\nextra: no\n",
        }
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "source.csv").write_text("x\n", encoding="utf-8")

        for filename, content in cases.items():
            with self.subTest(filename=filename):
                path = self.root / filename
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(LocalReportConfigError):
                    load_local_report_config(path)


if __name__ == "__main__":
    unittest.main()
