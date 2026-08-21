from __future__ import annotations

from typing import Any

from app.contracts.report_execution import ReportExecutionRequest


def render_system_prompt(
    *,
    language: str,
    input_path: str,
    work_path: str,
    output_path: str,
    available_files: str,
) -> str:
    return f"""You are the GenReport internal report engine.

Requested language: {language}
Read-only inputs: {input_path}
Working files and skills: {work_path}
Authoritative generated outputs: {output_path}

{available_files}

Use only the supplied AXIOM sandbox tools. Each tool call is isolated, so reload
variables and files on every call. Runtime package installation is prohibited;
use only preinstalled packages. Never write outside {output_path}. Treat input
files as read-only. Runtime Gateway artifact finalization is authoritative: only
files finalized there may be presented as report artifacts.

When image inputs are attached, inspect the attached images directly. Never
pixel-analyze an image, use OCR on an image, or install packages to analyze an
image. Use your direct visual observations to create the required PDF report.

For a formal report, read the exact skill file
{work_path}/.skills/latex_skill.md. For slides, read
{work_path}/.skills/ppt_skill.md. Do not call read_file on the .skills
directory itself; use an individual skill file.

For PDF inputs, use preinstalled PyMuPDF (fitz) from Python instead of shelling
out to pdftotext or other optional system binaries.
""".strip()


def build_report_messages(
    request: ReportExecutionRequest,
    *,
    available_files: str,
    image_parts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": render_system_prompt(
                language=request.language,
                input_path=request.execution_context.input_path,
                work_path=request.execution_context.work_path,
                output_path=request.execution_context.output_path,
                available_files=available_files,
            ),
        }
    ]
    for item in request.history:
        content = item.content
        if item.artifact_refs:
            content += "\nArtifacts: " + ", ".join(item.artifact_refs)
        messages.append({"role": item.role, "content": content})
    instruction = f"{request.instruction}"
    content: str | list[dict[str, Any]] = instruction
    if image_parts:
        content = [{"type": "text", "text": instruction}, *image_parts]
    messages.append({"role": "user", "content": content})
    return messages
