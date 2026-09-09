# Evidence-Bound Reporting Design

## Goal

Make generated reports proportional to the supplied evidence. A report must
state only conclusions directly supported by the input files, and it must
identify insufficient, missing, ambiguous, or conflicting evidence instead of
inferring a stronger conclusion.

## Design

Add the following default rules to the shared report system prompt used by both
local and service report workflows:

- Every substantive fact, comparison, and conclusion must be directly
  supported by supplied input files and cited to those files.
- Do not use general domain knowledge, speculate, fill gaps, or broaden the
  report beyond the user instruction and supplied evidence.
- If the evidence is absent, weak, ambiguous, incomplete, or conflicting,
  explicitly state that there is insufficient evidence and describe the
  limitation without guessing.
- Keep the report proportional to the available evidence; do not add a lengthy
  narrative when the inputs support only a narrow finding.

The rules belong in `render_system_prompt`, so they apply consistently to
standard and local report generation without new CLI flags, configuration, or
extra model calls.

## Validation

Extend prompt tests to assert the evidence-bound, insufficiency, and
proportional-length instructions are present. Run the focused prompt tests and
the complete test suite.

## Scope

This change guides generation behavior only. It does not introduce a separate
evidence-extraction phase, modify citation rendering, or guarantee that a
model never violates instructions.
