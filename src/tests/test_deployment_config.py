from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "docker" / "docker-compose.yaml"


class DeploymentConfigTests(unittest.TestCase):
    def test_compose_has_no_service_token_or_state_mounts(self):
        compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        api = compose["services"]["api"]
        environment = api["environment"]

        self.assertNotIn("GEN_REPORT_API_TOKEN", environment)
        self.assertNotIn("DATABASE_URL", environment)
        self.assertNotIn("FILE_STORAGE_MODE", environment)
        self.assertNotIn("CODE_EXECUTION_MODE", environment)
        self.assertTrue(
            all("/data" not in str(volume) for volume in api.get("volumes", []))
        )
        self.assertEqual(set(compose.get("volumes", {})), {"uv-cache"})

    def test_environment_example_is_engine_only(self):
        env_example = (ROOT / "docker" / ".env.example").read_text(encoding="utf-8")

        self.assertNotIn("GEN_REPORT_API_TOKEN", env_example)
        for legacy in (
            "DATABASE_URL",
            "FILE_STORAGE_MODE",
            "FRONTEND_URL",
        ):
            self.assertNotIn(legacy, env_example)
        self.assertIn("LOCAL_MODE", env_example)
        self.assertIn("LOCAL_WORKSPACE_ROOT", env_example)

    def test_compose_installs_file_utility_for_local_report_tools(self):
        compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        command = " ".join(compose["services"]["api"]["command"])

        self.assertIn("apt-get install -y --no-install-recommends file", command)


if __name__ == "__main__":
    unittest.main()
