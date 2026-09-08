# Local Folder Report CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a non-recursive `--folder-path PATH --query TEXT` mode to the existing local report CLI while preserving the YAML `--config` mode.

**Architecture:** Keep `LocalReportRunner` as the only execution engine. Extend `app.local_report.main` with a small folder-discovery helper that validates the folder, returns sorted immediate regular files, and builds `LocalReportConfig` from CLI input plus environment-backed settings. Use the existing injected runner and LLM factories in tests so no network or model execution is required.

**Tech Stack:** Python 3.11+, `argparse`, `pathlib`, `unittest`, existing `LocalReportConfig`, `LocalReportRunner`, and Pydantic settings.

---

## Files and responsibilities

- Modify `src/app/local_report.py`: add folder-mode arguments, immediate-file discovery, in-memory config construction, and input-mode validation.
- Modify `src/tests/test_local_report_cli.py`: cover folder-mode config construction, ordering, non-recursion, and invalid folder/input combinations.
- Modify `README.md`: document the direct folder/query command and its environment-backed configuration.

## Task 1: Add the successful folder-mode path

**Files:**

- Modify: `src/tests/test_local_report_cli.py`
- Modify: `src/app/local_report.py`

- [ ] **Step 1: Write the failing folder-mode test**

Add a test fixture directory containing two immediate files and one nested file,
then capture the config passed to the fake runner:

```python
def test_folder_mode_builds_sorted_non_recursive_config(self) -> None:
    folder = self.root / "inputs"
    folder.mkdir()
    (folder / "z-last.csv").write_text("z\n", encoding="utf-8")
    (folder / "a-first.csv").write_text("a\n", encoding="utf-8")
    nested = folder / "nested"
    nested.mkdir()
    (nested / "ignored.csv").write_text("ignored\n", encoding="utf-8")

    received = {}

    test_case = self

    def runner_factory(**kwargs):
        received["settings"] = kwargs["settings"]
        received["llm_service"] = kwargs["llm_service"]

        class CapturingRunner:
            async def run(self, config):
                received["config"] = config
                return LocalReportResult(
                    workspace=SimpleNamespace(run_root=test_case.root),
                    output_text="Report ready.",
                    artifacts=[],
                )

        runner = CapturingRunner()
        return runner

    self.settings.DEFAULT_MODEL = "env-model"
    self.settings.OPENAI_API_KEY = ""
    self.settings.OPENAI_BASE_URL = "https://provider.example/v1"
    with redirect_stdout(StringIO()):
        exit_code = main(
            [
                "--folder-path",
                str(folder),
                "--query",
                "Analyze these files",
            ],
            runner_factory=runner_factory,
            settings_value=self.settings,
            llm_factory=lambda **kwargs: kwargs,
        )

    self.assertEqual(exit_code, 0)
    config = received["config"]
    self.assertEqual(config.query, "Analyze these files")
    self.assertEqual(
        [path.name for path in config.files],
        ["a-first.csv", "z-last.csv"],
    )
    self.assertEqual(config.model, "env-model")
    self.assertEqual(config.openai_api_key, "")
    self.assertEqual(config.openai_base_url, "https://provider.example/v1")
    self.assertEqual(config.language, "auto")
    self.assertIsNone(config.run_id)

```

Update the test settings in `setUp` with `DEFAULT_MODEL = "env-model",
`OPENAI_API_KEY = ""`, and `OPENAI_BASE_URL = "https://provider.example/v1"`,
because folder mode reads these settings directly.

- [ ] **Step 2: Run the focused test and verify it fails for the missing feature**

Run:

```bash
PYTHONPATH=src python -m unittest src.tests.test_local_report_cli.LocalReportCliTests.test_folder_mode_builds_sorted_non_recursive_config -v
```

Expected result: the test fails because the parser does not yet recognize
`--folder-path`.

- [ ] **Step 3: Implement the minimal folder-mode config path**

In `src/app/local_report.py`, add:

```python
from app.contracts.local_report import (
    LocalReportConfig,
    LocalReportConfigError,
    load_local_report_config,
)


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
```

Change `main`'s parser to define an optional mutually exclusive input group:

```python
input_group = parser.add_mutually_exclusive_group()
input_group.add_argument("--config", type=Path, help="Path to report YAML config")
input_group.add_argument(
    "--folder-path",
    type=Path,
    help="Folder containing immediate input files",
)
parser.add_argument("--query", help="Report query for --folder-path mode")
```

After settings/local-mode validation, reject a missing input mode and reject
`--query` with `--config` using `parser.error`, then choose the config source:

```python
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
```

Preserve the current success output and `LOCAL_MODE` check. Remove the old
`required=True` parser setting because the two input modes are now validated
after parsing.

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```bash
PYTHONPATH=src python -m unittest src.tests.test_local_report_cli.LocalReportCliTests.test_folder_mode_builds_sorted_non_recursive_config -v
```

Expected result: one test passes with no errors.

- [ ] **Step 5: Commit the successful folder-mode implementation**

```bash
git add src/app/local_report.py src/tests/test_local_report_cli.py
git commit -m "feat: add local folder report CLI mode"
```

## Task 2: Add and verify input validation regressions

**Files:**

- Modify: `src/tests/test_local_report_cli.py`
- Modify: `src/app/local_report.py` only if a test exposes a missing validation branch

- [ ] **Step 1: Write focused failing validation tests**

Add these tests using `redirect_stderr` and `main`:

```python
def test_folder_mode_rejects_missing_folder(self) -> None:
    stderr = StringIO()
    with redirect_stderr(stderr):
        exit_code = main(
            ["--folder-path", str(self.root / "missing"), "--query", "Report"],
            settings_value=self.settings,
        )
    self.assertEqual(exit_code, 2)
    self.assertIn("Input folder is unavailable", stderr.getvalue())


