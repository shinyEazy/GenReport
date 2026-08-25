from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

REMOTE_WORKFLOW_TAGS = ("genreport", "report-workflow", "remote")
LOCAL_WORKFLOW_TAGS = ("genreport", "report-workflow", "local")


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
        process_inputs=_normalize_mapping,
        process_outputs=_normalize_payload,
    )(function)


def _tracing_enabled() -> bool:
    return any(
        os.getenv(name, "").strip().lower() == "true"
        for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")
    )


def _normalize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _normalize_payload(item)
        for key, item in value.items()
        if key != "self"
    }


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
