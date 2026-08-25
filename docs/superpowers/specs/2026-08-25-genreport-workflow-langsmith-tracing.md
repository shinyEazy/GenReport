# GenReport workflow LangSmith tracing

## Goal

Trace each complete GenReport execution in LangSmith, not only workspace-file
discovery. The remote streaming report API and the local CLI runner must each
create one root workflow run. Existing file-discovery tracing must become a
child of the remote workflow trace.

## Scope

Tracing is enabled only when the existing LangSmith configuration enables it.
When disabled, unavailable, or unable to initialize, report behavior remains
unchanged and no tracing-specific dependency failure may fail a report.

The user has requested full payload capture. Trace inputs and outputs therefore
include report prompts, selected files, LLM messages and chunks, tool arguments
and results, generated artifact metadata, and failure details. This deployment
must use a LangSmith project appropriate for that data.

## Trace model

### Root runs

| Execution path | Trace name | Run type | Tags |
| --- | --- | --- | --- |
| Remote `POST /v1/reports:stream` | `genreport-report-workflow` | `chain` | `genreport`, `report-workflow`, `remote` |
| Local `LocalReportRunner.run` | `genreport-local-report-workflow` | `chain` | `genreport`, `report-workflow`, `local` |

The root input retains the caller's report request/configuration and the root
output retains the completed result or failure event. `operation_id`,
`response_id`, and `run_id` remain in the captured request data so a LangSmith
run can be correlated with AXIOM runtime events.

### Nested spans

Remote runs use child spans for input preparation, discovery, asset
materialization, prompt construction, each LLM round, each executed tool, and
artifact finalization. The existing `file-discovery` LangSmith chain is nested
inside input preparation rather than becoming a separate root.

Local runs use child spans for workspace preparation, prompt construction, each
LLM round, each executed tool, and local artifact finalization. The local path
has no workspace discovery stage.

LLM rounds are `llm` runs and include the complete message/tool-definition
input plus emitted chunks. Tool spans are `tool` runs and include the full tool
name, arguments, and result or raised failure. The remaining spans are `chain`
runs.

## Architecture

Introduce a small service-local tracing module that owns:

- enabled/disabled detection using the canonical LangSmith environment values;
- no-op wrappers when tracing is disabled or `langsmith` is unavailable;
- consistent project name, root/span names, types, and tags; and
- dynamic decorator factories usable for both coroutines and async generators.

`ReportExecutionService` and `LocalReportRunner` retain their public APIs. They
delegate to trace-wrapped private workflow implementations. Stage wrappers use
the same active context so LangSmith records a single parent-child tree. The
report event protocol, tool execution behavior, cleanup, and error mapping do
not change.

## Error handling

Workflow exceptions continue through the current report failure/event paths.
Their stage span receives the exception, and the root run records the final
failure payload. Trace setup failures degrade to an unwrapped call; they do not
change the API response, SSE stream, or CLI exit behavior.

## Tests

Tests will verify:

1. disabled tracing returns the original callable;
2. enabled tracing configures the exact root names, types, project, and tags;
3. remote report execution invokes its root and stage wrappers while preserving
   the existing SSE event sequence;
4. local report execution invokes its root and stage wrappers while preserving
   its result and artifact behavior; and
5. discovery remains a nested stage in the remote trace configuration.

Tests use injected trace factories or patched LangSmith decorators; no test
sends report content to LangSmith.
