from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool


@dataclass(frozen=True, slots=True)
class MethodHubTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


class MethodHubClient:
    def __init__(
        self,
        endpoint: str,
        *,
        authorization: str | None = None,
        trace_id: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        endpoint = endpoint.strip()
        if not endpoint:
            raise ValueError("Method Hub endpoint is required")
        self.endpoint = endpoint
        self.authorization = authorization.strip() if authorization else None
        self.trace_id = trace_id.strip() if trace_id else None
        self.organization_id = organization_id.strip() if organization_id else None

    async def list_tools(self) -> list[MethodHubTool]:
        async with self._session() as session:
            response = await session.list_tools()
        return [self._normalize_tool(item) for item in response.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        async with self._session() as session:
            response = await session.call_tool(name, arguments)
        if bool(getattr(response, "isError", False)):
            raise RuntimeError(
                self._response_text(response) or f"Method Hub tool failed: {name}"
            )
        structured = getattr(response, "structuredContent", None)
        if structured is None:
            structured = getattr(response, "structured_content", None)
        if structured is not None:
            return structured
        text = self._response_text(response)
        if not text:
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    async def create_langchain_tools(
        self,
        *,
        allowed_names: Collection[str],
        organization_id: str,
        workspace_id: str,
    ) -> list[BaseTool]:
        allowed = set(allowed_names)
        definitions = [tool for tool in await self.list_tools() if tool.name in allowed]
        return [
            self._create_langchain_tool(
                definition,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
            for definition in definitions
        ]

    def _create_langchain_tool(
        self,
        definition: MethodHubTool,
        *,
        organization_id: str,
        workspace_id: str,
    ) -> BaseTool:
        async def invoke(**arguments: Any) -> Any:
            scoped = _inject_supported_scope(
                arguments,
                definition=definition,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
            return await self.call_tool(definition.name, scoped)

        return StructuredTool.from_function(
            coroutine=invoke,
            name=definition.name,
            description=(
                definition.description or f"Method Hub tool {definition.name}"
            ),
            args_schema=definition.input_schema,
            infer_schema=False,
        )

    def _session(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def session_context():
            ClientSession, streamable_http_client = _load_mcp_client()
            async with httpx.AsyncClient(
                headers=self._request_headers(),
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, read=300.0),
            ) as client:
                async with streamable_http_client(
                    self.endpoint,
                    http_client=client,
                ) as streams:
                    read_stream, write_stream = streams[:2]
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        yield session

        return session_context()

    def _request_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.authorization:
            headers["Authorization"] = self.authorization
        if self.trace_id:
            headers["X-Trace-ID"] = self.trace_id
        if self.organization_id:
            headers["X-Org-ID"] = self.organization_id
        return headers

    @staticmethod
    def _normalize_tool(value: Any) -> MethodHubTool:
        name = str(getattr(value, "name", "") or "").strip()
        if not name:
            raise RuntimeError("Method Hub returned a tool without a name")
        input_schema = getattr(value, "inputSchema", None)
        if input_schema is None:
            input_schema = getattr(value, "input_schema", {})
        return MethodHubTool(
            name=name,
            description=str(getattr(value, "description", "") or ""),
            input_schema=input_schema if isinstance(input_schema, dict) else {},
        )

    @staticmethod
    def _response_text(response: Any) -> str:
        return "\n".join(
            str(text)
            for item in getattr(response, "content", []) or []
            if (text := getattr(item, "text", None)) is not None
        )


def _load_mcp_client():
    try:
        from mcp import ClientSession

        streamable_http = import_module("mcp.client.streamable_http")
    except ImportError as exc:
        raise RuntimeError(
            "The mcp package is required for report file discovery"
        ) from exc
    factory = getattr(streamable_http, "streamable_http_client", None)
    if factory is None:
        factory = getattr(streamable_http, "streamablehttp_client", None)
    if factory is None:
        raise RuntimeError("The installed mcp package has no streamable HTTP client")
    return ClientSession, factory


def _inject_supported_scope(
    arguments: dict[str, Any],
    *,
    definition: MethodHubTool,
    organization_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    scoped = dict(arguments)
    properties = definition.input_schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    if "organization_id" in properties:
        scoped["organization_id"] = organization_id
    if "workspace_id" in properties:
        scoped["workspace_id"] = workspace_id
    if "workspace_ids" in properties:
        scoped["workspace_ids"] = [workspace_id]
    return scoped
