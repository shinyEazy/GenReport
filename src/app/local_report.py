from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app.contracts.local_report import (
    LocalReportConfig,
    LocalReportConfigError,
    load_local_report_config,
)
from app.core.config import settings
from app.services.llm_service import LLMService
from app.services.local_report_runner import LocalReportRunError, LocalReportRunner


def _discover_folder_files(folder_path: Path) -> list[Path]:
    folder = folder_path.expanduser().resolve()
    if not folder.exists():
        raise LocalReportConfigError(f"Input folder is unavailable: {folder}")
    if not folder.is_dir():
        raise LocalReportConfigError(f"Input path is not a folder: {folder}")

    files = sorted(
        (path for path in folder.iterdir() if path.is_file()),
        key=lambda path: path.name,
    )
    if not files:
        raise LocalReportConfigError(f"Input folder contains no files: {folder}")
    return files


def _build_folder_config(
    folder_path: Path,
    query: str | None,
    runtime_settings: Any,
) -> LocalReportConfig:
    if query is None or not query.strip():
        raise LocalReportConfigError(
            "--query is required when --folder-path is provided."
        )
    return LocalReportConfig(
        query=query.strip(),
        files=_discover_folder_files(folder_path),
        model=runtime_settings.DEFAULT_MODEL,
        openai_api_key=runtime_settings.OPENAI_API_KEY,
        openai_base_url=runtime_settings.OPENAI_BASE_URL,
        language="auto",
        run_id=None,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    runner_factory: Callable[..., LocalReportRunner] = LocalReportRunner,
    settings_value: Any = None,
    llm_factory: Callable[[], Any] = LLMService,
) -> int:
    parser = argparse.ArgumentParser(description="Generate a report locally.")
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--config",
        type=Path,
        help="Path to report YAML config",
    )
    input_group.add_argument(
        "--folder-path",
        type=Path,
        help="Folder containing immediate input files",
    )
    parser.add_argument("--query", help="Report query for --folder-path mode")
    args = parser.parse_args(argv)
    runtime_settings = settings if settings_value is None else settings_value
    if not runtime_settings.LOCAL_MODE:
        print("error: local reports require LOCAL_MODE=true", file=sys.stderr)
        return 2
    if args.config is None and args.folder_path is None:
        parser.error("one of --config or --folder-path is required")
    if args.config is not None and args.query is not None:
        parser.error("--query can only be used with --folder-path")

    try:
        config = (
            load_local_report_config(args.config)
            if args.config is not None
            else _build_folder_config(args.folder_path, args.query, runtime_settings)
        )
        runner = runner_factory(
            settings=runtime_settings,
            llm_service=llm_factory(
                api_key=config.openai_api_key,
                base_url=config.openai_base_url,
            ),
        )
        result = asyncio.run(runner.run(config))
    except (LocalReportConfigError, LocalReportRunError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if result.output_text:
        print(result.output_text)
    print(f"Workspace: {result.workspace.run_root.resolve()}")
    print("Artifacts:")
    for artifact in result.artifacts:
        print(f"- {artifact['artifact_ref']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
