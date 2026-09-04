# Input-file citations in generated reports

## Goal

Every generated PDF report must cite factual claims inline using stable, numeric
references to its supplied input files, and it must finish with a `References`
section that defines those numbers.

## Scope

- A citation identifies an input **file**, not a PDF page, spreadsheet sheet, or
  individual source passage.
- Numbering follows the received `execution_files` order and starts at one.
- A claim based on one input uses `[1]`; a claim based on multiple inputs uses
  `[1, 2]`.
- The final `References` section lists every supplied file as `[n] filename` in
  the same order.
- Reports without input files are not required to include citations or a
  `References` section.

## Architecture

`AxiomToolExecutor` will turn the request's `ExecutionFileRequest` records into
an immutable, numbered citation manifest. `report_prompt` will include that
manifest and explicit authoring rules in the system prompt. The supplied LaTeX
skill will show the same report-level requirement and a LaTeX-native pattern for
the inline markers and reference list.

Before artifacts are finalized, GenReport will inspect report source files that
the model registered as generated outputs. For runs with supplied inputs, a
report source must contain at least one valid inline marker matching the
manifest and a `References` heading containing each manifest entry. If the
validation fails, finalization fails and no incomplete PDF artifact is emitted.

## Components

### Citation manifest

A small pure helper will produce both a prompt-ready rendering and validation
metadata from `execution_files`. The manifest will retain only source numbers
and display names; it will not expose source IDs, object keys, credentials, or
sandbox paths beyond those already available to the model.

Duplicate filenames are disambiguated in the reference list by their received
order (for example, `[1] report.pdf`, `[2] report.pdf`). Citation numbers remain
the sole identifier in inline prose.

### Prompt and report skill

The system prompt will add the manifest immediately after available inputs and
will state these enforceable rules:

1. Cite every substantive fact, statistic, comparison, and conclusion derived
   from supplied input with `[n]` or `[n, m]`.
2. Use only numbers from the manifest, without invented sources.
3. End the final PDF with a heading named `References`, followed by one entry
   for every provided file in manifest order.

The LaTeX skill will provide a plain-LaTeX implementation that does not require
BibTeX or network-installed packages.

### Validation

Validation applies only if one or more input files exist. It operates on text
source artifacts (`.tex`, `.md`, `.html`, `.htm`) belonging to the generated
report. A source artifact is valid when it contains at least one citation marker
within the manifest range and all required reference-list entries after a
`References` heading.

PDF is deliberately not parsed: checking the authored source avoids unreliable
PDF text extraction and happens before Runtime Gateway finalization. If no
inspectable report source is registered, or the source is missing requirements,
the run fails in the artifact phase with a clear internal error.

## Error handling

- No input files: citation validation is skipped.
- Invalid/missing citation source: artifact finalization raises an artifact
  error, yielding `report.failed`; no final PDF is published.
- Non-report generated files do not need citations.

## Testing

- Unit-test manifest output and prompt instructions for single and multiple
  files.
- Unit-test validation acceptance for valid `[1]` and `[1, 2]` citations with
  complete references.
- Unit-test rejection for absent inline citations, missing `References`, and a
  reference list missing an input file.
- Preserve current behavior for runs with no supplied files.

## Non-goals

- Page, sheet, cell, paragraph, or URL-level citations.
- A database schema, frontend citation panel, or a citation API in the report
  completion event.
- Retrofitting already-published reports.
