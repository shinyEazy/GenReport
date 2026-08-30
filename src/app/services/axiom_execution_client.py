from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from typing import Any
from urllib.parse import quote

import httpx

from app.contracts.report_execution import ExecutionContextRequest

FILE_CHUNK_BYTES = 8 * 1024 * 1024
TERMINAL_COMMAND_STATUSES = {"succeeded", "failed", "timed_out", "cancelled"}


class AxiomExecutionClient:
    def __init__(self, context: ExecutionContextRequest) -> None:
        self.context = context
        self._http = httpx.AsyncClient(timeout=120.0)
        self._recovery_used = False

    async def close(self) -> None:
        await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.context.capability_token}"}

    def _url(self, suffix: str) -> str:
        return (
            f"{str(self.context.gateway_url).rstrip('/')}/sandbox/{suffix.lstrip('/')}"
        )

    async def execute(
        self,
        *,
        language: str,
        code: str,
        cwd: str,
        timeout_seconds: int = 120,
        dependencies: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._request(
                "POST",
                "commands",
                json={
                    "language": language,
                    "code": code,
                    "cwd": cwd,
                    "timeout_seconds": timeout_seconds,
                    "dependencies": dependencies or [],
                },
            )
            command_id = response.json()["command_id"]
            deadline = time.monotonic() + timeout_seconds + 30
            while True:
                current = await self._request(
                    "GET", f"commands/{command_id}", allow_recovery=False
                )
                payload = current.json()
                if payload.get("status") in TERMINAL_COMMAND_STATUSES:
                    return payload
                if time.monotonic() >= deadline:
                    await self.cancel(command_id)
                    raise TimeoutError("AXIOM sandbox command timed out")
                await asyncio.sleep(0.25)
        except httpx.HTTPStatusError as exc:
            if self._is_recoverable(exc) and not self._recovery_used:
                await self.recover()
                return await self.execute(
                    language=language,
                    code=code,
                    cwd=cwd,
                    timeout_seconds=timeout_seconds,
                    dependencies=dependencies,
                )
            raise

    async def cancel(self, command_id: str) -> None:
        await self._request("POST", f"commands/{command_id}/cancel")

    async def list_files(self, path: str) -> list[dict[str, Any]]:
        response = await self._request("GET", "files", params={"path": path})
        payload = response.json()
        return [item for item in payload if isinstance(item, dict)]

    async def iter_read(
        self, path: str, *, chunk_size: int = FILE_CHUNK_BYTES
    ) -> AsyncIterator[bytes]:
        offset = 0
        encoded_path = quote(path.removeprefix("/workspace/"), safe="/")
        while True:
            response = await self._request(
                "GET",
                f"files/{encoded_path}",
                params={"offset": offset, "limit": chunk_size},
            )
            chunk = response.content
            if not chunk:
                return
            yield chunk
            offset += len(chunk)
            if len(chunk) < chunk_size:
                return

    async def read_file(self, path: str) -> bytes:
        content = bytearray()
        async for chunk in self.iter_read(path):
            content.extend(chunk)
        return bytes(content)

    async def write_chunks(
        self,
        path: str,
        chunks: AsyncIterable[bytes] | Iterable[bytes],
    ) -> int:
        encoded_path = quote(path.removeprefix("/workspace/"), safe="/")
        offset = 0
        first = True

        async def put(chunk: bytes) -> None:
            nonlocal offset, first
            if len(chunk) > FILE_CHUNK_BYTES:
                raise ValueError("file chunk exceeds 8 MiB")
            await self._request(
                "PUT",
                f"files/{encoded_path}",
                params={"offset": offset, "truncate": first},
                content=chunk,
            )
            offset += len(chunk)
            first = False

        if isinstance(chunks, AsyncIterable):
            async for chunk in chunks:
                await put(chunk)
        else:
            for chunk in chunks:
                await put(chunk)
        if first:
            await put(b"")
        return offset

    async def write_file(self, path: str, content: bytes | str) -> int:
        value = content.encode("utf-8") if isinstance(content, str) else content
        return await self.write_chunks(
            path,
            (
                value[offset : offset + FILE_CHUNK_BYTES]
                for offset in range(0, len(value), FILE_CHUNK_BYTES)
            ),
        )

    async def delete_file(self, path: str) -> None:
        encoded_path = quote(path.removeprefix("/workspace/"), safe="/")
        await self._request("DELETE", f"files/{encoded_path}")

    async def finalize(
        self, entries: list[dict[str, Any]], *, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._request(
                    "POST",
                    "finalize",
                    json={"entries": entries, "workspace_id": workspace_id},
                    allow_recovery=attempt == 0,
                )
                artifacts = response.json().get("artifacts")
                return [item for item in artifacts or [] if isinstance(item, dict)]
            except (httpx.HTTPError, RuntimeError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError("AXIOM artifact finalization failed") from last_error

    async def renew(self) -> None:
        response = await self._direct_request("POST", "token/renew")
        payload = response.json()
        if isinstance(payload.get("execution_context"), dict):
            self.context = ExecutionContextRequest.model_validate(
                payload["execution_context"]
            )
            self._recovery_used = True
        else:
            self.context = self.context.model_copy(
                update={
                    "capability_token": payload["capability_token"],
                    "expires_at": payload["expires_at"],
                }
            )

    async def recover(self) -> None:
        response = await self._direct_request("POST", "recover")
        payload = response.json()
        self.context = ExecutionContextRequest.model_validate(
            payload["execution_context"]
        )
        self._recovery_used = True

    async def _request(
        self,
        method: str,
        suffix: str,
        *,
        allow_recovery: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        if self.context.expires_at - int(time.time()) < 60 and suffix != "token/renew":
            await self.renew()
        try:
            return await self._direct_request(method, suffix, **kwargs)
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = str(exc.response.json().get("detail") or "")
            except Exception:
                detail = exc.response.text
            recoverable = self._is_recoverable(exc, detail=detail)
            if allow_recovery and recoverable and not self._recovery_used:
                await self.recover()
                return await self._request(
                    method, suffix, allow_recovery=False, **kwargs
                )
            raise

    @staticmethod
    def _is_recoverable(
        exc: httpx.HTTPStatusError, *, detail: str | None = None
    ) -> bool:
        if detail is None:
            try:
                detail = str(exc.response.json().get("detail") or "")
            except Exception:
                detail = exc.response.text
        return (
            exc.response.status_code in {404, 409, 410} and "sandbox" in detail.lower()
        )

    async def _direct_request(
        self, method: str, suffix: str, **kwargs: Any
    ) -> httpx.Response:
        response = await self._http.request(
            method,
            self._url(suffix),
            headers=self._headers(),
            **kwargs,
        )
        response.raise_for_status()
        return response
