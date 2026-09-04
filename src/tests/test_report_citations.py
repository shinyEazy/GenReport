import unittest

from app.contracts.report_execution import ExecutionFileRequest
from app.services.report_citations import (
    CitationValidationError,
    citation_manifest,
    validate_report_source,
)


def _file(number: int, filename: str) -> ExecutionFileRequest:
    return ExecutionFileRequest(
        artifact_id=f"artifact-{number}",
        filename=filename,
        sandbox_path=f"/workspace/runs/run_1/inputs/{filename}",
        size=1,
    )


class ReportCitationTests(unittest.TestCase):
    def test_manifest_uses_received_input_order(self):
        manifest = citation_manifest(
            [_file(1, "sales.csv"), _file(2, "notes.pdf")]
        )

        self.assertEqual(manifest.references, ("[1] sales.csv", "[2] notes.pdf"))
        self.assertIn("[1] sales.csv", manifest.prompt)
        self.assertIn("[2] notes.pdf", manifest.prompt)

    def test_validator_accepts_inline_multi_source_citation_and_references(self):
        manifest = citation_manifest(
            [_file(1, "sales.csv"), _file(2, "notes.pdf")]
        )

        validate_report_source(
            "Revenue improved [1, 2].\n"
            "\\section*{References}\n"
            "[1] sales.csv\n"
            "[2] notes.pdf",
            manifest,
        )

    def test_validator_rejects_missing_inline_citation(self):
        manifest = citation_manifest([_file(1, "sales.csv")])

        with self.assertRaisesRegex(CitationValidationError, "inline"):
            validate_report_source(
                "Revenue improved.\n## References\n[1] sales.csv",
                manifest,
            )

    def test_validator_rejects_missing_references_heading(self):
        manifest = citation_manifest([_file(1, "sales.csv")])

        with self.assertRaisesRegex(CitationValidationError, "References"):
            validate_report_source("Revenue improved [1].", manifest)

    def test_validator_rejects_incomplete_reference_list(self):
        manifest = citation_manifest(
            [_file(1, "sales.csv"), _file(2, "notes.pdf")]
        )

        with self.assertRaisesRegex(CitationValidationError, "references"):
            validate_report_source(
                "Revenue improved [1].\n## References\n[1] sales.csv",
                manifest,
            )


if __name__ == "__main__":
    unittest.main()
