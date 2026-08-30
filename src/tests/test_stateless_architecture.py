from pathlib import Path
import unittest


BACKEND = Path(__file__).resolve().parents[1]


class StatelessArchitectureTests(unittest.TestCase):
    def test_removed_stateful_modules_are_absent(self):
        removed = [
            "app/api/deps.py",
            "app/api/v1/chat.py",
            "app/api/v1/conversations.py",
            "app/api/v1/files.py",
            "app/api/v1/code_execution.py",
            "app/api/v1/export.py",
            "app/models/models.py",
            "app/models/schemas.py",
            "app/models/__init__.py",
            "app/core/database.py",
            "app/core/security.py",
            "app/core/hashid.py",
            "app/services/agent_service.py",
            "app/services/code_execution_service.py",
            "app/services/local_code_execution.py",
            "app/services/opensandbox_service.py",
            "app/services/oss_service.py",
            "app/services/sandbox_file_manager.py",
            "app/services/pdf_service.py",
            "app/services/notebook_service.py",
            "app/services/report_service.py",
            "app/services/latex_service.py",
            "app/services/notebook_kernel.py",
        ]

        self.assertEqual(
            [path for path in removed if (BACKEND / path).exists()],
            [],
        )

    def test_python_sources_do_not_reference_genreport_persistence(self):
        forbidden = (
            "sqlalchemy",
            "DATABASE_URL",
            "UsageRecord",
            "class Conversation(",
            "class Message(",
            "FILE_STORAGE_MODE",
            "CODE_EXECUTION_MODE",
            "get_opensandbox_service",
            "get_oss_service",
        )
        sources = "\n".join(
            path.read_text(encoding="utf-8") for path in (BACKEND / "app").rglob("*.py")
        )

        self.assertEqual([value for value in forbidden if value in sources], [])

    def test_requirements_exclude_stateful_dependencies(self):
        requirements = (BACKEND / "requirements.txt").read_text(encoding="utf-8")

        for package in ("sqlalchemy", "alembic", "python-jose", "bcrypt", "hashids"):
            self.assertNotIn(package, requirements.lower())


if __name__ == "__main__":
    unittest.main()
