import sys
import types
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from app.services import method_hub_client


class MethodHubClientCompatibilityTests(unittest.TestCase):
    def test_loads_legacy_streamable_http_client_name(self) -> None:
        sentinel = object()
        mcp_module = types.ModuleType("mcp")
        mcp_module.__path__ = []
        mcp_module.ClientSession = object
        client_module = types.ModuleType("mcp.client")
        client_module.__path__ = []
        streamable_module = types.ModuleType("mcp.client.streamable_http")
        streamable_module.streamablehttp_client = sentinel

        with patch.dict(
            sys.modules,
            {
                "mcp": mcp_module,
                "mcp.client": client_module,
                "mcp.client.streamable_http": streamable_module,
            },
        ):
            _, factory = method_hub_client._load_mcp_client()

        self.assertIs(factory, sentinel)


class MethodHubLangChainToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_keeps_sse_stream_open_for_slow_tool_results(self) -> None:
        class FakeSession:
            def __init__(self, *_args) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args) -> None:
                return None

            async def initialize(self) -> None:
                return None

        @asynccontextmanager
        async def fake_streamable_http_client(*_args, **_kwargs):
            yield object(), object()

        created_clients = []
        real_async_client = method_hub_client.httpx.AsyncClient

        def record_client(*args, **kwargs):
            client = real_async_client(*args, **kwargs)
            created_clients.append(client)
            return client

        client = method_hub_client.MethodHubClient("http://method-hub.test/mcp")
        with (
            patch.object(
                method_hub_client,
                "_load_mcp_client",
                return_value=(FakeSession, fake_streamable_http_client),
            ),
            patch.object(
                method_hub_client.httpx, "AsyncClient", side_effect=record_client
            ),
        ):
            async with client._session():
                pass

        self.assertEqual(created_clients[0].timeout.connect, 30.0)
        self.assertEqual(created_clients[0].timeout.read, 300.0)
        self.assertTrue(created_clients[0].follow_redirects)
        await created_clients[0].aclose()

    def test_request_headers_forward_user_context(self) -> None:
        client = method_hub_client.MethodHubClient(
            "http://method-hub.test/mcp",
            authorization="Bearer user-token",
            trace_id="trace-1",
            organization_id="org-1",
        )

        self.assertEqual(
            client._request_headers(),
            {
                "Authorization": "Bearer user-token",
                "X-Trace-ID": "trace-1",
                "X-Org-ID": "org-1",
            },
        )

    async def test_langchain_tool_injects_supported_request_scope(self) -> None:
        client = method_hub_client.MethodHubClient("http://method-hub.test/mcp")
        client.list_tools = AsyncMock(
            return_value=[
                method_hub_client.MethodHubTool(
                    name="corpus_bm25_search",
                    description="Search indexed files",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "organization_id": {"type": "string"},
                            "workspace_id": {"type": "string"},
                            "workspace_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [
                            "query",
                            "organization_id",
                            "workspace_id",
                            "workspace_ids",
                        ],
                    },
                )
            ]
        )
        client.call_tool = AsyncMock(return_value={"results": []})

        tools = await client.create_langchain_tools(
            allowed_names={"corpus_bm25_search"},
            organization_id="test-org",
            workspace_id="workspace-b",
        )

        exposed_schema = tools[0].args_schema
        self.assertEqual(set(exposed_schema["properties"]), {"query"})
        self.assertEqual(exposed_schema["required"], ["query"])
        result = await tools[0].ainvoke({"query": "UET admissions"})

        self.assertEqual(result, {"results": []})
        client.call_tool.assert_awaited_once_with(
            "corpus_bm25_search",
            {
                "query": "UET admissions",
                "organization_id": "test-org",
                "workspace_id": "workspace-b",
                "workspace_ids": ["workspace-b"],
            },
        )

    async def test_langchain_tool_does_not_send_unsupported_scope_fields(self) -> None:
        client = method_hub_client.MethodHubClient("http://method-hub.test/mcp")
        client.list_tools = AsyncMock(
            return_value=[
                method_hub_client.MethodHubTool(
                    name="corpus_bm25_search",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                )
            ]
        )
        client.call_tool = AsyncMock(return_value=[])

        tools = await client.create_langchain_tools(
            allowed_names={"corpus_bm25_search"},
            organization_id="test-org",
            workspace_id="workspace-b",
        )
        await tools[0].ainvoke({"query": "UET admissions"})

        client.call_tool.assert_awaited_once_with(
            "corpus_bm25_search",
            {"query": "UET admissions"},
        )

    async def test_langchain_tool_propagates_method_hub_errors(self) -> None:
        client = method_hub_client.MethodHubClient("http://method-hub.test/mcp")
        client.list_tools = AsyncMock(
            return_value=[
                method_hub_client.MethodHubTool(
                    name="corpus_retrieve_context",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                )
            ]
        )
        client.call_tool = AsyncMock(
            side_effect=RuntimeError("embedding provider unavailable")
        )

        tools = await client.create_langchain_tools(
            allowed_names={"corpus_retrieve_context"},
            organization_id="test-org",
            workspace_id="workspace-b",
        )

        with self.assertRaisesRegex(RuntimeError, "embedding provider unavailable"):
            await tools[0].ainvoke({"query": "UET admissions"})


if __name__ == "__main__":
    unittest.main()
