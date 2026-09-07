import unittest
from unittest.mock import AsyncMock, patch

import httpx
from app.services.tool_subscriptions import registered_tool_names


class ToolSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_at_binding_not_raw_catalog_or_execution(self):
        from app.services.method_hub_client import MethodHubClient, MethodHubTool

        client = MethodHubClient("http://hub/mcp", authorization="Bearer token")
        client.list_tools = AsyncMock(
            return_value=[MethodHubTool(name=name) for name in ["allowed", "other"]]
        )
        with patch(
            "app.services.method_hub_client.registered_tool_names",
            AsyncMock(return_value={"allowed", "unknown"}),
        ):
            bound = await client.create_langchain_tools(
                allowed_names={"allowed", "other"},
                organization_id="org",
                workspace_id="ws",
            )
        self.assertEqual([tool.name for tool in bound], ["allowed"])
        self.assertEqual(
            [tool.name for tool in await client.list_tools()], ["allowed", "other"]
        )
        with patch(
            "app.services.method_hub_client.registered_tool_names",
            AsyncMock(return_value=set()),
        ):
            self.assertEqual(
                await client.create_langchain_tools(
                    allowed_names={"allowed"}, organization_id="org", workspace_id="ws"
                ),
                [],
            )

    async def test_identity_and_response_validation(self):
        self.assertEqual(await registered_tool_names("org", None), set())
        factory = httpx.AsyncClient
        requests = []

        def response(request):
            requests.append(request)
            return httpx.Response(
                200, json={"organization_id": "org", "tool_names": ["allowed"]}
            )

        with patch(
            "app.services.tool_subscriptions.httpx.AsyncClient",
            lambda **kw: factory(**kw, transport=httpx.MockTransport(response)),
        ):
            self.assertEqual(
                await registered_tool_names("org", "Bearer token"), {"allowed"}
            )
            with self.assertRaises(ValueError):
                await registered_tool_names("other-org", "Bearer token")
        self.assertEqual(requests[0].headers["authorization"], "Bearer token")

    async def test_outage_does_not_enable_all_tools(self):
        from app.services.method_hub_client import MethodHubClient

        client = MethodHubClient("http://hub/mcp", authorization="Bearer token")
        client.list_tools = AsyncMock()
        with patch(
            "app.services.method_hub_client.registered_tool_names",
            AsyncMock(side_effect=RuntimeError("unavailable")),
        ):
            with self.assertRaises(RuntimeError):
                await client.create_langchain_tools(
                    allowed_names={"allowed"}, organization_id="org", workspace_id="ws"
                )
        client.list_tools.assert_not_awaited()
