import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.contracts.related_files import RelatedFilesRequest, RelatedFilesResponse
from app.services.related_file_discovery import RelatedFileDiscoveryService

router = APIRouter()
logger = logging.getLogger(__name__)


def get_related_file_service(
    request: Request,
    payload: RelatedFilesRequest,
) -> RelatedFileDiscoveryService:
    from app.core.config import settings
    from app.services.method_hub_client import MethodHubClient
    from app.services.axiom_model_service import AxiomModelService
    from app.services.report_file_discovery import AxiomDiscoveryAgent, DiscoveryAgent

    hub = MethodHubClient(
        settings.METHOD_HUB_MCP_URL,
        authorization=request.headers.get("x-axiom-user-authorization"),
        trace_id=request.headers.get("x-trace-id"),
        organization_id=request.headers.get("x-org-id"),
    )
    model_service_configured = bool(
        settings.MODEL_SERVICE_URL or settings.MODEL_SERVICE_TOKEN
    )
    if model_service_configured and not (
        settings.MODEL_SERVICE_URL and settings.MODEL_SERVICE_TOKEN
    ):
        raise RuntimeError(
            "MODEL_SERVICE_URL and MODEL_SERVICE_TOKEN must be configured together."
        )
    if model_service_configured:
        agent = AxiomDiscoveryAgent(
            method_hub=hub,
            model_service=AxiomModelService(
                base_url=settings.MODEL_SERVICE_URL,
                token=settings.MODEL_SERVICE_TOKEN,
                organization_id=payload.organization_id,
                workspace_id=payload.workspace_id,
                run_id=f"related-{payload.workspace_id}",
                trace_id=request.headers.get("x-trace-id"),
                profile_mode=True,
            ),
            max_artifacts=settings.REPORT_DISCOVERY_MAX_ARTIFACTS,
            max_rounds=settings.REPORT_DISCOVERY_MAX_ROUNDS,
        )
    else:
        agent = DiscoveryAgent(
            method_hub=hub,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            default_model=settings.DEFAULT_MODEL,
            max_artifacts=settings.REPORT_DISCOVERY_MAX_ARTIFACTS,
            max_rounds=settings.REPORT_DISCOVERY_MAX_ROUNDS,
        )
    return RelatedFileDiscoveryService(hub, agent)


@router.post(":discover-related", response_model=RelatedFilesResponse)
async def discover_related_files(
    payload: RelatedFilesRequest,
    service: RelatedFileDiscoveryService = Depends(get_related_file_service),
) -> RelatedFilesResponse:
    try:
        return await service.discover(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Related file discovery failed workspace_id=%s", payload.workspace_id
        )
        raise HTTPException(
            status_code=503, detail="Related file discovery is unavailable. Try again."
        ) from exc
