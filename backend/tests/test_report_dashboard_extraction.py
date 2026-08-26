import unittest

from app.contracts.report_execution import ExecutionFileRequest, ReportExecutionRequest
from app.services.report_dashboard_extraction import (
    PdfDashboardInputPreparationService,
    build_dashboard_extraction_messages,
    build_dashboard_extraction_service,
)
from app.services.report_input_preparation import ReportInputPreparationError

from tests.test_report_contract import valid_payload


class PdfDashboardExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_only_the_completed_pdf_without_discovery(self):
        service = PdfDashboardInputPreparationService()
        pdf = ExecutionFileRequest(
            artifact_id="report-asset",
            filename="report.pdf",
            sandbox_path="/workspace/runs/run-1/inputs/report.pdf",
            content_type="application/pdf; charset=binary",
            size=10,
        )

        prepared = await service.prepare(
            existing_files=[pdf],
            discover_workspace_files=False,
        )

        self.assertEqual(prepared.files, [pdf])
        self.assertEqual(prepared.selected_inputs, [])

    async def test_rejects_discovery_and_non_pdf_inputs(self):
        service = PdfDashboardInputPreparationService()
        pdf = ExecutionFileRequest(
            artifact_id="report-asset",
            filename="report.pdf",
            sandbox_path="/workspace/runs/run-1/inputs/report.pdf",
            content_type="application/pdf",
            size=10,
        )

        with self.assertRaisesRegex(
            ReportInputPreparationError,
            "does not support workspace file discovery",
        ):
            await service.prepare(existing_files=[pdf], discover_workspace_files=True)

        csv = pdf.model_copy(
            update={"filename": "report.csv", "content_type": "text/csv"}
        )
        with self.assertRaisesRegex(
            ReportInputPreparationError,
            "accepts only a PDF report input",
        ):
            await service.prepare(existing_files=[csv], discover_workspace_files=False)

    def test_prompt_forbids_discovery_and_requires_dashboard_contract(self):
        request = ReportExecutionRequest.model_validate(valid_payload())
        messages = build_dashboard_extraction_messages(
            request,
            available_files="- report.pdf [application/pdf]",
        )

        system_prompt = messages[0]["content"]
        self.assertIn(
            "Do not use\nworkspace discovery, Method Hub, external sources",
            system_prompt,
        )
        self.assertIn('"report.pdf#page=N"', system_prompt)
        self.assertIn("report-dashboard.json", system_prompt)
        self.assertNotIn("report.pdf in the output directory", system_prompt)

    def test_builder_uses_distinct_trace_and_suppresses_discovery_event(self):
        trace_calls: list[dict] = []

        def trace_operation(function, **kwargs):
            trace_calls.append(kwargs)
            return function

        service = build_dashboard_extraction_service(
            llm_service=object(),
            executor_factory=lambda _request: object(),
            event_factory_builder=lambda _request: object(),
            max_iterations=1,
            runtime_gateway_client=object(),
            multimodal_models=(),
            trace_operation=trace_operation,
        )

        self.assertEqual(trace_calls[0]["name"], "genreport-dashboard-extraction-workflow")
        self.assertIn("dashboard-extraction", trace_calls[0]["tags"])
        self.assertFalse(service.emit_selected_inputs)
        self.assertEqual(service.workflow_name, "genreport-dashboard-extraction-workflow")


if __name__ == "__main__":
    unittest.main()
