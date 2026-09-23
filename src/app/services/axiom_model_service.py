"""Request-scoped GenReport adapter for AXIOM Model Service.

This stays local because GenReport is deployed from an independent checkout. It
never receives provider credentials and has no direct-provider fallback.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx


class AxiomModelService:
    default_model = "report.generate"

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        organization_id: str,
        workspace_id: str,
        run_id: str,
        trace_id: str | None = None,
        http: httpx.AsyncClient | None = None,
        profile_mode: bool = False,
    ) -> None:
        if not base_url or not token:
            raise ValueError("AXIOM Model Service URL and service token are required")
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "X-Org-ID": organization_id,
            "X-Workspace-ID": workspace_id,
            "X-Consumer-Service": "genreport",
            "X-Run-ID": run_id,
        }
        if trace_id:
            self._headers["X-Trace-ID"] = trace_id
        self._run_id = run_id
        self._revision: int | None = None
        self._resolutions: dict[str, str] = {}
        self.default_model = "report.generate"
        self._http = http
        self._profile_mode = profile_mode

    async def _resolve(self, client: httpx.AsyncClient, task_id: str) -> str:
        if task_id in self._resolutions:
            return self._resolutions[task_id]
        # Profile resolution is the canonical AXIOM path. Keep the task
        # endpoint as a narrow compatibility path for older Model Service
        # deployments during the migration window.
        if self._profile_mode:
            profile_response = await client.post(
                f"{self._base_url}/consumer-model-resolutions",
                json={
                    "consumer_id": "genreport",
                    "roles": ["llm"],
                    "run_id": self._run_id,
                    "workspace_id": self._headers.get("X-Workspace-ID"),
                    **(
                        {"config_revision": self._revision}
                        if self._revision is not None
                        else {}
                    ),
                },
                headers=self._headers,
            )
            if profile_response.is_success and not profile_response.headers.get(
                "content-type", ""
            ).startswith("text/event-stream"):
                data = self._checked_json(profile_response)
                resolutions = data.get("resolutions")
                if not isinstance(resolutions, list) or not resolutions:
                    raise RuntimeError(
                        "Model Service returned an invalid consumer profile resolution"
                    )
                resolution = resolutions[0]
                if not isinstance(resolution, dict) or not isinstance(
                    resolution.get("resolution_id"), str
                ):
                    raise RuntimeError(
                        "Model Service returned an invalid consumer profile resolution"
                    )
                revision = data.get("config_revision")
                if not isinstance(revision, int):
                    raise RuntimeError(
                        "Model Service returned an invalid config revision"
                    )
                self._revision = revision
                self._resolutions[task_id] = resolution["resolution_id"]
                model_id = resolution.get("model_id")
                if isinstance(model_id, str) and model_id:
                    self.default_model = model_id
                return resolution["resolution_id"]
            if profile_response.status_code not in {
                404,
                405,
            } and profile_response.headers.get("content-type", "").startswith(
                "application/json"
            ):
                self._checked_json(profile_response)
        body: dict[str, Any] = {"run_id": self._run_id, "task_ids": [task_id]}
        if self._revision is not None:
            body["config_revision"] = self._revision
        response = await client.post(
            f"{self._base_url}/task-resolutions", json=body, headers=self._headers
        )
        data = self._checked_json(response)
        resolutions = data.get("resolutions")
        if not isinstance(resolutions, list) or len(resolutions) != 1:
            raise RuntimeError("Model Service returned an invalid task resolution")
        resolution = resolutions[0]
        if not isinstance(resolution, dict) or not isinstance(
            resolution.get("resolution_id"), str
        ):
            raise RuntimeError("Model Service returned an invalid task resolution")
        revision = data.get("config_revision")
        if not isinstance(revision, int):
            raise RuntimeError("Model Service returned an invalid config revision")
        self._revision = revision
        self._resolutions[task_id] = resolution["resolution_id"]
        model_id = resolution.get("model_id")
        if isinstance(model_id, str) and model_id:
            self.default_model = model_id
        return resolution["resolution_id"]

    @property
    def config_revision(self) -> int | None:
        return self._revision

    @staticmethod
    def _checked_json(response: httpx.Response) -> dict[str, Any]:
        if response.is_success:
            data = response.json()
            if isinstance(data, dict):
                return data
        code = "model_service_error"
        try:
            data = response.json()
            if isinstance(data, dict) and isinstance(data.get("code"), str):
                code = data["code"]
        except ValueError:
            pass
        raise RuntimeError(f"Model Service request failed ({code})")

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tool_definitions: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        *,
        task_id: str = "report.generate",
        response_format: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        if tool_definitions is None:
            raise ValueError("tool_definitions are required for report execution")
        owns_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=120)
        tool_calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] | None = None
        try:
            selected_model_resource_id = model.strip() if isinstance(model, str) else ""
            if selected_model_resource_id:
                body = {
                    "model_resource_id": selected_model_resource_id,
                    "messages": messages,
                    "tools": tool_definitions,
                    "tool_choice": tool_choice,
                    "stream": True,
                }
            else:
                resolution_id = await self._resolve(client, task_id)
                body = {
                    "resolution_id": resolution_id,
                    "messages": messages,
                    "tools": tool_definitions,
                    "tool_choice": tool_choice,
                    "stream": True,
                }
            if response_format is not None:
                body["response_format"] = response_format
            async with client.stream(
                "POST",
                f"{self._base_url}/inference/responses",
                json=body,
                headers=self._headers,
            ) as response:
                if not response.is_success:
                    await response.aread()
                    self._checked_json(response)
                if not response.headers.get("content-type", "").startswith(
                    "text/event-stream"
                ):
                    raise RuntimeError("Model Service did not return an event stream")
                event_type = ""
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                        if event_type == "text.delta" and isinstance(
                            data.get("text"), str
                        ):
                            yield {"type": "delta", "content": data["text"]}
                        elif event_type == "tool_call.delta":
                            index = int(data.get("index", 0))
                            call = tool_calls.setdefault(
                                index,
                                {
                                    "id": data.get("call_id") or f"tool_{index}",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                },
                            )
                            if data.get("name"):
                                call["function"]["name"] = data["name"]
                            if data.get("arguments"):
                                call["function"]["arguments"] += data["arguments"]
                        elif event_type == "usage" and isinstance(
                            data.get("usage"), dict
                        ):
                            usage = data["usage"]
                        elif event_type == "response.failed":
                            yield {
                                "type": "error",
                                "content": "Model Service stream failed",
                            }
                            return
                        elif event_type == "response.completed":
                            for call in tool_calls.values():
                                yield {"type": "tool_call", "tool_call": call}
                            yield {
                                "type": "done",
                                "content": "",
                                "tool_calls": list(tool_calls.values()),
                                "thinking": "",
                                "usage": usage,
                            }
                            return
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            yield {"type": "error", "content": str(exc)}
        finally:
            if owns_client:
                await client.aclose()
