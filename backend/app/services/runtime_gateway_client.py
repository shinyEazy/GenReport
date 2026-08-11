from __future__ import annotations

from typing import Any

import httpx


class RuntimeGatewayClient:
    def __init__(self, timeout_seconds: float = 120.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def mirror_artifact_refs(
        self,
        runtime_gateway: dict[str, Any] | None,
        artifacts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not runtime_gateway or not artifacts:
            return []
        endpoint = runtime_gateway.get("endpoint")
        token = runtime_gateway.get("token")
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            return []
        if not isinstance(token, str) or not token:
            return []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{endpoint.rstrip('/')}/artifact-refs",
                json={
                    "workspace_id": runtime_gateway.get("workspace_id"),
                    "artifacts": artifacts,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            payload = response.json()
        mirrored = payload.get("artifacts")
        if not isinstance(mirrored, list):
            return []
        return [item for item in mirrored if isinstance(item, dict)]

    async def record_event(
        self,
        runtime_gateway: dict[str, Any] | None,
        event_type: str,
        payload: dict[str, Any],
        *,
        status: str = "completed",
    ) -> None:
        endpoint, token = self._endpoint_and_token(runtime_gateway)
        if endpoint is None or token is None:
            return
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{endpoint}/events",
                json={
                    "event_type": event_type,
                    "payload": payload,
                    "status": status,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()

    def _endpoint_and_token(
        self,
        runtime_gateway: dict[str, Any] | None,
    ) -> tuple[str | None, str | None]:
        if not runtime_gateway:
            return None, None
        endpoint = runtime_gateway.get("endpoint")
        token = runtime_gateway.get("token")
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            return None, None
        if not isinstance(token, str) or not token:
            return None, None
        return endpoint.rstrip("/"), token
