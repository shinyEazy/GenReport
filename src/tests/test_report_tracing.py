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
            ["genreport", "report-workflow", "remote"],
        )
        self.assertEqual(
            LOCAL_WORKFLOW_TAGS,
            ["genreport", "report-workflow", "local"],
        )

    def test_returns_original_function_when_tracing_is_disabled(self) -> None:
        def operation(value):
            return value

        with patch.dict(os.environ, {"LANGCHAIN_TRACING_V2": "false"}, clear=True):
            traced = trace_operation(operation, name="report-stage")

        self.assertIs(traced, operation)

    @patch("langsmith.traceable")
    def test_configures_named_operation_with_full_payload_processors(
        self, traceable
    ) -> None:
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

    @patch("langsmith.traceable")
    def test_reduces_llm_stream_to_final_answer(self, traceable) -> None:
        def operation():
            yield {"type": "delta", "content": "Final "}
            yield {"type": "delta", "content": "answer"}
            yield {
                "type": "done",
                "content": "Final answer",
                "tool_calls": [],
            }

        with patch.dict(os.environ, {"LANGCHAIN_TRACING_V2": "true"}):
            trace_operation(operation, name="model", run_type="llm")

        kwargs = traceable.call_args.kwargs
        self.assertIn("reduce_fn", kwargs)
        self.assertEqual(
            kwargs["reduce_fn"](
                [
                    {"type": "delta", "content": "Final "},
                    {"type": "delta", "content": "answer"},
                    {
                        "type": "done",
                        "content": "Final answer",
                        "tool_calls": [],
                    },
                ]
            ),
            "Final answer",
        )

    @patch("langsmith.traceable")
    def test_llm_trace_exposes_bound_tools(self, traceable) -> None:
        def operation():
            return None

        with patch.dict(os.environ, {"LANGCHAIN_TRACING_V2": "true"}):
            trace_operation(operation, name="model", run_type="llm")

        process_inputs = traceable.call_args.kwargs["process_inputs"]
        tool_definitions = [
            {
                "type": "function",
                "function": {
                    "name": "execute_python",
                    "description": "Run Python.",
                    "parameters": {"type": "object"},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file.",
                    "parameters": {"type": "object"},
                },
            },
        ]

        logged_inputs = process_inputs(
            {
                "messages": [{"role": "user", "content": "Create a report."}],
                "model": "test-model",
                "tool_definitions": tool_definitions,
            }
        )

        self.assertIn("tools", logged_inputs)
        self.assertEqual(logged_inputs["tools"], tool_definitions)
        self.assertEqual(
            logged_inputs["bound_tools"],
            ["execute_python", "read_file"],
        )
        self.assertNotIn("tool_definitions", logged_inputs)


if __name__ == "__main__":
    unittest.main()
