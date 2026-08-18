import unittest

import httpx

from main import app


class MainApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_health_describes_stateless_engine(self):
        response = await self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "healthy", "service": "gen-report-engine"},
        )

    async def test_capabilities_publish_only_report_stream(self):
        response = await self.client.get("/api/v1/capabilities")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["endpoints"], ["POST /api/v1/reports:stream"])
        self.assertFalse(response.json()["persistence"])
        self.assertEqual(
            response.json()["execution_backend"],
            "axiom-runtime-gateway",
        )

    async def test_application_has_engine_identity(self):
        self.assertEqual(app.title, "GenReport Engine")


if __name__ == "__main__":
    unittest.main()
