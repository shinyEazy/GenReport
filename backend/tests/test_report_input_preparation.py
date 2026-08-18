import unittest
from unittest.mock import AsyncMock

from app.contracts.report_execution import ExecutionFileRequest
from app.services.report_input_preparation import (
    ReportInputPreparationError,
    ReportInputPreparationService,
)


def metadata_result(
    document_id: str,
    *,
    workspace_id: str = "workspace-b",
    filename: str = "uet.xlsx",
) -> dict:
    return {
        "result": {
            "document": {
                "document_id": document_id,
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
    async def test_resolves_document_ids_and_stages_authoritative_metadata(self) -> None:
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

        files = await service.prepare(
            query="Create a UET report",
            existing_files=[],
            discover_workspace_files=True,
            organization_id="test-org",
            workspace_id="workspace-b",
            runtime_gateway={"endpoint": "http://runtime.test", "token": "secret"},
            model="test-model",
        )

        self.assertEqual(files[0].artifact_id, "asset-1")
        method_hub.call_tool.assert_awaited_once_with(
            "corpus_get_file_ingested_data",
            {
                "document_id": "doc-1",
                "organization_id": "test-org",
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

        files = await service.prepare(
            query="Create a report",
            existing_files=[],
            discover_workspace_files=True,
            organization_id="test-org",
            workspace_id="workspace-b",
            runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
            model="test-model",
        )

        self.assertEqual([item.artifact_id for item in files], ["asset-valid"])
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

        files = await service.prepare(
            query="hello",
            existing_files=[],
            discover_workspace_files=True,
            organization_id="test-org",
            workspace_id="workspace-b",
            runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
            model="test-model",
        )

        self.assertEqual(files, [])
        method_hub.call_tool.assert_not_awaited()
        runtime_gateway.stage_report_inputs.assert_not_awaited()

    async def test_explicit_files_skip_discovery_and_metadata_lookup(self) -> None:
        existing = [
            ExecutionFileRequest(
                artifact_id="attachment-1",
                filename="upload.csv",
                sandbox_path="/workspace/runs/resp-1/inputs/abc-upload.csv",
                content_type="text/csv",
                size=10,
            )
        ]
        discovery = AsyncMock()
        method_hub = AsyncMock()
        runtime_gateway = AsyncMock()
        service = ReportInputPreparationService(
            discovery_agent=discovery,
            method_hub=method_hub,
            runtime_gateway_client=runtime_gateway,
        )

        files = await service.prepare(
            query="Create a report",
            existing_files=existing,
            discover_workspace_files=True,
            organization_id="test-org",
            workspace_id="workspace-b",
            runtime_gateway={},
            model="test-model",
        )

        self.assertEqual(files, existing)
        discovery.discover.assert_not_awaited()
        method_hub.call_tool.assert_not_awaited()
        runtime_gateway.stage_report_inputs.assert_not_awaited()

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
