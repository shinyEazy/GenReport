from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SUPPORTED_CONFIG_KEYS = {
    "query",
    "files",
    "model",
    "openai_api_key",
    "openai_base_url",
    "language",
    "run_id",
}


class LocalReportConfigError(ValueError):
    """Raised when a local report configuration cannot be used safely."""


@dataclass(frozen=True)
class LocalReportConfig:
    query: str
    files: list[Path]
    model: str | None
    openai_api_key: str
    openai_base_url: str
    language: str
    run_id: str | None


def load_local_report_config(path: Path) -> LocalReportConfig:
    config_path = path.expanduser().resolve()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LocalReportConfigError(f"Unable to read config: {exc}") from exc
    if not isinstance(raw, dict):
        raise LocalReportConfigError("Config root must be a mapping.")

    unknown_keys = set(raw) - SUPPORTED_CONFIG_KEYS
    if unknown_keys:
        raise LocalReportConfigError(
            f"Unsupported config keys: {', '.join(sorted(unknown_keys))}."
        )

    query = _required_text(raw, "query")
    files = _resolve_files(raw.get("files"), config_path.parent)
    model = _optional_text(raw, "model")
    openai_api_key = _required_text(raw, "openai_api_key")
    openai_base_url = _required_text(raw, "openai_base_url")
    language = _optional_text(raw, "language") or "auto"
    run_id = _optional_text(raw, "run_id")
    if run_id is not None and not RUN_ID_PATTERN.fullmatch(run_id):
        raise LocalReportConfigError("run_id contains unsupported characters.")
    return LocalReportConfig(
        query=query,
        files=files,
        model=model,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        language=language,
        run_id=run_id,
    )


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = _optional_text(raw, key)
    if value is None:
        raise LocalReportConfigError(f"{key} is required.")
    return value


def _optional_text(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise LocalReportConfigError(f"{key} must be a string.")
    return value.strip() or None


def _resolve_files(value: object, config_dir: Path) -> list[Path]:
    if not isinstance(value, list) or not value:
        raise LocalReportConfigError("files must be a non-empty list.")

    resolved: list[Path] = []
    names: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise LocalReportConfigError("files must contain non-empty paths.")
        source = Path(item).expanduser()
        if not source.is_absolute():
            source = config_dir / source
        source = source.resolve()
        if not source.is_file():
            raise LocalReportConfigError(f"Input file is unavailable: {source}")
        if source.name in names:
            raise LocalReportConfigError(
                f"Input file names must be unique: {source.name}"
            )
        names.add(source.name)
        resolved.append(source)
    return resolved
