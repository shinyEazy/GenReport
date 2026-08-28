import unittest

import httpx
from app.api.v1 import reports
from app.contracts.report_execution import ReportCompletion
from app.services.report_events import ReportEventFactory
from main import app

from tests.test_report_contract import valid_payload


class FakeExecutionService:
    async def stream(self, request):
        factory = ReportEventFactory(request)
        yield factory.create(
            "report.output_text.delta",
            {"delta": "Report ready."},
        )
        yield factory.create(
            "report.completed",
            ReportCompletion(output_text="Report ready.").model_dump(mode="json"),
        )


async def fake_execution_service():
    return FakeExecutionService()


class ReportsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app.dependency_overrides[reports.get_report_execution_service] = (
            fake_execution_service
        )
        app.dependency_overrides[reports.get_dashboard_extraction_service] = (
            fake_execution_service
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        app.dependency_overrides.clear()
        await self.client.aclose()

    async def test_reports_stream_runs_without_service_auth(self):
        response = await self.client.post(
            "/api/v1/reports:stream", json=valid_payload()
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: report.completed", response.text)

    async def test_dashboard_extraction_stream_is_a_separate_sse_route(self):
        response = await self.client.post(
            "/api/v1/reports:extract-dashboard", json=valid_payload()
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("event: report.completed", response.text)

    async def test_reports_stream_returns_validation_error_before_sse(self):
        payload = valid_payload()
        payload["execution_context"]["run_id"] = "other"

        response = await self.client.post(
            "/api/v1/reports:stream",
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))

    async def test_reports_stream_emits_normalized_events(self):
        response = await self.client.post(
            "/api/v1/reports:stream",
            json=valid_payload(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("event: report.output_text.delta", response.text)
        self.assertIn("event: report.completed", response.text)

    async def test_removed_user_facing_routes_are_not_mounted(self):
        self.assertEqual(
            (await self.client.post("/api/v1/conversations", json={})).status_code,
            404,
        )
        self.assertEqual(
            (await self.client.post("/api/v1/chat/stream", json={})).status_code,
            404,
        )
        self.assertEqual(
            (await self.client.post("/api/v1/files/upload", files={})).status_code,
            404,
        )
        self.assertEqual(
            (await self.client.post("/api/v1/export/pdf", json={})).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
