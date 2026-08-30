import logging
import unittest
from unittest.mock import AsyncMock

from app.contracts.report_execution import SelectedFilesRequest, ExecutionFileRequest
from app.services.report_input_preparation import (
    ReportInputPreparationError,
    ReportInputPreparationService,
)


def metadata_result(
    document_id: str,
    *,
    workspace_id: str = "workspace-b",
    filename: str = "uet.xlsx",
    source_id: str | None = None,
) -> dict:
    return {
        "result": {
            "document": {
                "document_id": document_id,
                "source_id": source_id or f"source-{document_id}",
                "workspace_id": workspace_id,
                "object_key": (
                    f"organizations/test-org/workspaces/{workspace_id}/sources/{filename}"
                ),
                "file_name": filename,
                "bucket": "axiom-documents",
                "content_type": "application/vnd.ms-excel",
            }
        }
    }


class ReportInputPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_files_stage_requested_documents_without_discovery(self) -> None:
        discovery = AsyncMock()
        method_hub = AsyncMock()
        method_hub.call_tool.return_value = metadata_result(
            "doc-selected",
            filename="selected.pdf",
        )
        runtime_gateway = AsyncMock()
        runtime_gateway.stage_report_inputs.return_value = [
            {
                "artifact_id": "asset-selected",
                "filename": "selected.pdf",
                "sandbox_path": "/workspace/runs/resp-1/inputs/selected.pdf",
                "content_type": "application/pdf",
                "size": 123,
            }
        ]
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway,
        )

        prepared = await service.prepare(
            query="Create a report",
            existing_files=[],
            discover_workspace_files=True,
            organization_id="test-org",
            workspace_id="workspace-b",
            runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
            model="test-model",
            selected_files=SelectedFilesRequest(
                mode="selected",
                resource_ids=["doc-selected"],
                resource_names=["selected.pdf"],
            ),
        )

        discovery.discover.assert_not_awaited()
        self.assertEqual([item.document_id for item in prepared.files], ["doc-selected"])

    async def test_logs_when_workspace_discovery_is_disabled(self) -> None:
        discovery = AsyncMock()
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=AsyncMock(),
            runtime_gateway_client=AsyncMock(),
        )
        existing = [
            ExecutionFileRequest(
                artifact_id="asset-1",
                filename="latest.xlsx",
                sandbox_path="/workspace/runs/resp-1/inputs/latest.xlsx",
                content_type="application/vnd.ms-excel",
                size=123,
                source_id="source-1",
            )
        ]

        with self.assertLogs(
            "app.services.report_input_preparation", level=logging.INFO
        ) as logs:
            prepared = await service.prepare(
                query="Create a report",
                existing_files=existing,
                discover_workspace_files=False,
                organization_id="test-org",
                workspace_id="workspace-b",
                runtime_gateway=None,
                model="test-model",
                primary_source_id="source-1",
            )

        discovery.discover.assert_not_awaited()
        self.assertEqual(prepared.files[0].filename, "latest.xlsx")
        output = "\n".join(logs.output)
        self.assertIn("workspace file discovery skipped", output)
        self.assertIn("latest.xlsx", output)

    async def test_resolves_document_ids_and_stages_authoritative_metadata(
        self,
    ) -> None:
        discovery = AsyncMock()
        discovery.discover.return_value = ["doc-1", "doc-1"]
        method_hub = AsyncMock()
        method_hub.call_tool.return_value = metadata_result("doc-1")
        runtime_gateway = AsyncMock()
        runtime_gateway.stage_report_inputs.return_value = [
            {
                "artifact_id": "asset-1",
                "filename": "uet.xlsx",
                "sandbox_path": "/workspace/runs/resp-1/inputs/abc-uet.xlsx",
                "content_type": "application/vnd.ms-excel",
                "size": 123,
            }
        ]
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway,
        )

        prepared = await service.prepare(
            query="Create a UET report",
            existing_files=[],
            discover_workspace_files=True,
            organization_id="test-org",
            workspace_id="workspace-b",
            runtime_gateway={"endpoint": "http://runtime.test", "token": "secret"},
            model="test-model",
        )

        self.assertEqual(prepared.files[0].artifact_id, "asset-1")
        method_hub.call_tool.assert_awaited_once_with(
            "corpus_get_file_ingested_data",
            {
                "document_id": "doc-1",
                "workspace_id": "workspace-b",
                "mode": "overview",
                "output_compression": "none",
            },
        )
        runtime_gateway.stage_report_inputs.assert_awaited_once_with(
            {"endpoint": "http://runtime.test", "token": "secret"},
            [
                {
                    "artifact_id": (
                        "organizations/test-org/workspaces/workspace-b/sources/uet.xlsx"
                    ),
                    "filename": "uet.xlsx",
                    "bucket": "axiom-documents",
                    "document_id": "doc-1",
                    "source_id": "source-doc-1",
                    "content_type": "application/vnd.ms-excel",
                }
            ],
        )

    async def test_discards_cross_workspace_metadata_and_keeps_valid_file(self) -> None:
        discovery = AsyncMock()
        discovery.discover.return_value = ["doc-wrong", "doc-valid"]
        method_hub = AsyncMock()
        method_hub.call_tool.side_effect = [
            metadata_result("doc-wrong", workspace_id="workspace-a"),
            metadata_result("doc-valid", workspace_id="workspace-b"),
        ]
        runtime_gateway = AsyncMock()
        runtime_gateway.stage_report_inputs.return_value = [
            {
                "artifact_id": "asset-valid",
                "filename": "uet.xlsx",
                "sandbox_path": "/workspace/runs/resp-1/inputs/valid-uet.xlsx",
                "content_type": "application/vnd.ms-excel",
                "size": 123,
            }
        ]
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway,
        )

        prepared = await service.prepare(
            query="Create a report",
            existing_files=[],
            discover_workspace_files=True,
            organization_id="test-org",
            workspace_id="workspace-b",
            runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
            model="test-model",
        )

        self.assertEqual([item.artifact_id for item in prepared.files], ["asset-valid"])
        staged = runtime_gateway.stage_report_inputs.await_args.args[1]
        self.assertEqual([item["document_id"] for item in staged], ["doc-valid"])

    async def test_fails_when_no_selected_metadata_matches_workspace(self) -> None:
        discovery = AsyncMock()
        discovery.discover.return_value = ["doc-wrong"]
        method_hub = AsyncMock()
        method_hub.call_tool.return_value = metadata_result(
            "doc-wrong", workspace_id="workspace-a"
        )
        runtime_gateway = AsyncMock()
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway,
        )

        with self.assertRaisesRegex(
            ReportInputPreparationError,
            "No related workspace files",
        ):
            await service.prepare(
                query="Create a report",
                existing_files=[],
                discover_workspace_files=True,
                organization_id="test-org",
                workspace_id="workspace-b",
                runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
                model="test-model",
            )

        runtime_gateway.stage_report_inputs.assert_not_awaited()

    async def test_allows_discovery_to_select_no_files(self) -> None:
        discovery = AsyncMock()
        discovery.discover.return_value = []
        method_hub = AsyncMock()
        runtime_gateway = AsyncMock()
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway,
        )

        prepared = await service.prepare(
            query="hello",
            existing_files=[],
            discover_workspace_files=True,
            organization_id="test-org",
            workspace_id="workspace-b",
            runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
            model="test-model",
        )

        self.assertEqual(prepared.files, [])
        method_hub.call_tool.assert_not_awaited()
        runtime_gateway.stage_report_inputs.assert_not_awaited()

    async def test_primary_execution_file_is_retained_and_related_files_are_appended(
        self,
    ) -> None:
        existing = [
            ExecutionFileRequest(
                artifact_id="attachment-1",
                filename="upload.csv",
                sandbox_path="/workspace/runs/resp-1/inputs/abc-upload.csv",
                content_type="text/csv",
                size=10,
                source_id="source-primary",
                document_id="document-primary",
                source_object_key="organizations/test-org/sources/upload.csv",
            )
        ]
        discovery = AsyncMock()
        discovery.discover.return_value = ["doc-related"]
        method_hub = AsyncMock()
        method_hub.call_tool.return_value = metadata_result(
            "doc-related",
            source_id="source-related",
        )
        runtime_gateway = AsyncMock()
        runtime_gateway.stage_report_inputs.return_value = [
            {
                "artifact_id": "attachment-related",
                "filename": "uet.xlsx",
                "sandbox_path": "/workspace/runs/resp-1/inputs/related-uet.xlsx",
                "content_type": "application/vnd.ms-excel",
                "size": 123,
            }
        ]
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway,
        )

        prepared = await service.prepare(
            query="Create a report",
            existing_files=existing,
            discover_workspace_files=True,
            organization_id="test-org",
            workspace_id="workspace-b",
            runtime_gateway={},
            model="test-model",
            primary_source_id="source-primary",
        )

        self.assertEqual(
            [item.source_id for item in prepared.files],
            ["source-primary", "source-related"],
        )
        self.assertEqual(
            [item.role for item in prepared.selected_inputs],
            ["primary", "related"],
        )
        discovery.discover.assert_awaited_once()
        method_hub.call_tool.assert_awaited_once()
        runtime_gateway.stage_report_inputs.assert_awaited_once()

    async def test_discovery_failures_use_preparation_error_boundary(self) -> None:
        discovery = AsyncMock()
        discovery.discover.side_effect = RuntimeError("method hub unavailable")
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=AsyncMock(),
            runtime_gateway_client=AsyncMock(),
        )

        with self.assertLogs(
            "app.services.report_input_preparation",
            level="ERROR",
        ):
            with self.assertRaisesRegex(
                ReportInputPreparationError,
                "Unable to discover workspace files",
            ):
                await service.prepare(
                    query="Create a report",
                    existing_files=[],
                    discover_workspace_files=True,
                    organization_id="test-org",
                    workspace_id="workspace-b",
                    runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
                    model="test-model",
                )

    async def test_discovery_failure_retains_existing_primary_file(self) -> None:
        existing = [
            ExecutionFileRequest(
                artifact_id="attachment-1",
                filename="upload.csv",
                sandbox_path="/workspace/runs/resp-1/inputs/abc-upload.csv",
                content_type="text/csv",
                size=10,
                source_id="source-primary",
                document_id="document-primary",
                source_object_key="organizations/test-org/sources/upload.csv",
            )
        ]
        discovery = AsyncMock()
        discovery.discover.side_effect = RuntimeError("provider rejected tool choice")
        method_hub = AsyncMock()
        runtime_gateway = AsyncMock()
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway,
        )

        with self.assertLogs(
            "app.services.report_input_preparation",
            level="WARNING",
        ) as logs:
            prepared = await service.prepare(
                query="Create a report",
                existing_files=existing,
                discover_workspace_files=True,
                organization_id="test-org",
                workspace_id="workspace-b",
                runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
                model="test-model",
                primary_source_id="source-primary",
            )

        self.assertEqual(prepared.files, existing)
        self.assertEqual([item.role for item in prepared.selected_inputs], ["primary"])
        self.assertEqual([record.levelno for record in logs.records], [logging.WARNING])
        method_hub.call_tool.assert_not_awaited()
        runtime_gateway.stage_report_inputs.assert_not_awaited()

    async def test_no_usable_related_artifacts_retains_existing_primary_file(
        self,
    ) -> None:
        existing = [
            ExecutionFileRequest(
                artifact_id="attachment-1",
                filename="upload.csv",
                sandbox_path="/workspace/runs/resp-1/inputs/abc-upload.csv",
                content_type="text/csv",
                size=10,
                source_id="source-primary",
                document_id="document-primary",
                source_object_key="organizations/test-org/sources/upload.csv",
            )
        ]
        discovery = AsyncMock()
        discovery.discover.return_value = ["doc-wrong"]
        method_hub = AsyncMock()
        method_hub.call_tool.return_value = metadata_result(
            "doc-wrong", workspace_id="workspace-a"
        )
        runtime_gateway = AsyncMock()
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway,
        )

        with self.assertLogs(
            "app.services.report_input_preparation",
            level="WARNING",
        ) as logs:
            prepared = await service.prepare(
                query="Create a report",
                existing_files=existing,
                discover_workspace_files=True,
                organization_id="test-org",
                workspace_id="workspace-b",
                runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
                model="test-model",
                primary_source_id="source-primary",
            )

        self.assertEqual(prepared.files, existing)
        self.assertEqual([item.role for item in prepared.selected_inputs], ["primary"])
        self.assertEqual([record.levelno for record in logs.records], [logging.WARNING])
        runtime_gateway.stage_report_inputs.assert_not_awaited()

    async def test_invalid_staged_payload_uses_preparation_error_boundary(self) -> None:
        discovery = AsyncMock()
        discovery.discover.return_value = ["doc-1"]
        method_hub = AsyncMock()
        method_hub.call_tool.return_value = metadata_result("doc-1")
        runtime_gateway = AsyncMock()
        runtime_gateway.stage_report_inputs.return_value = [{"artifact_id": "asset-1"}]
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway,
        )

        with self.assertLogs(
            "app.services.report_input_preparation",
            level="ERROR",
        ):
            with self.assertRaisesRegex(
                ReportInputPreparationError,
                "could not be staged",
            ):
                await service.prepare(
                    query="Create a report",
                    existing_files=[],
                    discover_workspace_files=True,
                    organization_id="test-org",
                    workspace_id="workspace-b",
                    runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
                    model="test-model",
                )

    async def test_staging_failures_use_preparation_error_boundary(self) -> None:
        discovery = AsyncMock()
        discovery.discover.return_value = ["doc-1"]
        method_hub = AsyncMock()
        method_hub.call_tool.return_value = metadata_result("doc-1")
        runtime_gateway = AsyncMock()
        runtime_gateway.stage_report_inputs.side_effect = RuntimeError(
            "runtime unavailable"
        )
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway,
        )

        with self.assertLogs(
            "app.services.report_input_preparation",
            level="ERROR",
        ):
            with self.assertRaisesRegex(
                ReportInputPreparationError,
                "could not be staged",
            ):
                await service.prepare(
                    query="Create a report",
                    existing_files=[],
                    discover_workspace_files=True,
                    organization_id="test-org",
                    workspace_id="workspace-b",
                    runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
                    model="test-model",
                )


if __name__ == "__main__":
    unittest.main()
