from __future__ import annotations

import json
from builtins import BaseExceptionGroup
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

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
from app.services.report_tracing import trace_operation


REPORT_RETRIEVAL_TOOL_NAMES = {
    "corpus_retrieve_context",
    "corpus_vector_search",
    "corpus_bm25_search",
    "corpus_get_file_ingested_data",
}
MAX_REPORT_RETRIEVAL_TOOL_CALLS = 5

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


def _limit_retrieval_tool_calls(max_calls: int = MAX_REPORT_RETRIEVAL_TOOL_CALLS):
    retrieval_calls = 0

    @wrap_tool_call(name="LimitReportRetrievalToolCalls")
    async def limit_retrieval_tool_calls(
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        nonlocal retrieval_calls
        tool_call = request.tool_call
        if tool_call.get("name") not in REPORT_RETRIEVAL_TOOL_NAMES:
            return await handler(request)
        if retrieval_calls >= max_calls:
            return _ToolResult(
                content=json.dumps(
                    {
                        "success": False,
                        "error_type": "RetrievalLimitExceeded",
                        "error": "Discovery has already used its one retrieval call.",
                        "instruction": (
                            "do not call retrieval tools again. Use the retrieved "
                            "results and return the structured document_ids selection now."
                        ),
                    },
                    ensure_ascii=False,
                ),
                tool_call_id=str(tool_call.get("id", "unknown")),
                name=tool_call.get("name"),
                status="error",
            )
        retrieval_calls += 1
        return await handler(request)

    return limit_retrieval_tool_calls


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
            middleware=[_limit_retrieval_tool_calls(), _recover_tool_errors],
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

        model_config: dict[str, Any] = {
            "api_key": self.api_key,
            "base_url": self.base_url or None,
            "model": model,
            "temperature": 0,
        }
        if _is_openrouter_url(self.base_url):
            # Discovery uses a structured response, which deepagents implements
            # with forced tool choice. Alibaba rejects that combination while
            # Qwen is in thinking mode, so disable reasoning for this call.
            model_config["reasoning"] = {"effort": "none"}

        return ChatOpenAI(
            **model_config,
        )


def _is_openrouter_url(base_url: str) -> bool:
    hostname = (urlparse(base_url).hostname or "").lower()
    return hostname == "openrouter.ai" or hostname.endswith(".openrouter.ai")


def _trace_discovery_call(
    function: Callable[..., Awaitable[list[str]]],
) -> Callable[..., Awaitable[list[str]]]:
    return trace_operation(
        function,
        name="genreport-file-discovery",
        run_type="chain",
        tags=["genreport", "file-discovery"],
    )


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
        "Identify the smallest useful set of existing document_ids needed for "
        "the requested report—usually 1–3 documents and never more than 5. "
        "You have exactly one retrieval call: use corpus_retrieve_context first. "
        "After that call returns, do not call a retrieval tool again; use its "
        "results to select the relevant document ids. Select additional ids only "
        "when they provide material, complementary evidence; do not add loosely "
        "related files. Do not inspect full ingested data. "
        "Return only document ids supported by tool results; never invent ids. "
        "For normal chit-chat or when no relevant document exists, return an "
        "empty document_ids list."
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
