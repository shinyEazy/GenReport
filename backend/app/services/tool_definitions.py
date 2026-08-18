from __future__ import annotations

from typing import Any


def _string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def _function_tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def get_axiom_tool_definitions(
    *,
    work_path: str,
    output_path: str,
) -> list[dict[str, Any]]:
    isolated = (
        "Each command runs in isolation, so reload variables and files on every call. "
        "Runtime package installation is disabled; use preinstalled packages. "
        f"Inputs are read-only and every generated file must be saved under {output_path}."
    )
    return [
        _function_tool(
            "execute_python",
            (
                "Run Python for data analysis, visualization, and report generation. "
                f"Use {work_path}/.skills for report templates. {isolated}"
            ),
            {"code": _string("Python code to execute.")},
            ["code"],
        ),
        _function_tool(
            "execute_shell",
            (
                "Run one isolated shell command using preinstalled tools. "
                f"Use {work_path}/.skills for report templates. {isolated}"
            ),
            {"command": _string("Shell command to execute.")},
            ["command"],
        ),
        _function_tool(
            "read_file",
            "Read a text file from the run input, work, or output directories. Inputs are read-only.",
            {"path": _string("Run-scoped file path to read.")},
            ["path"],
        ),
        _function_tool(
            "write_file",
            f"Write a text file. The path must be under {output_path}.",
            {
                "path": _string("Output file path."),
                "content": _string("Text content to write."),
            },
            ["path", "content"],
        ),
        _function_tool(
            "edit_file",
            f"Replace exact text in an existing file under {output_path}.",
            {
                "path": _string("Output file path."),
                "old_string": _string("Exact text to replace."),
                "new_string": _string("Replacement text."),
            },
            ["path", "old_string", "new_string"],
        ),
        _function_tool(
            "glob_files",
            "Find files within the run-scoped input, work, or output directories.",
            {
                "pattern": _string("Glob pattern such as *.csv or **/*.png."),
                "path": _string("Optional run-scoped directory to search."),
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum matches to return.",
                },
            },
            ["pattern"],
        ),
        _function_tool(
            "grep_files",
            "Search text files within the run-scoped input, work, or output directories.",
            {
                "pattern": _string("Regular expression or literal text to find."),
                "path": _string("Optional run-scoped directory to search."),
                "include_glob": _string("Optional filename filter such as *.py."),
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether matching is case-sensitive.",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum matches to return.",
                },
            },
            ["pattern"],
        ),
        _function_tool(
            "update_todo",
            "Update the request-local todo list for a long report task.",
            {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": _string("Task description."),
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                        "required": ["content", "status"],
                        "additionalProperties": False,
                    },
                }
            },
            ["todos"],
        ),
    ]
