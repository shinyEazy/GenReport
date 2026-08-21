from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app.contracts.local_report import LocalReportConfigError, load_local_report_config
from app.core.config import settings
from app.services.llm_service import LLMService
from app.services.local_report_runner import LocalReportRunError, LocalReportRunner


def main(
    argv: Sequence[str] | None = None,
    *,
    runner_factory: Callable[..., LocalReportRunner] = LocalReportRunner,
    settings_value: Any = None,
    llm_factory: Callable[[], Any] = LLMService,
) -> int:
    parser = argparse.ArgumentParser(description="Generate a report locally.")
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to report YAML config",
    )
    args = parser.parse_args(argv)
    runtime_settings = settings if settings_value is None else settings_value
    if not runtime_settings.LOCAL_MODE:
        print("error: local reports require LOCAL_MODE=true", file=sys.stderr)
        return 2

    try:
        config = load_local_report_config(args.config)
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
