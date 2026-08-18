import json
import unittest

from app.contracts.report_execution import ReportExecutionRequest
from app.services.report_prompt import build_report_messages
from tests.test_report_contract import valid_payload


class ReportPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = ReportExecutionRequest.model_validate(valid_payload())

    def test_uses_supplied_history_without_database_lookup(self):
        messages = build_report_messages(
            self.request,
            available_files="AVAILABLE INPUT FILES:\n- input.csv",
        )

        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user", "assistant", "user"],
        )
        self.assertEqual(messages[1]["content"], "Earlier question")
        self.assertEqual(
            messages[2]["content"],
            "Earlier answer\nArtifacts: artifact://report-0",
        )
        self.assertTrue(messages[-1]["content"].endswith(self.request.instruction))

    def test_mentions_only_supplied_run_paths(self):
        serialized = json.dumps(
            build_report_messages(self.request, available_files="No inputs")
        )

        self.assertIn(self.request.execution_context.output_path, serialized)
        self.assertNotIn("/tmp/workspace", serialized)
        self.assertNotIn("conversation", serialized.lower())


if __name__ == "__main__":
    unittest.main()
