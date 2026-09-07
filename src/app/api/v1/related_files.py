import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.contracts.related_files import RelatedFilesRequest, RelatedFilesResponse
from app.services.related_file_discovery import RelatedFileDiscoveryService

router = APIRouter()
logger = logging.getLogger(__name__)


def get_related_file_service(request: Request) -> RelatedFileDiscoveryService:
    from app.core.config import settings
    from app.services.method_hub_client import MethodHubClient
    from app.services.report_file_discovery import DiscoveryAgent

    hub = MethodHubClient(
        settings.METHOD_HUB_MCP_URL,
        authorization=request.headers.get("x-axiom-user-authorization"),
        trace_id=request.headers.get("x-trace-id"),
        organization_id=request.headers.get("x-org-id"),
    )
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
