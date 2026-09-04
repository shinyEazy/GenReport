from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.contracts.report_execution import ExecutionFileRequest


class CitationValidationError(ValueError):
    """Raised when an input-backed report lacks required source citations."""


@dataclass(frozen=True)
class CitationManifest:
    references: tuple[str, ...]

    @property
    def prompt(self) -> str:
        if not self.references:
            return ""
        return "\n".join(("INPUT-FILE CITATION MANIFEST:", *self.references))


_REFERENCES_HEADING = re.compile(
    r"(?:^\s*(?:#{1,6}\s+)?references\s*$|"
    r"\\section\*?\{references\}|"
    r"<h[1-6]\b[^>]*>\s*references\s*</h[1-6]>)",
    re.IGNORECASE | re.MULTILINE,
)
_CITATION_MARKER = re.compile(r"\[(\s*\d+\s*(?:,\s*\d+\s*)*)\]")


def citation_manifest(files: Sequence[ExecutionFileRequest]) -> CitationManifest:
    return CitationManifest(
        tuple(f"[{index}] {item.filename}" for index, item in enumerate(files, 1))
    )


def citation_requirements(files: Sequence[ExecutionFileRequest]) -> str:
    manifest = citation_manifest(files)
    if not manifest.references:
        return ""
    return (
        "Use the input-file citation manifest below in the final PDF. Cite each "
        "substantive factual claim, statistic, comparison, or conclusion from the "
        "supplied inputs with [n] or [n, m]. Use only manifest numbers. Finish the "
        "PDF with a References heading and exactly one [n] filename entry for every "
        "manifest file in order.\n\n"
        f"{manifest.prompt}"
    )


def validate_report_source(source: str, manifest: CitationManifest) -> None:
    """Ensure source text contains inline markers and every input reference."""

    if not manifest.references:
        return

    heading = _REFERENCES_HEADING.search(source)
    if heading is None:
        raise CitationValidationError("missing References section")
    if not _contains_valid_marker(source[: heading.start()], len(manifest.references)):
        raise CitationValidationError("missing inline input-file citation")

    references = source[heading.end() :]
    missing = [entry for entry in manifest.references if entry not in references]
    if missing:
        raise CitationValidationError("missing input-file references")


def _contains_valid_marker(text: str, max_reference: int) -> bool:
    for match in _CITATION_MARKER.finditer(text):
        indexes = [int(value.strip()) for value in match.group(1).split(",")]
        if all(1 <= index <= max_reference for index in indexes):
            return True
    return False
