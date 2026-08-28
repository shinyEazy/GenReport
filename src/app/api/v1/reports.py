from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.contracts.report_execution import ReportExecutionRequest
from app.services.report_events import ReportEventFactory, encode_report_sse
from app.services.report_execution import ReportExecutionService

router = APIRouter()


def get_report_execution_service(
    request: Request,
    report_request: ReportExecutionRequest,
) -> ReportExecutionService:
    return _build_report_service(
        request,
        report_request,
        dashboard_extraction=False,
    )


def get_dashboard_extraction_service(
    request: Request,
    report_request: ReportExecutionRequest,
) -> ReportExecutionService:
    return _build_report_service(
        request,
        report_request,
        dashboard_extraction=True,
    )


def _build_report_service(
    request: Request,
    report_request: ReportExecutionRequest,
    *,
    dashboard_extraction: bool,
) -> ReportExecutionService:
    from app.core.config import settings
    from app.services.axiom_execution_client import AxiomExecutionClient
    from app.services.axiom_tool_executor import AxiomToolExecutor
    from app.services.llm_service import LLMService
    from app.services.report_dashboard_extraction import (
        build_dashboard_extraction_service,
    )
    from app.services.report_tracing import trace_operation
    from app.services.runtime_gateway_client import RuntimeGatewayClient

    runtime_gateway_client = RuntimeGatewayClient()
    input_preparer = None
    if not dashboard_extraction:
        from app.services.method_hub_client import MethodHubClient
        from app.services.report_file_discovery import DiscoveryAgent
        from app.services.report_input_preparation import ReportInputPreparationService

        method_hub = MethodHubClient(
            settings.METHOD_HUB_MCP_URL,
            authorization=request.headers.get("x-axiom-user-authorization"),
            trace_id=request.headers.get("x-trace-id"),
            organization_id=report_request.organization_id,
        )
        discovery_agent = DiscoveryAgent(
            method_hub=method_hub,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            default_model=settings.DEFAULT_MODEL,
            max_artifacts=settings.REPORT_DISCOVERY_MAX_ARTIFACTS,
            max_rounds=settings.REPORT_DISCOVERY_MAX_ROUNDS,
        )
        input_preparer = ReportInputPreparationService(
            discovery_agent=discovery_agent,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway_client,
        )

    def executor_factory(request: ReportExecutionRequest) -> AxiomToolExecutor:
        return AxiomToolExecutor(
            client=AxiomExecutionClient(request.execution_context),
            files=list(request.execution_files),
            input_path=request.execution_context.input_path,
            work_path=request.execution_context.work_path,
            output_path=request.execution_context.output_path,
            multimodal_image_detail=settings.MULTIMODAL_IMAGE_DETAIL,
            multimodal_image_max_bytes=settings.MULTIMODAL_IMAGE_MAX_BYTES,
        )

    if dashboard_extraction:
        return build_dashboard_extraction_service(
            llm_service=LLMService(),
            executor_factory=executor_factory,
            event_factory_builder=ReportEventFactory,
            max_iterations=settings.MAX_AGENT_ITERATIONS,
            runtime_gateway_client=runtime_gateway_client,
            multimodal_models=settings.MULTIMODAL_MODELS,
            trace_operation=trace_operation,
            max_page_images=settings.REPORT_DASHBOARD_MAX_PAGE_IMAGES,
        )

    assert input_preparer is not None
    return ReportExecutionService(
        llm_service=LLMService(),
        input_preparer=input_preparer,
        executor_factory=executor_factory,
        event_factory_builder=ReportEventFactory,
        max_iterations=settings.MAX_AGENT_ITERATIONS,
        runtime_gateway_client=runtime_gateway_client,
        multimodal_models=settings.MULTIMODAL_MODELS,
    )


@router.post(":stream")
async def stream_report(
    report_request: ReportExecutionRequest,
    service: ReportExecutionService = Depends(get_report_execution_service),
) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        stream = service.stream(report_request)
        try:
            async for event in stream:
                yield encode_report_sse(event)
        except asyncio.CancelledError:
            await stream.aclose()
            raise

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(":extract-dashboard")
async def extract_dashboard(
    report_request: ReportExecutionRequest,
    service: ReportExecutionService = Depends(get_dashboard_extraction_service),
) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        stream = service.stream(report_request)
        try:
            async for event in stream:
                yield encode_report_sse(event)
        except asyncio.CancelledError:
            await stream.aclose()
            raise

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
