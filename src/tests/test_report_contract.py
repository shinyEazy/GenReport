import json
import unittest

from pydantic import ValidationError

from app.contracts.report_execution import (
    ReportExecutionRequest,
    ReportUsage,
)
from app.services.report_events import ReportEventFactory, encode_report_sse


def valid_payload() -> dict:
    return {
        "schema_version": "1",
        "operation_id": "op_1",
        "response_id": "resp_1",
        "run_id": "run_1",
        "trace_id": "trace_1",
        "instruction": "Create a report",
        "history": [
            {
                "role": "user",
                "content": "Earlier question",
                "artifact_refs": [],
            },
            {
                "role": "assistant",
                "content": "Earlier answer",
                "artifact_refs": ["artifact://report-0"],
            },
        ],
        "model": "deepseek-v4-pro",
        "language": "en",
        "organization_id": "org-1",
        "workspace_id": "workspace-1",
        "execution_context": {
            "version": "v1",
            "run_id": "run_1",
            "conversation_id": "conv_1",
            "sandbox_id": "00000000-0000-0000-0000-000000000001",
            "execution_workspace_id": "00000000-0000-0000-0000-000000000002",
            "gateway_url": "http://axiom/api/v1/runtime/runs/run_1",
            "capability_token": "runtime-token",
            "expires_at": 2_000_000_000,
            "input_path": "/workspace/runs/run_1/inputs",
            "work_path": "/workspace/runs/run_1/work",
            "output_path": "/workspace/runs/run_1/outputs",
            "capabilities": ["sandbox.files", "sandbox.commands"],
        },
        "execution_files": [
            {
                "artifact_id": "artifact-1",
                "filename": "input.csv",
                "sandbox_path": "/workspace/runs/run_1/inputs/input.csv",
                "content_type": "text/csv",
                "size": 10,
            }
        ],
        "runtime_gateway": {
            "run_id": "run_1",
            "endpoint": "http://axiom/api/v1/runtime/runs/run_1",
            "token": "runtime-token",
            "token_type": "bearer",
            "expires_at": 2_000_000_000,
            "workspace_id": "workspace-1",
            "capabilities": ["sandbox.files", "sandbox.commands"],
        },
        "discover_workspace_files": False,
    }


class ReportExecutionContractTests(unittest.TestCase):
    def test_accepts_selected_files(self):
        payload = valid_payload()
        payload["selected_files"] = {
            "mode": "selected",
            "resource_ids": ["document-1"],
            "resource_names": ["report.pdf"],
        }

        request = ReportExecutionRequest.model_validate(payload)

        self.assertEqual(request.selected_files.resource_ids, ["document-1"])

    def test_accepts_self_contained_report_request(self):
        request = ReportExecutionRequest.model_validate(valid_payload())

        self.assertEqual(request.run_id, "run_1")
        self.assertEqual(request.history[1].role, "assistant")
        self.assertEqual(request.execution_files[0].artifact_id, "artifact-1")

    def test_requires_exact_run_paths(self):
        payload = valid_payload()
        payload["execution_context"]["output_path"] = "/workspace/runs/other/outputs"

        with self.assertRaisesRegex(ValidationError, "output_path"):
            ReportExecutionRequest.model_validate(payload)

    def test_rejects_file_from_another_run(self):
        payload = valid_payload()
        payload["execution_files"][0]["sandbox_path"] = (
            "/workspace/runs/other/inputs/input.csv"
        )

        with self.assertRaisesRegex(ValidationError, "sandbox_path"):
            ReportExecutionRequest.model_validate(payload)

    def test_rejects_runtime_gateway_run_mismatch(self):
        payload = valid_payload()
        payload["runtime_gateway"]["run_id"] = "other"

        with self.assertRaisesRegex(ValidationError, "runtime_gateway"):
            ReportExecutionRequest.model_validate(payload)

    def test_rejects_internal_history_role(self):
        payload = valid_payload()
        payload["history"] = [{"role": "tool", "content": "hidden"}]

        with self.assertRaises(ValidationError):
            ReportExecutionRequest.model_validate(payload)

    def test_primary_source_must_match_exactly_one_execution_file(self):
        payload = valid_payload()
        payload["primary_source_id"] = "source-primary"
        payload["execution_files"][0].update(
            {
                "source_id": "source-primary",
                "document_id": "document-primary",
                "source_object_key": "organizations/org-1/sources/latest.csv",
                "source_last_modified": "2026-08-20T09:00:00Z",
            }
        )

        request = ReportExecutionRequest.model_validate(payload)

        self.assertEqual(request.primary_source_id, "source-primary")
        self.assertEqual(request.execution_files[0].source_id, "source-primary")

        payload["execution_files"][0]["source_id"] = "source-other"
        with self.assertRaisesRegex(ValidationError, "primary_source_id"):
            ReportExecutionRequest.model_validate(payload)

    def test_event_factory_emits_complete_correlation_envelope(self):
        request = ReportExecutionRequest.model_validate(valid_payload())
        usage = ReportUsage(
            model="deepseek-v4-pro",
            input_tokens=10,
            output_tokens=4,
            reasoning_tokens=0,
            total_tokens=14,
            estimated=False,
        )

        event = ReportEventFactory(request).create(
            "report.usage",
            usage.model_dump(mode="json"),
        )
        encoded = encode_report_sse(event)
        data = json.loads(
            next(
                line.removeprefix("data: ")
                for line in encoded.splitlines()
                if line.startswith("data: ")
            )
        )

        self.assertEqual(data["operation_id"], "op_1")
        self.assertEqual(data["response_id"], "resp_1")
        self.assertEqual(data["run_id"], "run_1")
        self.assertEqual(data["producer"], "gen-report")
        self.assertIn("id: evt_", encoded)


if __name__ == "__main__":
    unittest.main()
