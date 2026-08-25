from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.contracts.report_execution import ExecutionFileRequest, SelectedReportInput

if TYPE_CHECKING:
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
    source_id: str
    content_type: str | None = None
    source_last_modified: datetime | None = None

    def as_dict(self) -> dict[str, str]:
        value = {
            "artifact_id": self.artifact_id,
            "filename": self.filename,
            "bucket": self.bucket,
            "document_id": self.document_id,
            "source_id": self.source_id,
        }
        if self.content_type:
            value["content_type"] = self.content_type
        if self.source_last_modified:
            value["source_last_modified"] = self.source_last_modified.isoformat()
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
        primary_source_id: str | None = None,
    ) -> PreparedReportInputs:
        files = list(existing_files)
        if not discover_workspace_files:
            return _prepared_inputs(files, primary_source_id=primary_source_id)
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
            if files:
                logger.warning(
                    "Report file discovery failed organization_id=%s workspace_id=%s; "
                    "retaining existing report inputs: %s",
                    organization_id,
                    workspace_id,
                    exc,
                )
                return _prepared_inputs(files, primary_source_id=primary_source_id)
            logger.exception(
                "Report file discovery failed organization_id=%s workspace_id=%s",
                organization_id,
                workspace_id,
            )
            raise ReportInputPreparationError(
                "Unable to discover workspace files for this report"
            ) from exc

        if not document_ids:
            return _prepared_inputs(files, primary_source_id=primary_source_id)

        artifacts = await self._resolve_artifacts(
            document_ids=document_ids,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        if not artifacts:
            if files:
                logger.warning(
                    "No usable related workspace files were selected "
                    "organization_id=%s workspace_id=%s; retaining existing report inputs",
                    organization_id,
                    workspace_id,
                )
                return _prepared_inputs(files, primary_source_id=primary_source_id)
            raise ReportInputPreparationError(
                "No related workspace files were selected for this report"
            )

        try:
            staged = await self.runtime_gateway_client.stage_report_inputs(
                runtime_gateway,
                [item.as_dict() for item in artifacts],
            )
            related_files = [
                ExecutionFileRequest.model_validate(item) for item in staged
            ]
        except Exception as exc:
            logger.exception(
                "Report input staging failed organization_id=%s workspace_id=%s",
                organization_id,
                workspace_id,
            )
            raise ReportInputPreparationError(
                "The selected workspace files could not be staged for this report"
            ) from exc
        if not related_files:
            raise ReportInputPreparationError(
                "The selected workspace files could not be staged for this report"
            )
        if len(related_files) != len(artifacts):
            raise ReportInputPreparationError(
                "The selected workspace files could not be matched to their sources"
            )
        files.extend(
            _with_source_metadata(file, artifact)
            for file, artifact in zip(related_files, artifacts, strict=True)
        )
        return _prepared_inputs(files, primary_source_id=primary_source_id)

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
                        "workspace_id": workspace_id,
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
                    source_id=_first_string(metadata.get("source_id"), object_key)
                    or object_key,
                    content_type=_first_string(metadata.get("content_type")),
                    source_last_modified=_as_datetime(metadata.get("last_modified")),
                )
            )
        return artifacts


@dataclass(frozen=True, slots=True)
class PreparedReportInputs:
    files: list[ExecutionFileRequest]
    selected_inputs: list[SelectedReportInput]


def _with_source_metadata(
    file: ExecutionFileRequest,
    artifact: SelectedReportArtifact,
) -> ExecutionFileRequest:
    return file.model_copy(
        update={
            "source_id": artifact.source_id,
            "document_id": artifact.document_id,
            "source_object_key": artifact.artifact_id,
            "source_last_modified": artifact.source_last_modified,
        }
    )


def _prepared_inputs(
    files: list[ExecutionFileRequest],
    *,
    primary_source_id: str | None,
) -> PreparedReportInputs:
    unique_files: list[ExecutionFileRequest] = []
    seen: set[str] = set()
    for item in files:
        identity = item.source_id or item.artifact_id
        if identity in seen:
            continue
        seen.add(identity)
        unique_files.append(item)

    selected_inputs = [
        SelectedReportInput(
            source_id=item.source_id or item.artifact_id,
            document_id=item.document_id,
            object_key=item.source_object_key or item.artifact_id,
            filename=item.filename,
            content_type=item.content_type,
            role=(
                "primary"
                if item.source_id == primary_source_id
                else "related"
            ),
        )
        for item in unique_files
    ]
    if primary_source_id is not None and sum(
        item.role == "primary" for item in selected_inputs
    ) != 1:
        raise ReportInputPreparationError(
            "primary source must match exactly one selected report input"
        )
    return PreparedReportInputs(files=unique_files, selected_inputs=selected_inputs)


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


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
