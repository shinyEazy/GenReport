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
