import unittest
from unittest.mock import AsyncMock

from app.contracts.related_files import DiscoverySeed, RelatedFilesRequest
from app.contracts.report_execution import ExecutionFileRequest
from app.services.related_file_discovery import RelatedFileDiscoveryService
from app.services.report_input_preparation import ReportInputPreparationService


def overview(index, workspace="ws"):
    return {
        "result": {
            "document": {
                "document_id": f"doc-{index}",
                "workspace_id": workspace,
                "object_key": f"source-{index}.pdf",
                "bucket": "bucket",
                "file_name": f"source-{index}.pdf",
            },
            "contents": [{"text": f"TOPIC-{index} " + "content " * 300}],
        }
    }


class DiscoveryContextTests(unittest.IsolatedAsyncioTestCase):
    async def run_report(self, hub, agent, count=1):
        files = [
            ExecutionFileRequest(
                artifact_id=f"asset-{i}",
                filename=f"source-{i}.pdf",
                source_id=f"source-{i}.pdf",
                source_object_key=f"source-{i}.pdf",
                sandbox_path=f"/workspace/runs/run/inputs/source-{i}.pdf",
                size=1,
            )
            for i in range(count)
        ]
        service = ReportInputPreparationService(
            method_hub=hub,
            discovery_agent=agent,
            runtime_gateway_client=AsyncMock(),
        )
        result = await service.prepare(
            query="Create a report about filenames",
            existing_files=files,
            discover_workspace_files=True,
            organization_id="org",
            workspace_id="ws",
            primary_source_ids=[file.source_id for file in files],
            runtime_gateway=None,
            model=None,
        )
        self.assertIsNone(files[0].document_id)
        return result

    async def test_missing_document_ids_resolve_to_same_query_as_picker(self):
        report_hub, report_agent = AsyncMock(), AsyncMock()
        report_hub.call_tool.side_effect = [overview(i) for i in range(4)]
        report_agent.discover.return_value = []
        prepared = await self.run_report(report_hub, report_agent, count=4)
        self.assertEqual(
            [file.document_id for file in prepared.files],
            [f"doc-{i}" for i in range(4)],
        )
        self.assertEqual(
            report_hub.call_tool.call_args_list[0].args[1],
            {
                "object_key": "source-0.pdf",
                "workspace_id": "ws",
                "mode": "overview",
                "output_compression": "none",
            },
        )
        picker_hub, picker_agent = AsyncMock(), AsyncMock()
        picker_hub.call_tool.side_effect = [overview(i) for i in range(4)]
        picker_agent.discover.return_value = []
        await RelatedFileDiscoveryService(picker_hub, picker_agent).discover(
            RelatedFilesRequest(
                organization_id="org",
                workspace_id="ws",
                files=[
                    DiscoverySeed(
                        document_id=f"doc-{i}",
                        object_key=f"source-{i}.pdf",
                        bucket="bucket",
                    )
                    for i in range(4)
                ],
            )
        )
        query = report_agent.discover.call_args.kwargs["query"]
        self.assertEqual(query, picker_agent.discover.call_args.kwargs["query"])
        self.assertIn("TOPIC-3", query)
        self.assertGreater(len(query), 6000)

    async def test_foreign_source_metadata_does_not_trigger_filename_search(self):
        hub, agent = AsyncMock(), AsyncMock()
        hub.call_tool.return_value = overview(0, workspace="foreign")
        with self.assertLogs("app.services.report_input_preparation", level="WARNING"):
            await self.run_report(hub, agent)
        agent.discover.assert_not_awaited()

    async def test_missing_overview_does_not_trigger_filename_search(self):
        hub, agent = AsyncMock(), AsyncMock()
        payload = overview(0)
        payload["result"]["contents"] = []
        hub.call_tool.return_value = payload
        with self.assertLogs("app.services.report_input_preparation", level="WARNING"):
            await self.run_report(hub, agent)
        agent.discover.assert_not_awaited()
