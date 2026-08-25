# Separate GenReport Discovery and Generation Traces

## Goal

Make LangSmith show two independent, compact trace trees for a report request:
one for workspace file discovery and one for report generation.

## Trace contract

When workspace discovery runs, it keeps its existing root and DeepAgents tree:

```text
genreport-file-discovery
└─ report-file-discovery
   ├─ model
   └─ tools
```

After input preparation completes, report generation starts a separate root:

```text
genreport-report-workflow
├─ model
├─ tools
├─ model
└─ ...
```

The generation tree contains only named `model` and `tools` spans. Input
preparation, asset materialization, prompt construction, and artifact
finalization still execute with their current error handling and SSE behavior,
but do not create LangSmith spans.

## Design

`ReportExecutionService.stream()` prepares inputs before entering the traced
generation implementation. This prevents the discovery agent's root trace from
being a child of the generation root. It preserves the public event sequence:
the preparing status is emitted first, input-preparation failures still produce
a typed `report.failed` event, and successful selected-input events are emitted
before generated output.

`DiscoveryAgent` restores its previous root trace name,
`genreport-file-discovery`. Its existing DeepAgents agent remains named
`report-file-discovery`, so LangSmith continues to show the familiar nested
model and tool spans.

The remote generation wrapper retains only two trace boundaries:

- each LLM stream round is an `llm` span named `model`;
- each executor tool invocation is a `tool` span named `tools`.

The root keeps the `genreport`, `report-workflow`, and `remote` tags. No
change is made to local CLI tracing in this scope.

## Testing

Regression tests will assert that discovery remains an independent trace
factory and that a remote report execution records only the generation root,
its model rounds, and its tool rounds. Existing SSE, tool-lifecycle, failure,
and input-selection tests must remain green.
