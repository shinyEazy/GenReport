import json

import httpx
import pytest

from app.services.axiom_model_service import AxiomModelService


@pytest.mark.asyncio
async def test_axiom_service_resolves_report_task_and_translates_stream():
    requests: list[httpx.Request] = []
    body = (
        'event: response.started\ndata: {"request_id":"r","sequence":1}\n\n'
        'event: text.delta\ndata: {"request_id":"r","sequence":2,"text":"hello"}\n\n'
        'event: tool_call.delta\ndata: {"request_id":"r","sequence":3,"index":0,"call_id":"c","name":"lookup","arguments":"{}"}\n\n'
        'event: usage\ndata: {"request_id":"r","sequence":4,"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}\n\n'
        'event: response.completed\ndata: {"request_id":"r","sequence":5}\n\n'
    )

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("task-resolutions"):
            return httpx.Response(200, json={"config_revision": 7, "resolutions": [{
                "resolution_id": "resolved", "task_id": "report.generate",
                "provider_id": "p", "model_id": "m", "config_revision": 7,
                "assignment_revision": 2,
            }]})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        service = AxiomModelService(
            base_url="http://model-service/api/v2", token="service-token", organization_id="org",
            workspace_id="workspace", run_id="run", trace_id="trace", http=http,
        )
        events = [event async for event in service.stream_chat(
            [{"role": "user", "content": "hi"}], tool_definitions=[]
        )]

    assert [event["type"] for event in events] == ["delta", "tool_call", "done"]
    assert events[-1]["usage"]["total_tokens"] == 5
    assert json.loads(requests[0].content) == {"run_id": "run", "task_ids": ["report.generate"]}
    inference = json.loads(requests[1].content)
    assert inference["resolution_id"] == "resolved"
    assert inference["tools"] == []
    assert "model" not in inference
    assert requests[0].headers["Authorization"] == "Bearer service-token"


@pytest.mark.asyncio
async def test_axiom_service_fails_closed_when_resolution_fails():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(409, json={"code": "task_unconfigured"})
    )) as http:
        service = AxiomModelService(
            base_url="http://model-service/api/v2", token="token", organization_id="org",
            workspace_id="workspace", run_id="run", http=http,
        )
        events = [event async for event in service.stream_chat([], tool_definitions=[])]
    assert events == [{"type": "error", "content": "Model Service request failed (task_unconfigured)"}]


@pytest.mark.asyncio
async def test_axiom_service_uses_selected_model_resource_without_profile_resolution():
    requests: list[httpx.Request] = []
    body = 'event: response.completed\ndata: {"request_id":"r"}\n\n'

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        service = AxiomModelService(
            base_url="http://model-service/api/v2",
            token="service-token",
            organization_id="org",
            workspace_id="workspace",
            run_id="run",
            http=http,
        )
        events = [
            event
            async for event in service.stream_chat(
                [{"role": "user", "content": "hi"}],
                model="model-resource-selected",
                tool_definitions=[],
            )
        ]

    assert [event["type"] for event in events] == ["done"]
    assert len(requests) == 1
    inference = json.loads(requests[0].content)
    assert inference["model_resource_id"] == "model-resource-selected"
    assert "resolution_id" not in inference
