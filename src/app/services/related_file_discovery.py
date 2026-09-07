from app.services.discovery_context import (
    format_seed_context,
    related_discovery_query,
    load_seed_overview,
)
from app.contracts.related_files import (
    RelatedFile,
    RelatedFilesRequest,
    RelatedFilesResponse,
)
from app.services.report_input_preparation import (
    _find_document_metadata,
    _overview_text,
)


class RelatedFileDiscoveryService:
    """Discover source relationships without staging files or generating a report."""

    def __init__(self, method_hub, discovery_agent):
        self.method_hub = method_hub
        self.discovery_agent = discovery_agent

    async def _overview(self, document_id: str, workspace_id: str):
        return await load_seed_overview(
            self.method_hub,
            document_id=document_id,
            workspace_id=workspace_id,
        )

    async def discover(self, request: RelatedFilesRequest) -> RelatedFilesResponse:
        seed_ids = {file.document_id for file in request.files}
        seed_keys = {(file.bucket, file.object_key) for file in request.files}
        context = []
        for seed in request.files:
            overview = await self._overview(seed.document_id, request.workspace_id)
            metadata = _find_document_metadata(
                overview,
                document_id=seed.document_id,
                workspace_id=request.workspace_id,
            )
            if (
                metadata is None
                or metadata["object_key"] != seed.object_key
                or metadata["bucket"] != seed.bucket
            ):
                raise ValueError(
                    "A selected file is no longer available in this workspace."
                )
            text = _overview_text(overview)
            if not text:
                raise ValueError(
                    "A selected file has no indexed overview. Reindex it and try again."
                )
            context.append(
                format_seed_context(
                    seed.document_id,
                    text,
                    metadata.get("file_name") or metadata["filename"],
                )
            )

        document_ids = await self.discovery_agent.discover(
            query=related_discovery_query("\n\n".join(context)),
            organization_id=request.organization_id,
            workspace_id=request.workspace_id,
        )
        files = []
        seen_keys = set(seed_keys)
        for document_id in dict.fromkeys(document_ids):
            if document_id in seed_ids:
                continue
            overview = await self._overview(document_id, request.workspace_id)
            metadata = _find_document_metadata(
                overview,
                document_id=document_id,
                workspace_id=request.workspace_id,
            )
            if metadata is None:
                continue
            key = (metadata["bucket"], metadata["object_key"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            files.append(
                RelatedFile(
                    document_id=document_id,
                    object_key=key[1],
                    bucket=key[0],
                    filename=metadata.get("file_name") or metadata["filename"],
                )
            )
        return RelatedFilesResponse(files=files)
