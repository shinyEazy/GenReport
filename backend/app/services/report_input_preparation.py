from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.models.schemas import ExecutionFileRequest
from app.services.method_hub_client import MethodHubClient
from app.services.report_file_discovery import DiscoveryAgent
from app.services.runtime_gateway_client import RuntimeGatewayClient


logger = logging.getLogger(__name__)


class ReportInputPreparationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SelectedReportArtifact:
    artifact_id: str
    filename: str
    bucket: str
    document_id: str
    content_type: str | None = None

    def as_dict(self) -> dict[str, str]:
        value = {
            "artifact_id": self.artifact_id,
            "filename": self.filename,
            "bucket": self.bucket,
            "document_id": self.document_id,
        }
        if self.content_type:
            value["content_type"] = self.content_type
        return value


class ReportInputPreparationService:
    def __init__(
        self,
        *,
        discovery_agent: DiscoveryAgent,
        method_hub: MethodHubClient,
        runtime_gateway_client: RuntimeGatewayClient,
    ) -> None:
        self.discovery_agent = discovery_agent
        self.method_hub = method_hub
        self.runtime_gateway_client = runtime_gateway_client

    async def prepare(
        self,
        *,
        query: str,
        existing_files: list[ExecutionFileRequest],
        discover_workspace_files: bool,
        organization_id: str | None,
        workspace_id: str | None,
        runtime_gateway: dict[str, Any] | None,
        model: str | None,
    ) -> list[ExecutionFileRequest]:
        if existing_files or not discover_workspace_files:
            return existing_files
        if not organization_id or not workspace_id:
            raise ReportInputPreparationError(
                "Organization and workspace are required for report file discovery"
            )

        try:
            document_ids = await self.discovery_agent.discover(
                query=query,
                organization_id=organization_id,
                workspace_id=workspace_id,
                model=model,
            )
        except Exception as exc:
            logger.exception(
                "Report file discovery failed organization_id=%s workspace_id=%s",
                organization_id,
                workspace_id,
            )
            raise ReportInputPreparationError(
                "Unable to discover workspace files for this report"
            ) from exc

        if not document_ids:
            return existing_files

        artifacts = await self._resolve_artifacts(
            document_ids=document_ids,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        if not artifacts:
            raise ReportInputPreparationError(
                "No related workspace files were selected for this report"
            )

        try:
            staged = await self.runtime_gateway_client.stage_report_inputs(
                runtime_gateway,
                [item.as_dict() for item in artifacts],
            )
        except Exception as exc:
            logger.exception(
                "Report input staging failed organization_id=%s workspace_id=%s",
                organization_id,
                workspace_id,
            )
            raise ReportInputPreparationError(
                "The selected workspace files could not be staged for this report"
            ) from exc
        files = [ExecutionFileRequest.model_validate(item) for item in staged]
        if not files:
            raise ReportInputPreparationError(
                "The selected workspace files could not be staged for this report"
            )
        return files

    async def _resolve_artifacts(
        self,
        *,
        document_ids: list[str],
        organization_id: str,
        workspace_id: str,
    ) -> list[SelectedReportArtifact]:
        artifacts: list[SelectedReportArtifact] = []
        seen: set[str] = set()
        for raw_document_id in document_ids:
            document_id = raw_document_id.strip()
            if not document_id or document_id in seen:
                continue
            seen.add(document_id)
            try:
                result = await self.method_hub.call_tool(
                    "corpus_get_file_ingested_data",
                    {
                        "document_id": document_id,
                        "organization_id": organization_id,
                        "mode": "overview",
                        "output_compression": "none",
                    },
                )
            except Exception:
                logger.warning(
                    "Report metadata lookup failed document_id=%s",
                    document_id,
                    exc_info=True,
                )
                continue
            metadata = _find_document_metadata(
                result,
                document_id=document_id,
                workspace_id=workspace_id,
            )
            if metadata is None:
                continue
            filename = _first_string(
                metadata.get("file_name"),
                metadata.get("filename"),
            )
            object_key = _first_string(metadata.get("object_key"))
            bucket = _first_string(metadata.get("bucket"))
            if filename is None or object_key is None or bucket is None:
                continue
            artifacts.append(
                SelectedReportArtifact(
                    artifact_id=object_key,
                    filename=filename,
                    bucket=bucket,
                    document_id=document_id,
                    content_type=_first_string(metadata.get("content_type")),
                )
            )
        return artifacts


def _find_document_metadata(
    value: Any,
    *,
    document_id: str,
    workspace_id: str,
) -> dict[str, Any] | None:
    if isinstance(value, list):
        for item in value:
            found = _find_document_metadata(
                item,
                document_id=document_id,
                workspace_id=workspace_id,
            )
            if found is not None:
                return found
        return None
    if not isinstance(value, dict):
        return None
    if (
        _first_string(value.get("document_id")) == document_id
        and _first_string(value.get("workspace_id")) == workspace_id
        and _first_string(value.get("object_key")) is not None
        and _first_string(value.get("bucket")) is not None
        and _first_string(value.get("file_name"), value.get("filename")) is not None
    ):
        return value
    for item in value.values():
        found = _find_document_metadata(
            item,
            document_id=document_id,
            workspace_id=workspace_id,
        )
        if found is not None:
            return found
    return None


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
