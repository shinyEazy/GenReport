import importlib
import json
import sys
import unittest


class ToolDefinitionIsolationTests(unittest.TestCase):
    def test_axiom_executor_does_not_import_legacy_execution_services(self):
        for name in list(sys.modules):
            if name.startswith("app.services."):
                sys.modules.pop(name)

        importlib.import_module("app.services.axiom_tool_executor")

        forbidden = {
            "app.services.agent_service",
            "app.services.code_execution_service",
            "app.services.local_code_execution",
            "app.services.opensandbox_service",
            "app.services.sandbox_file_manager",
            "app.services.oss_service",
        }
        self.assertTrue(forbidden.isdisjoint(sys.modules))

    def test_tool_definitions_are_run_scoped_and_session_free(self):
        module = importlib.import_module("app.services.tool_definitions")

        definitions = module.get_axiom_tool_definitions(
            work_path="/workspace/runs/run-1/work",
            output_path="/workspace/runs/run-1/outputs",
        )

        serialized = json.dumps(definitions)
        self.assertNotIn("/tmp/workspace", serialized)
        self.assertNotIn("session_id", serialized)
        self.assertIn("/workspace/runs/run-1/outputs", serialized)


if __name__ == "__main__":
    unittest.main()
