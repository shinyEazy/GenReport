from __future__ import annotations

from collections.abc import Callable

from app.contracts.report_execution import ExecutionFileRequest, ReportExecutionRequest
from app.services.report_execution import ReportExecutionService
from app.services.report_input_preparation import (
    PreparedReportInputs,
    ReportInputPreparationError,
)
from app.services.report_prompt import render_system_prompt
from app.services.report_tracing import REMOTE_WORKFLOW_TAGS


class PdfDashboardInputPreparationService:
    """Accept exactly one already-produced PDF and never invoke discovery."""

    async def prepare(
        self,
        *,
        existing_files: list[ExecutionFileRequest],
        discover_workspace_files: bool,
        **_: object,
    ) -> PreparedReportInputs:
        if discover_workspace_files:
            raise ReportInputPreparationError(
                "Dashboard extraction does not support workspace file discovery"
            )
        if len(existing_files) != 1:
            raise ReportInputPreparationError(
                "Dashboard extraction requires exactly one PDF input"
            )
        source = existing_files[0]
        content_type = source.content_type.split(";", 1)[0].strip().lower()
        if content_type != "application/pdf" or not source.filename.lower().endswith(
            ".pdf"
        ):
            raise ReportInputPreparationError(
                "Dashboard extraction accepts only a PDF report input"
            )
        return PreparedReportInputs(files=[source], selected_inputs=[])


def build_dashboard_extraction_messages(
    request: ReportExecutionRequest,
    *,
    available_files: str,
    image_parts: list[dict] | None = None,
) -> list[dict]:
    system_prompt = f"""You are the AXIOM report dashboard extraction workflow.

{
        render_system_prompt(
            language=request.language,
            input_path=request.execution_context.input_path,
            work_path=request.execution_context.work_path,
            output_path=request.execution_context.output_path,
            available_files=available_files,
        )
    }

The only authoritative input is the supplied completed PDF report. Do not use
workspace discovery, Method Hub, external sources, or any file other than that
PDF. Read the PDF with PyMuPDF (fitz) using the sandbox execute_python tool.
When page previews are attached, use them to inspect embedded charts and images.

Create exactly one output file at
{request.execution_context.output_path}/report-dashboard.json. Do not create a
PDF, markdown, HTML, CSV, or image output. The JSON must be valid UTF-8 and
must use this exact schema:

{{
  "schema_version": 1,
  "generated_at": "ISO-8601 UTC timestamp",
  "headline": {{"title": "...", "summary": "...", "confidence": "high|medium|low"}},
  "changes": [{{"id": "...", "title": "...", "detail": "...", "tone": "positive|warning|neutral|critical"}}],
  "metrics": [{{"id": "...", "label": "...", "value": "...", "unit": "...", "delta": "...", "delta_direction": "up|down|flat", "interpretation": "...", "source_ref": "report.pdf#page=N"}}],
  "charts": [{{"id": "...", "title": "...", "description": "...", "type": "line|bar|donut", "unit": "...", "points": [{{"label": "...", "value": 0, "series": "..."}}], "source_ref": "report.pdf#page=N"}}],
  "coverage": {{"pages": 1, "source_count": 1, "extracted_sections": 1}}
}}

Only include metrics and chart points explicitly supported by the PDF. A chart
point value must be a finite number, never a placeholder or inferred value.
Use concise evidence references such as report.pdf#page=2 whenever the PDF
contains a page number. If the report has no reliable metric or chart, return
empty arrays and explain that limitation in the headline and coverage.

Keep the dashboard compact and decision-focused: return at most 3 changes, 6
metrics, and 3 charts. Keep the headline summary to one short sentence, keep
change details to one short sentence, and omit metric interpretations or chart
descriptions when they add no new information. Do not repeat the headline in
changes, metrics, or chart descriptions.
""".strip()
    instruction = (
        "Extract the report's evidence-backed dashboard snapshot now. "
        "Write report-dashboard.json before replying, then briefly summarize "
        "what was extracted."
    )
    content: str | list[dict] = instruction
    if image_parts:
        content = [{"type": "text", "text": instruction}, *image_parts]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]


def build_dashboard_extraction_service(
    *,
    llm_service: object,
    executor_factory: Callable,
    event_factory_builder: Callable,
    max_iterations: int,
    runtime_gateway_client: object,
    multimodal_models: list[str] | tuple[str, ...],
    trace_operation: Callable,
    max_page_images: int = 8,
) -> ReportExecutionService:
    return ReportExecutionService(
        llm_service=llm_service,
        input_preparer=PdfDashboardInputPreparationService(),
        executor_factory=executor_factory,
        event_factory_builder=event_factory_builder,
        max_iterations=max_iterations,
        runtime_gateway_client=runtime_gateway_client,
        multimodal_models=multimodal_models,
        trace_operation=trace_operation,
        workflow_name="genreport-dashboard-extraction-workflow",
        workflow_tags=[*REMOTE_WORKFLOW_TAGS, "dashboard-extraction"],
        emit_selected_inputs=False,
        preparing_message="Using report.pdf as the only input.",
        input_preparation_failure_phase="validation",
        prompt_builder=build_dashboard_extraction_messages,
        pdf_page_image_max_pages=max_page_images,
    )


__all__ = [
    "PdfDashboardInputPreparationService",
    "build_dashboard_extraction_messages",
    "build_dashboard_extraction_service",
]
