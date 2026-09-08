# Local Folder Report CLI Design

## Goal

Allow a developer to run a local report directly against the files in one
folder without writing a YAML configuration file, while preserving the
existing YAML-based local CLI.

## User-facing command

```bash
PYTHONPATH=src python -m app.local_report \
  --folder-path ./examples/data/lambda \
  --query "Analyze all supplied files and generate a report"
```

The folder path is scanned non-recursively. Only regular files whose immediate
parent is the supplied folder are included. Files are sorted by filename for
deterministic prompts and workspace contents. Subdirectories are ignored.

## Architecture and data flow

`app.local_report.main` will support two mutually exclusive input modes:

1. `--config PATH`: the existing YAML workflow.
2. `--folder-path PATH --query TEXT`: the new direct folder workflow.

For folder mode, the CLI validates the path, discovers immediate regular files,
and constructs the existing `LocalReportConfig` in memory. The config uses the
query from the command line, the discovered files, and environment-backed
settings for the model, API key, API base URL, and default language. The
existing `LocalReportRunner` remains the sole execution path.

The runner continues to copy source files into the per-run workspace, execute
the report model and local tools, and print the report text, workspace path, and
generated artifacts.

## Validation and errors

- `--config` and `--folder-path` are mutually exclusive.
- `--query` is required in folder mode and is rejected with config mode.
- A missing path, non-directory path, or directory with no immediate regular
  files returns exit code `2` with a concise error on stderr.
- Local mode must still be enabled with `LOCAL_MODE=true`.
- Existing YAML validation and runner errors retain their current behavior.

## Tests

CLI tests will cover:

- constructing a folder-mode config from the query and immediate files;
- excluding nested files;
- deterministic filename ordering;
- missing, non-directory, and empty-folder failures;
- continued success of the existing config mode.

The tests will inject the runner and LLM factories already supported by the
current CLI tests, avoiding network calls and model execution.

## Documentation

`README.md` will document the direct folder invocation, non-recursive behavior,
and the environment values required for local execution. The existing YAML
workflow documentation remains available for callers that need per-run model
or credential overrides.
