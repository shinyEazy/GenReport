from app.api.v1.chat import _resolved_generated_file_path


def test_write_file_registration_uses_resolved_workspace_path() -> None:
    assert _resolved_generated_file_path(
        "write_file",
        {"path": "/tmp/workspace/report.html"},
        {
            "success": True,
            "path": "/workspace/GenReport/backend/data/workspaces/49/report.html",
        },
    ) == "/workspace/GenReport/backend/data/workspaces/49/report.html"
