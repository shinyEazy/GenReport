# GenReport Engine

GenReport is AXIOM's internal, stateless report execution engine. It accepts one
self-contained request from Data Intelligence, runs report tools through the
AXIOM Runtime Gateway, and streams normalized events. It owns no durable data.

AXIOM persists conversations, selected history, response/run lifecycle, usage,
failures, and artifact relationships. GenReport has no database, local upload
API, conversation API, frontend, or standalone export API.

## API

- `POST /api/v1/reports:stream` - internal report execution
- `GET /health` - process health
- `GET /api/v1/capabilities` - stateless engine capabilities

The endpoint has no application-level service token. Keep it reachable only on a
trusted internal network; Runtime Gateway operations remain protected by the
request-scoped capability token.

Example request shape:

```json
{
  "schema_version": "1",
  "operation_id": "op_1",
  "response_id": "resp_1",
  "run_id": "resp_1",
  "instruction": "Create the quarterly report",
  "history": [],
  "model": "deepseek-v4-pro",
  "language": "en",
  "organization_id": "org-1",
  "workspace_id": "workspace-1",
  "execution_context": {
    "version": "v1",
    "run_id": "resp_1",
    "conversation_id": "conv-1",
    "sandbox_id": "00000000-0000-0000-0000-000000000001",
    "execution_workspace_id": "00000000-0000-0000-0000-000000000002",
    "gateway_url": "http://axiom/api/v1/runtime/runs/resp_1",
    "capability_token": "runtime-capability",
    "expires_at": 2000000000,
    "input_path": "/workspace/runs/resp_1/inputs",
    "work_path": "/workspace/runs/resp_1/work",
    "output_path": "/workspace/runs/resp_1/outputs",
    "capabilities": ["sandbox.files", "sandbox.commands"]
  },
  "execution_files": [],
  "runtime_gateway": {
    "run_id": "resp_1",
    "endpoint": "http://axiom/api/v1/runtime/runs/resp_1",
    "token": "runtime-capability",
    "token_type": "bearer",
    "expires_at": 2000000000,
    "workspace_id": "workspace-1",
    "capabilities": ["events", "artifacts"]
  },
  "discover_workspace_files": false
}
```

The response is `text/event-stream` and may contain:

```text
event: report.status
event: report.output_text.delta
event: report.usage
event: report.completed
```

Failures terminate with one typed `report.failed` event. Tool lifecycle events
and artifact finalization go directly to the request-scoped Runtime Gateway.

## Configuration

```bash
cp backend/.env.example backend/.env
```

Set the model credentials and optional Method Hub endpoint.

### LangSmith tracing

To trace both the streaming API workflow and local CLI runs, configure the
LangSmith credentials and enable tracing:

```bash
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your-langsmith-api-key
LANGCHAIN_PROJECT=gen-report
```

Remote runs use a `genreport-report-workflow` root; CLI runs use
`genreport-local-report-workflow`. Each has nested traces for discovery, input
or workspace preparation, asset materialization, prompt construction, every LLM
round, tool call, and artifact finalization. The traces intentionally preserve
full prompts, tool inputs and results, generated-artifact metadata, and errors,
so restrict LangSmith project access appropriately.

### Local CLI mode

For a host-local report run, set `LOCAL_MODE=true` in `backend/.env`, then
create a YAML config from `backend/config.local.example.yaml`:

```bash
cd backend
python -m app.local_report --config config.local.yaml
```

Relative entries in `files` resolve from the YAML file's directory. The CLI
copies them into `data/workspaces/<run_id>/inputs` and writes generated report
artifacts under `data/workspaces/<run_id>/outputs`.

`model`, `openai_api_key`, and `openai_base_url` are loaded directly from the
local YAML config for each CLI run. Keep this file out of version control.

Local mode runs model-generated Python and shell commands as the invoking host
user. The workspace structure constrains GenReport paths, but it is not an OS
or container sandbox; use the AXIOM Sandbox Service for untrusted requests.

## Run

```bash
./start.sh
curl http://localhost:8011/health
./stop.sh
```

Compose retains only the dependency cache volume. It does not mount a data or
database directory.

## Test

```bash
cd backend
python -m unittest discover -s tests -v
```

### Related files without report generation

`POST /api/v1/reports:discover-related` accepts `organization_id`, `workspace_id`,
and 1–20 `files` with `document_id`, `object_key`, and `bucket`. The internal caller
must validate workspace access and forward `X-Axiom-User-Authorization` and
`X-Org-ID`, as in the report flow. The engine validates seed corpus metadata,
uses indexed overviews as context for the existing DiscoveryAgent, and returns
`files` containing `document_id`, `object_key`, `bucket`, and `filename`.
It excludes seed documents and creates no report or sandbox. The public
intelligence-service endpoint verifies returned source ownership before exposing
results to the browser.

Report generation and related-file search share the overview loader, per-document
context formatting (2,000 characters per document), and relevance prompt in
`app/services/discovery_context.py`. When a staged primary source has no corpus
document ID, report preparation resolves it by its original object key within the
current workspace and carries the resolved ID into prepared inputs. Invalid or
unindexed resolutions do not fall back to a filename-only discovery query. The
report path no longer truncates the combined seed context to 6,000 characters.

### Organization tool bindings

Before binding discovery tools, GenReport intersects its retrieval-tool allowlist
with the organization's registered tools from Authz. Set `TOOL_SUBSCRIPTIONS_API_URL`
to the gateway endpoint (default
`http://host.docker.internal:8007/authz-service/api/v1/authz/me/tool-subscriptions`).
The user bearer identifies the organization; the response must match the requested
organization. Empty registrations bind no tools; lookup failures never enable the
full catalog as a fallback. Raw MethodHub listing and internal preparation calls
are unchanged. Register retrieval tools in the Tools UI before running discovery.
