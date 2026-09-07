import unittest
from unittest.mock import AsyncMock

from app.contracts.related_files import DiscoverySeed, RelatedFilesRequest
from app.services.related_file_discovery import RelatedFileDiscoveryService


def overview(document_id, key, workspace="ws"):
    return {
        "result": {
            "document": {
                "document_id": document_id,
                "workspace_id": workspace,
                "object_key": key,
                "bucket": "bucket",
                "file_name": key,
            },
            "contents": [{"text": "Quarterly copper production and forecasts"}],
        }
    }


class RelatedFileDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hub = AsyncMock()
        self.agent = AsyncMock()
        self.service = RelatedFileDiscoveryService(self.hub, self.agent)
        self.request = RelatedFilesRequest(
            organization_id="org",
            workspace_id="ws",
            files=[
                DiscoverySeed(
                    document_id="seed", object_key="seed.pdf", bucket="bucket"
                )
            ],
        )

    async def test_context_excludes_seeds_duplicates_and_foreign_results(self):
        self.agent.discover.return_value = ["seed", "related", "related", "foreign"]
        self.hub.call_tool.side_effect = [
            overview("seed", "seed.pdf"),
            overview("related", "related.pdf"),
            overview("foreign", "foreign.pdf", "elsewhere"),
        ]
        result = await self.service.discover(self.request)
        self.assertEqual([file.document_id for file in result.files], ["related"])
        arguments = self.agent.discover.call_args.kwargs
        self.assertIn("Quarterly copper", arguments["query"])
        self.assertEqual(arguments["workspace_id"], "ws")
        self.assertEqual(arguments["organization_id"], "org")

    async def test_invalid_seed_stops_discovery(self):
        self.hub.call_tool.return_value = overview("seed", "other.pdf")
        with self.assertRaises(ValueError):
            await self.service.discover(self.request)
        self.agent.discover.assert_not_awaited()

    async def test_empty_overview_stops_discovery(self):
        payload = overview("seed", "seed.pdf")
        payload["result"]["contents"] = []
        self.hub.call_tool.return_value = payload
        with self.assertRaises(ValueError):
            await self.service.discover(self.request)
        self.agent.discover.assert_not_awaited()


class RelatedFileEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_contract_and_errors(self):
        import httpx
        from main import app
        from app.api.v1.related_files import get_related_file_service
        from app.contracts.related_files import RelatedFilesResponse

        service = AsyncMock()
        service.discover.return_value = RelatedFilesResponse(files=[])
        app.dependency_overrides[get_related_file_service] = lambda: service
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                payload = {
                    "organization_id": "org",
                    "workspace_id": "ws",
                    "files": [
                        {
                            "document_id": "seed",
                            "object_key": "seed.pdf",
                            "bucket": "bucket",
                        }
                    ],
                }
                response = await client.post(
                    "/api/v1/reports:discover-related", json=payload
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"files": []})
                response = await client.post(
                    "/api/v1/reports:discover-related", json={**payload, "files": []}
                )
                self.assertEqual(response.status_code, 422)
                service.discover.side_effect = ValueError("Selected file unavailable")
                response = await client.post(
                    "/api/v1/reports:discover-related", json=payload
                )
                self.assertEqual(response.status_code, 422)
        finally:
            app.dependency_overrides.pop(get_related_file_service, None)
