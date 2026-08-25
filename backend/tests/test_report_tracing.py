from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.report_tracing import (
    LOCAL_WORKFLOW_TAGS,
    REMOTE_WORKFLOW_TAGS,
    trace_operation,
)


class ReportTracingTests(unittest.TestCase):
    def test_workflow_tags_identify_remote_and_local_execution(self) -> None:
        self.assertEqual(
            REMOTE_WORKFLOW_TAGS,
            ("genreport", "report-workflow", "remote"),
        )
        self.assertEqual(
            LOCAL_WORKFLOW_TAGS,
            ("genreport", "report-workflow", "local"),
        )

    def test_returns_original_function_when_tracing_is_disabled(self) -> None:
        def operation(value):
            return value

        with patch.dict(os.environ, {"LANGCHAIN_TRACING_V2": "false"}, clear=True):
            traced = trace_operation(operation, name="report-stage")

        self.assertIs(traced, operation)

    @patch("langsmith.traceable")
    def test_configures_named_operation_with_full_payload_processors(self, traceable) -> None:
        def operation(value):
            return value

        decorated = object()
        traceable.return_value.return_value = decorated
        with patch.dict(os.environ, {"LANGCHAIN_TRACING_V2": "true"}):
            traced = trace_operation(
                operation,
                name="report-stage",
                run_type="tool",
                tags=["genreport", "remote", "tool"],
            )

        self.assertIs(traced, decorated)
        kwargs = traceable.call_args.kwargs
        self.assertEqual(kwargs["name"], "report-stage")
        self.assertEqual(kwargs["run_type"], "tool")
        self.assertEqual(
            kwargs["project_name"], os.getenv("LANGCHAIN_PROJECT") or "gen-report"
        )
        self.assertEqual(kwargs["tags"], ["genreport", "remote", "tool"])
        self.assertIn("process_inputs", kwargs)
        self.assertIn("process_outputs", kwargs)


if __name__ == "__main__":
    unittest.main()
