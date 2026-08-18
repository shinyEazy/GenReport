from __future__ import annotations

import json
import os
from builtins import BaseExceptionGroup
from collections.abc import Awaitable, Callable
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents._models import get_model_identifier, get_model_provider
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage as _ToolResult
from pydantic import BaseModel, Field

from app.services.method_hub_client import MethodHubClient


REPORT_RETRIEVAL_TOOL_NAMES = {
    "corpus_retrieve_context",
    "corpus_vector_search",
    "corpus_bm25_search",
    "corpus_get_file_ingested_data",
}

_HIDDEN_DEEP_AGENT_TOOLS = frozenset(
    {
        "write_todos",
        "ls",
        "read_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
        "write_file",
    }
)


class ReportArtifactSelection(BaseModel):
    document_ids: list[str] = Field(
        default_factory=list,
        description="Existing Method Hub document IDs selected for the report",
    )


@wrap_tool_call(name="RecoverReportRetrievalToolErrors")
async def _recover_tool_errors(
    request: Any,
    handler: Callable[[Any], Awaitable[Any]],
) -> Any:
    try:
        return await handler(request)
    except Exception as exc:
        primary = _primary_error(exc)
        tool_call = request.tool_call
        return _ToolResult(
            content=json.dumps(
                {
                    "success": False,
                    "error_type": type(primary).__name__,
                    "error": str(primary),
                    "instruction": (
                        "Try another available retrieval tool or correct the "
                        "arguments; do not repeat the identical failing call."
                    ),
                },
                ensure_ascii=False,
            ),
            tool_call_id=str(tool_call.get("id", "unknown")),
            name=tool_call.get("name"),
            status="error",
        )


def _primary_error(exc: BaseException) -> BaseException:
    return _exception_leaves(exc)[0]


def _exception_leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [
            leaf
            for nested in exc.exceptions
            for leaf in _exception_leaves(nested)
        ]
    return [exc]


AgentFactory = Callable[..., Any]
ModelFactory = Callable[[str | None], Any]
ProfileRegistrar = Callable[[Any], None]
TraceFactory = Callable[
    [Callable[..., Awaitable[list[str]]]],
    Callable[..., Awaitable[list[str]]],
]


class DiscoveryAgent:
    def __init__(
        self,
        *,
        method_hub: MethodHubClient,
        api_key: str = "",
        base_url: str = "",
        default_model: str = "",
        model_factory: ModelFactory | None = None,
        agent_factory: AgentFactory = create_deep_agent,
        profile_registrar: ProfileRegistrar | None = None,
        trace_factory: TraceFactory | None = None,
        max_artifacts: int = 100,
        max_rounds: int = 100,
    ) -> None:
        self.method_hub = method_hub
        self.api_key = api_key
        self.base_url = base_url
        self.default_model = default_model
        self.model_factory = model_factory or self._build_model
        self.agent_factory = agent_factory
        self.profile_registrar = profile_registrar or _register_minimal_profile
        self.max_artifacts = max_artifacts
        self.max_rounds = max_rounds
        self._discover_traced = (trace_factory or _trace_discovery_call)(
            self._discover_impl
        )

    async def discover(
        self,
        *,
        query: str,
        organization_id: str,
        workspace_id: str,
        model: str | None = None,
    ) -> list[str]:
        return await self._discover_traced(
            query=query,
            organization_id=organization_id,
            workspace_id=workspace_id,
            model=model,
        )

    async def _discover_impl(
        self,
        *,
        query: str,
        organization_id: str,
        workspace_id: str,
        model: str | None = None,
    ) -> list[str]:
        tools = await self.method_hub.create_langchain_tools(
            allowed_names=REPORT_RETRIEVAL_TOOL_NAMES,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        if not tools:
            raise RuntimeError("Method Hub has no report retrieval tools available")

        llm = self.model_factory(model or self.default_model or None)
        self.profile_registrar(llm)
        agent = self.agent_factory(
            model=llm,
            tools=tools,
            middleware=[_recover_tool_errors],
            system_prompt=_system_prompt(workspace_id),
            subagents=[],
            response_format=ReportArtifactSelection,
            name="report-file-discovery",
        )
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": query}]},
            config={"recursion_limit": self.max_rounds * 2 + 1},
        )
        selection = (
            result.get("structured_response") if isinstance(result, dict) else None
        )
        if isinstance(selection, dict):
            selection = ReportArtifactSelection.model_validate(selection)
        if not isinstance(selection, ReportArtifactSelection):
            raise RuntimeError("Report file discovery returned no structured selection")
        return _deduplicate_document_ids(
            selection.document_ids,
            limit=self.max_artifacts,
        )

    def _build_model(self, model: str | None) -> Any:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for report file discovery")
        if not model:
            raise RuntimeError("A model is required for report file discovery")

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=self.api_key,
            base_url=self.base_url or None,
            model=model,
            temperature=0,
        )


def _trace_discovery_call(
    function: Callable[..., Awaitable[list[str]]],
) -> Callable[..., Awaitable[list[str]]]:
    if os.getenv("LANGCHAIN_TRACING_V2", "").strip().lower() != "true":
        return function
    try:
        from langsmith import traceable
    except ImportError:
        return function
    return traceable(
        name="genreport-file-discovery",
        run_type="chain",
        project_name=os.getenv("LANGCHAIN_PROJECT") or "gen-report",
        tags=["genreport", "file-discovery"],
    )(function)


def _deduplicate_document_ids(values: list[str], *, limit: int) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        document_id = value.strip() if isinstance(value, str) else ""
        if not document_id or document_id in seen:
            continue
        seen.add(document_id)
        selected.append(document_id)
        if len(selected) >= limit:
            break
    return selected


def _system_prompt(workspace_id: str) -> str:
    return (
        "Find 1 document_id needed for the requested. "
        "Use the available corpus retrieval tools. If semantic retrieval fails, "
        "use BM25 lexical retrieval. Return only document ID supported by tool "
        "results. "
        "RETURN 1 DOCUMENT ID ONLY. If request is normal chit chat then no need return. IF FOUND ANY DOCUMENT ID, RETURN 1 DIRECTLY."
    )


def _register_minimal_profile(model: Any) -> None:
    identifier = get_model_identifier(model)
    provider = get_model_provider(model)
    if not identifier:
        raise RuntimeError("Discovery model has no resolvable identifier")
    if ":" in identifier:
        profile_key = identifier
    elif provider:
        profile_key = f"{provider}:{identifier}"
    else:
        raise RuntimeError("Discovery model has no resolvable provider")
    register_harness_profile(
        profile_key,
        HarnessProfile(
            excluded_tools=_HIDDEN_DEEP_AGENT_TOOLS,
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )


__all__ = ["DiscoveryAgent", "ReportArtifactSelection"]
