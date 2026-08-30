import os
from pathlib import Path
import unittest
from unittest.mock import patch

from app.core.config import BACKEND_DIR, Settings, configure_langsmith_environment


class LangSmithSettingsTests(unittest.TestCase):
    def test_settings_have_no_genreport_service_token(self) -> None:
        self.assertNotIn("GEN_REPORT_API_TOKEN", Settings.model_fields)

    def test_accepts_langchain_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LANGCHAIN_API_KEY": "test-langsmith-key",
            },
            clear=True,
        ):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.LANGCHAIN_API_KEY, "test-langsmith-key")

    def test_accepts_langchain_environment_names(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_API_KEY": "test-langsmith-key",
                "LANGCHAIN_PROJECT": "data-intelligence",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
            },
            clear=True,
        ):
            settings = Settings(_env_file=None)

        self.assertTrue(settings.LANGCHAIN_TRACING_V2)
        self.assertEqual(settings.LANGCHAIN_API_KEY, "test-langsmith-key")
        self.assertEqual(settings.LANGCHAIN_PROJECT, "data-intelligence")
        self.assertEqual(settings.LANGCHAIN_ENDPOINT, "https://api.smith.langchain.com")

    def test_exports_canonical_environment_for_langsmith_sdk(self) -> None:
        settings = Settings(
            _env_file=None,
            LANGCHAIN_TRACING_V2="true",
            LANGCHAIN_API_KEY="test-langsmith-key",
            LANGCHAIN_PROJECT="gen-report",
            LANGCHAIN_ENDPOINT="https://api.smith.langchain.com",
        )

        with patch.dict(os.environ, {}, clear=True):
            configure_langsmith_environment(settings)

            self.assertEqual(os.environ["LANGCHAIN_TRACING_V2"], "true")
            self.assertEqual(os.environ["LANGCHAIN_API_KEY"], "test-langsmith-key")
            self.assertEqual(os.environ["LANGCHAIN_PROJECT"], "gen-report")
            self.assertEqual(
                os.environ["LANGCHAIN_ENDPOINT"], "https://api.smith.langchain.com"
            )

    def test_local_mode_defaults_to_disabled(self) -> None:
        settings = Settings(_env_file=None)

        self.assertFalse(settings.LOCAL_MODE)
        self.assertEqual(
            settings.LOCAL_WORKSPACE_ROOT,
            BACKEND_DIR / "data" / "workspaces",
        )

    def test_local_mode_accepts_workspace_configuration(self) -> None:
        settings = Settings(
            _env_file=None,
            LOCAL_MODE="true",
            LOCAL_WORKSPACE_ROOT="/tmp/gen-report-workspaces",
        )

        self.assertTrue(settings.LOCAL_MODE)
        self.assertEqual(
            settings.LOCAL_WORKSPACE_ROOT,
            Path("/tmp/gen-report-workspaces"),
        )


if __name__ == "__main__":
    unittest.main()
