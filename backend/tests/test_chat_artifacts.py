from app.api.v1 import chat


def test_write_file_registration_uses_resolved_workspace_path() -> None:
    assert chat._resolved_generated_file_path(
        "write_file",
        {"path": "/tmp/workspace/report.html"},
        {
            "success": True,
            "path": "/workspace/GenReport/backend/data/workspaces/49/report.html",
        },
    ) == "/workspace/GenReport/backend/data/workspaces/49/report.html"


def test_report_discovery_and_preparation_share_method_hub_client() -> None:
    service = chat.report_input_preparation_service
    assert service.method_hub is service.discovery_agent.method_hub