def test_folder_mode_rejects_file_path(self) -> None:
    stderr = StringIO()
    with redirect_stderr(stderr):
        exit_code = main(
            ["--folder-path", str(self.source), "--query", "Report"],
            settings_value=self.settings,
        )
    self.assertEqual(exit_code, 2)
    self.assertIn("Input path is not a folder", stderr.getvalue())


def test_folder_mode_rejects_empty_folder(self) -> None:
    folder = self.root / "empty"
    folder.mkdir()
    stderr = StringIO()
    with redirect_stderr(stderr):
        exit_code = main(
            ["--folder-path", str(folder), "--query", "Report"],
            settings_value=self.settings,
        )
    self.assertEqual(exit_code, 2)
    self.assertIn("Input folder contains no files", stderr.getvalue())


def test_folder_mode_requires_query(self) -> None:
    folder = self.root / "inputs"
    folder.mkdir()
    (folder / "source.csv").write_text("value\n42\n", encoding="utf-8")
    stderr = StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["--folder-path", str(folder)], settings_value=self.settings)
    self.assertEqual(exit_code, 2)
    self.assertIn("--query is required", stderr.getvalue())


def test_config_mode_rejects_query(self) -> None:
    stderr = StringIO()
    with redirect_stderr(stderr):
        with self.assertRaises(SystemExit) as raised:
            main(
                ["--config", str(self.config), "--query", "Report"],
                settings_value=self.settings,
            )
    self.assertEqual(raised.exception.code, 2)
    self.assertIn("--query can only be used", stderr.getvalue())


def test_parser_rejects_both_input_modes(self) -> None:
    with self.assertRaises(SystemExit) as raised:
        main(
            [
                "--config",
                str(self.config),
                "--folder-path",
                str(self.root),
            ],
            settings_value=self.settings,
        )
    self.assertEqual(raised.exception.code, 2)


def test_parser_requires_an_input_mode(self) -> None:
    with self.assertRaises(SystemExit) as raised:
        main(["--query", "Report"], settings_value=self.settings)
    self.assertEqual(raised.exception.code, 2)
```

The mutually exclusive group declaration rejects both input modes, while the
explicit post-parse check rejects a command with neither input option.

- [ ] **Step 2: Run the validation tests and verify the expected failures**

Run:

```bash
PYTHONPATH=src python -m unittest src.tests.test_local_report_cli -v
```

Expected result before any additional fix: newly added tests fail only where
the current parser/helper does not yet satisfy the specified validation.

- [ ] **Step 3: Apply the smallest validation fix**

Keep folder validation in `_discover_folder_files` and query validation in
`_build_folder_config`. Keep parser-level combinations in `argparse`/`parser.error`.
Do not change `LocalReportRunner` or YAML config validation.

- [ ] **Step 4: Run all CLI tests**

```bash
PYTHONPATH=src python -m unittest src.tests.test_local_report_cli -v
```

Expected result: all CLI tests pass, including the pre-existing config-mode
success and disabled-local-mode tests.

- [ ] **Step 5: Commit validation coverage**

```bash
git add src/tests/test_local_report_cli.py src/app/local_report.py
git commit -m "test: cover local folder CLI validation"
```

## Task 3: Document and verify the complete feature

**Files:**

- Modify: `README.md`

- [ ] **Step 1: Add the direct CLI documentation**

Under `### Local CLI mode`, document this command and explain that it scans
only immediate regular files:

```bash
cd src
PYTHONPATH=. python -m app.local_report \
  --folder-path ../examples/data/lambda \
  --query "Analyze all supplied files and create a report"
```

State that `LOCAL_MODE=true`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and
`MODEL_LIST`/`DEFAULT_MODEL` come from the environment-backed settings. State
that nested directories are ignored and that YAML mode remains available when
per-run credentials or model overrides are needed.

- [ ] **Step 2: Run the full unit-test suite**

```bash
PYTHONPATH=src python -m unittest discover -s src/tests -v
```

Expected result: the command exits `0` and every discovered test passes.

- [ ] **Step 3: Run a CLI help smoke test**

```bash
PYTHONPATH=src python -m app.local_report --help
```

Expected output includes `--config`, `--folder-path`, and `--query`, and the
command exits `0`.

- [ ] **Step 4: Review the final diff and status**

```bash
git diff HEAD -- src/app/local_report.py src/tests/test_local_report_cli.py README.md
git status --short
```

Confirm the diff contains only the requested CLI, tests, and documentation;
leave the pre-existing example-data changes untouched.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md
git commit -m "docs: describe local folder report CLI"
```
