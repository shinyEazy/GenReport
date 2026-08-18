from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.contracts.report_execution import ReportExecutionRequest
from app.services.report_events import ReportEventFactory, encode_report_sse
from app.services.report_execution import ReportExecutionService


router = APIRouter()


def get_report_execution_service() -> ReportExecutionService:
    from app.core.config import settings
    from app.services.axiom_execution_client import AxiomExecutionClient
    from app.services.axiom_tool_executor import AxiomToolExecutor
    from app.services.llm_service import LLMService
    from app.services.method_hub_client import MethodHubClient
    from app.services.report_file_discovery import DiscoveryAgent
    from app.services.report_input_preparation import ReportInputPreparationService
    from app.services.runtime_gateway_client import RuntimeGatewayClient

    runtime_gateway_client = RuntimeGatewayClient()
    method_hub = MethodHubClient(settings.METHOD_HUB_MCP_URL)
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
        )

    return ReportExecutionService(
        llm_service=LLMService(),
        input_preparer=input_preparer,
        executor_factory=executor_factory,
        event_factory_builder=ReportEventFactory,
        max_iterations=settings.MAX_AGENT_ITERATIONS,
        runtime_gateway_client=runtime_gateway_client,
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
