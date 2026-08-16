import os
import unittest
from unittest.mock import patch

from langchain_core.messages import ToolMessage

from app.services.report_file_discovery import (
    DiscoveryAgent,
    ReportArtifactSelection,
    _recover_tool_errors,
    _trace_discovery_call,
)


class FakeCompiledAgent:
    def __init__(self, response) -> None:
        self.response = response
        self.calls = []

    async def ainvoke(self, payload, config=None):
        self.calls.append((payload, config))
        return self.response


class FakeMethodHub:
    def __init__(self, tools=None) -> None:
        self.tools = tools if tools is not None else [object()]
        self.calls = []

    async def create_langchain_tools(self, **kwargs):
        self.calls.append(kwargs)
        return self.tools


class DiscoveryAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_helper_is_noop_when_langsmith_is_disabled(self) -> None:
        async def operation(**kwargs):
            return ["doc-1"]

        with patch.dict(os.environ, {"LANGCHAIN_TRACING_V2": "false"}):
            traced = _trace_discovery_call(operation)

        self.assertIs(traced, operation)

    @patch("langsmith.traceable")
    async def test_trace_helper_configures_named_chain_run(self, traceable) -> None:
        async def operation(**kwargs):
            return ["doc-1"]

        decorated = object()
        traceable.return_value.return_value = decorated
        with patch.dict(os.environ, {"LANGCHAIN_TRACING_V2": "true"}):
            traced = _trace_discovery_call(operation)

        self.assertIs(traced, decorated)
        traceable.assert_called_once_with(
            name="genreport-file-discovery",
            run_type="chain",
            project_name=os.getenv("LANGCHAIN_PROJECT") or "gen-report",
            tags=["genreport", "file-discovery"],
        )

    async def test_discovery_runs_through_injected_trace_wrapper(self) -> None:
        compiled = FakeCompiledAgent(
            {
                "structured_response": ReportArtifactSelection(
                    document_ids=["doc-1"]
                )
            }
        )
        traced_calls = []

        def trace_factory(function):
            async def traced(**kwargs):
                traced_calls.append(kwargs)
                return await function(**kwargs)

            return traced

        agent = DiscoveryAgent(
            method_hub=FakeMethodHub(),
            model_factory=lambda model: object(),
            agent_factory=lambda **kwargs: compiled,
            profile_registrar=lambda model: None,
            trace_factory=trace_factory,
        )

        selected = await agent.discover(
            query="Create a UET report",
            organization_id="test-org",
            workspace_id="default",
            model="test-model",
        )

        self.assertEqual(selected, ["doc-1"])
        self.assertEqual(
            traced_calls,
            [
                {
                    "query": "Create a UET report",
                    "organization_id": "test-org",
                    "workspace_id": "default",
                    "model": "test-model",
                }
            ],
        )

    async def test_returns_deduplicated_structured_document_ids(self) -> None:
        method_hub = FakeMethodHub()
        compiled = FakeCompiledAgent(
            {
                "structured_response": ReportArtifactSelection(
                    document_ids=["doc-1", "doc-1", " doc-2 ", ""]
                )
            }
        )
        captured = {}

        def agent_factory(**kwargs):
            captured.update(kwargs)
            return compiled

        agent = DiscoveryAgent(
            method_hub=method_hub,
            model_factory=lambda model: f"model:{model}",
            agent_factory=agent_factory,
            profile_registrar=lambda model: None,
            trace_factory=lambda function: function,
            max_artifacts=20,
            max_rounds=8,
        )

        selected = await agent.discover(
            query="Create a UET admissions report",
            organization_id="test-org",
            workspace_id="workspace-b",
            model="test-model",
        )

        self.assertEqual(selected, ["doc-1", "doc-2"])
        self.assertEqual(captured["model"], "model:test-model")
        self.assertEqual(captured["response_format"], ReportArtifactSelection)
        self.assertEqual(captured["subagents"], [])
        self.assertIn("BM25", captured["system_prompt"])
        self.assertEqual(
            method_hub.calls,
            [
                {
                    "allowed_names": {
                        "corpus_retrieve_context",
                        "corpus_vector_search",
                        "corpus_bm25_search",
                        "corpus_get_file_ingested_data",
                    },
                    "organization_id": "test-org",
                    "workspace_id": "workspace-b",
                }
            ],
        )
        self.assertEqual(compiled.calls[0][1], {"recursion_limit": 17})

    async def test_accepts_dict_structured_response_and_enforces_limit(self) -> None:
        compiled = FakeCompiledAgent(
            {
                "structured_response": {
                    "document_ids": ["doc-1", "doc-2", "doc-3"]
                }
            }
        )
        agent = DiscoveryAgent(
            method_hub=FakeMethodHub(),
            model_factory=lambda model: object(),
            agent_factory=lambda **kwargs: compiled,
            profile_registrar=lambda model: None,
            trace_factory=lambda function: function,
            max_artifacts=2,
        )

        selected = await agent.discover(
            query="Create a report",
            organization_id="test-org",
            workspace_id="default",
        )

        self.assertEqual(selected, ["doc-1", "doc-2"])

    async def test_requires_at_least_one_retrieval_tool(self) -> None:
        agent = DiscoveryAgent(
            method_hub=FakeMethodHub(tools=[]),
            model_factory=lambda model: object(),
            agent_factory=lambda **kwargs: None,
            profile_registrar=lambda model: None,
            trace_factory=lambda function: function,
        )

        with self.assertRaisesRegex(RuntimeError, "no report retrieval tools"):
            await agent.discover(
                query="Create a report",
                organization_id="test-org",
                workspace_id="default",
            )

    async def test_requires_structured_selection(self) -> None:
        compiled = FakeCompiledAgent({"messages": []})
        agent = DiscoveryAgent(
            method_hub=FakeMethodHub(),
            model_factory=lambda model: object(),
            agent_factory=lambda **kwargs: compiled,
            profile_registrar=lambda model: None,
            trace_factory=lambda function: function,
        )

        with self.assertRaisesRegex(RuntimeError, "no structured selection"):
            await agent.discover(
                query="Create a report",
                organization_id="test-org",
                workspace_id="default",
            )

    async def test_recover_tool_errors_returns_error_tool_message(self) -> None:
        request = type(
            "Request",
            (),
            {"tool_call": {"id": "call-1", "name": "corpus_retrieve_context"}},
        )()

        async def failing_handler(request):
            raise RuntimeError("embedding provider unavailable")

        result = await _recover_tool_errors.awrap_tool_call(
            request,
            failing_handler,
        )

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.status, "error")
        self.assertIn("embedding provider unavailable", result.content)
        self.assertIn("another available retrieval tool", result.content)


if __name__ == "__main__":
    unittest.main()
