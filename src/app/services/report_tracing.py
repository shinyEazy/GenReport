from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

REMOTE_WORKFLOW_TAGS = ["genreport", "report-workflow", "remote"]
LOCAL_WORKFLOW_TAGS = ["genreport", "report-workflow", "local"]


def trace_operation(
    function: Callable[..., T],
    *,
    name: str,
    run_type: str = "chain",
    tags: list[str] | None = None,
) -> Callable[..., T]:
    if not _tracing_enabled():
        return function
    try:
        from langsmith import traceable
    except ImportError:
        return function
    return traceable(
        name=name,
        run_type=run_type,
        project_name=os.getenv("LANGCHAIN_PROJECT") or "gen-report",
        tags=list(tags or ()),
        reduce_fn=_reduce_llm_stream if run_type == "llm" else None,
        process_inputs=(
            _normalize_llm_inputs if run_type == "llm" else _normalize_mapping
        ),
        process_outputs=_normalize_payload,
    )(function)


def _tracing_enabled() -> bool:
    return os.getenv("LANGCHAIN_TRACING_V2", "").strip().lower() == "true"


def _normalize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _normalize_payload(item)
        for key, item in value.items()
        if key != "self"
    }


def _normalize_llm_inputs(value: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize_mapping(value)
    tools = normalized.pop("tool_definitions", None)
    if not isinstance(tools, list):
        return normalized

    normalized["tools"] = tools
    normalized["bound_tools"] = [
        str(function["name"])
        for tool in tools
        if isinstance(tool, Mapping)
        and isinstance(function := tool.get("function"), Mapping)
        and function.get("name")
    ]
    return normalized


def _reduce_llm_stream(chunks: list[Any]) -> Any:
    final_chunk = next(
        (
            chunk
            for chunk in reversed(chunks)
            if isinstance(chunk, Mapping) and chunk.get("type") == "done"
        ),
        None,
    )
    if isinstance(final_chunk, Mapping):
        content = str(final_chunk.get("content") or "")
        if content:
            return content
        tool_calls = final_chunk.get("tool_calls")
        if tool_calls:
            return {"tool_calls": tool_calls}

    content = "".join(
        str(chunk.get("content") or "")
        for chunk in chunks
        if isinstance(chunk, Mapping) and chunk.get("type") == "delta"
    )
    return content


def _normalize_payload(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize_payload(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _normalize_payload(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _normalize_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalize_payload(item) for item in value]
    return value
