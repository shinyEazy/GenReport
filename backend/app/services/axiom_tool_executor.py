from __future__ import annotations

import base64
import fnmatch
import json
import mimetypes
import posixpath
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from app.contracts.report_execution import ExecutionFileRequest
from app.services.axiom_execution_client import AxiomExecutionClient
from app.services.tool_definitions import get_axiom_tool_definitions

IGNORED_OUTPUT_SUFFIXES = {
    ".aux",
    ".log",
    ".out",
    ".toc",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
}

HTML_IMAGE_SRC_PATTERN = re.compile(
    r"(?P<prefix><img\b[^>]*?\bsrc\s*=\s*)"
    r"(?P<quote>['\"])(?P<src>[^'\"]+)(?P=quote)",
    re.IGNORECASE,
)


class AxiomToolExecutor:
    def __init__(
        self,
        *,
        client: AxiomExecutionClient,
        files: list[ExecutionFileRequest],
        input_path: str,
        work_path: str,
        output_path: str,
    ) -> None:
        self.client = client
        self.files = files
        self.input_path = input_path.rstrip("/")
        self.work_path = work_path.rstrip("/")
        self.output_path = output_path.rstrip("/")
        self.todos: list[dict[str, Any]] = []
        self._inputs_by_name = {item.filename: item.sandbox_path for item in files}

    async def close(self) -> None:
        await self.client.close()

    async def materialize_assets(self) -> None:
        skills_path = f"{self.work_path}/.skills"
        setup = await self.client.execute(
            language="python",
            code=(
                "from pathlib import Path\n"
                f"Path({skills_path!r}).mkdir(parents=True, exist_ok=True)\n"
                f"Path({f'{skills_path}/res'!r}).mkdir(parents=True, exist_ok=True)\n"
            ),
            cwd=self.work_path,
            timeout_seconds=30,
        )
        if not setup.get("success"):
            raise RuntimeError("Unable to initialize GenReport skill directories.")
        skills_root = Path(__file__).resolve().parents[1] / "skills"
        for name in ("latex_skill.md", "ppt_skill.md"):
            path = skills_root / name
            if path.is_file():
                await self.client.write_file(
                    f"{self.work_path}/.skills/{name}",
                    path.read_bytes(),
                )
        logo = skills_root / "res" / "logo.png"
        if logo.is_file():
            await self.client.write_file(
                f"{self.work_path}/.skills/res/logo.png",
                logo.read_bytes(),
            )

    def get_available_files_prompt(self) -> str:
        if not self.files:
            return "No input files were selected for this run."
        lines = ["AVAILABLE INPUT FILES:"]
        for item in self.files:
            lines.append(f"  - {item.filename}: {item.sandbox_path}")
        lines.append("Inputs are read-only. Save every generated file under the output workspace.")
        return "\n".join(lines)

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return get_axiom_tool_definitions(
            work_path=self.work_path,
            output_path=self.output_path,
        )

    async def execute_tool(
        self, tool_name: str, tool_input: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        handlers = {
            "execute_python": self._execute_python,
            "execute_shell": self._execute_shell,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "glob_files": self._glob_files,
            "grep_files": self._grep_files,
            "update_todo": self._update_todo,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            return {"success": False, "error": f"Unknown tool: {tool_name}", "output": ""}
        try:
            return await handler(dict(tool_input))
        except Exception as exc:
            return {"success": False, "error": str(exc), "output": str(exc)}

    async def finalize_generated_files(
        self, generated_files: list[dict[str, Any]], workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        await self._inline_html_images(generated_files)
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in generated_files:
            path = item.get("sandbox_path") or item.get("path")
            if not isinstance(path, str) or path in seen:
                continue
            seen.add(path)
            filename = PurePosixPath(path).name
            entries.append(
                {
                    "path": path,
                    "filename": filename,
                    "content_type": mimetypes.guess_type(filename)[0]
                    or "application/octet-stream",
                    "artifact_type": self._file_type(filename),
                }
            )
        artifacts = await self.client.finalize(entries, workspace_id=workspace_id)
        normalized: list[dict[str, Any]] = []
        for item in artifacts:
            artifact = dict(item)
            artifact_ref = (
                artifact.get("artifact_ref")
                or artifact.get("artifact_id")
                or artifact.get("asset_id")
            )
            if isinstance(artifact_ref, str) and artifact_ref.strip():
                artifact["artifact_ref"] = artifact_ref
            normalized.append(artifact)
        return normalized

    async def _inline_html_images(
        self, generated_files: list[dict[str, Any]]
    ) -> None:
        files_by_path: dict[str, str] = {}
        for item in generated_files:
            path = item.get("sandbox_path") or item.get("path")
            if isinstance(path, str):
                files_by_path[posixpath.normpath(path)] = path

        for html_path in files_by_path.values():
            if PurePosixPath(html_path).suffix.lower() not in {".html", ".htm"}:
                continue
            try:
                html = (await self.client.read_file(html_path)).decode("utf-8")
            except (KeyError, UnicodeDecodeError):
                continue

            embedded: dict[str, str] = {}
            for match in HTML_IMAGE_SRC_PATTERN.finditer(html):
                src = match.group("src")
                asset_path = self._relative_html_asset_path(html_path, src)
                stored_path = files_by_path.get(asset_path)
                if stored_path is None or src in embedded:
                    continue
                content_type = mimetypes.guess_type(stored_path)[0]
                if not content_type or not content_type.startswith("image/"):
                    continue
                content = await self.client.read_file(stored_path)
                encoded = base64.b64encode(content).decode("ascii")
                embedded[src] = f"data:{content_type};base64,{encoded}"

            if not embedded:
                continue

            updated = HTML_IMAGE_SRC_PATTERN.sub(
                lambda match: (
                    f"{match.group('prefix')}{match.group('quote')}"
                    f"{embedded.get(match.group('src'), match.group('src'))}"
                    f"{match.group('quote')}"
                ),
                html,
            )
            await self.client.write_file(html_path, updated)

    @staticmethod
    def _relative_html_asset_path(html_path: str, src: str) -> str:
        parsed = urlsplit(src)
        if parsed.scheme or parsed.netloc or src.startswith(("data:", "#")):
            return ""
        decoded_path = unquote(parsed.path)
        if not decoded_path:
            return ""
        if decoded_path.startswith("/"):
            return posixpath.normpath(decoded_path)
        return posixpath.normpath(
            posixpath.join(str(PurePosixPath(html_path).parent), decoded_path)
        )

    async def _execute_python(self, value: dict[str, Any]) -> dict[str, Any]:
        before = await self._output_snapshot()
        code = self._rewrite_legacy_paths(str(value.get("code") or ""))
        result = await self.client.execute(
            language="python",
            code=code,
            cwd=self.output_path,
            timeout_seconds=int(value.get("timeout") or 120),
        )
        return await self._command_result(result, before)

    async def _execute_shell(self, value: dict[str, Any]) -> dict[str, Any]:
        before = await self._output_snapshot()
        command = self._rewrite_legacy_paths(str(value.get("command") or ""))
        result = await self.client.execute(
            language="shell",
            code=command,
            cwd=self.output_path,
            timeout_seconds=int(value.get("timeout") or 120),
        )
        return await self._command_result(result, before)

    async def _command_result(
        self, result: dict[str, Any], before: dict[str, int]
    ) -> dict[str, Any]:
        generated = await self._changed_outputs(before)
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        return {
            "success": bool(result.get("success")),
            "stdout": stdout,
            "stderr": stderr,
            "output": stdout or stderr,
            "exit_code": result.get("exit_code"),
            "generated_files": generated,
        }

    async def _read_file(self, value: dict[str, Any]) -> dict[str, Any]:
        path, content = await self._read_resolved(str(value.get("path") or ""))
        text = content.decode("utf-8", errors="replace")
        return {
            "success": True,
            "path": path,
            "content": text,
            "content_preview": text[:100_000],
            "output": text[:100_000],
        }

    async def _write_file(self, value: dict[str, Any]) -> dict[str, Any]:
        path = self._write_path(str(value.get("path") or ""))
        content = str(value.get("content") or "")
        await self.client.write_file(path, content)
        generated = self._generated_file(path, len(content.encode("utf-8")))
        return {
            "success": True,
            "path": path,
            "output": f"Wrote {path}",
            "generated_files": [generated],
        }

    async def _edit_file(self, value: dict[str, Any]) -> dict[str, Any]:
        path, content = await self._read_resolved(str(value.get("path") or ""))
        if not path.startswith(f"{self.output_path}/"):
            raise ValueError("only output files can be edited")
        old = str(value.get("old_string") or "")
        new = str(value.get("new_string") or "")
        text = content.decode("utf-8")
        if old not in text:
            raise ValueError("old_string was not found")
        updated = text.replace(old, new, 1)
        await self.client.write_file(path, updated)
        return {
            "success": True,
            "path": path,
            "output": f"Updated {path}",
            "generated_files": [
                self._generated_file(path, len(updated.encode("utf-8")))
            ],
        }

    async def _glob_files(self, value: dict[str, Any]) -> dict[str, Any]:
        pattern = str(value.get("pattern") or "*")
        max_results = min(int(value.get("max_results") or 200), 500)
        root = self._read_root(str(value.get("path") or self.output_path))
        snapshot = await self._recursive_snapshot(root)
        matches = [path for path in snapshot if fnmatch.fnmatch(PurePosixPath(path).name, pattern) or fnmatch.fnmatch(path, pattern)]
        matches = matches[:max_results]
        return {"success": True, "matches": matches, "output": "\n".join(matches)}

    async def _grep_files(self, value: dict[str, Any]) -> dict[str, Any]:
        pattern = str(value.get("pattern") or "")
        flags = 0 if value.get("case_sensitive") else re.IGNORECASE
        expression = re.compile(pattern, flags)
        include_glob = str(value.get("include_glob") or "*")
        max_results = min(int(value.get("max_results") or 200), 500)
        root = self._read_root(str(value.get("path") or self.output_path))
        snapshot = await self._recursive_snapshot(root)
        matches: list[str] = []
        for path in snapshot:
            if not fnmatch.fnmatch(PurePosixPath(path).name, include_glob):
                continue
            try:
                content = (await self.client.read_file(path)).decode("utf-8", errors="replace")
            except Exception:
                continue
            for line_number, line in enumerate(content.splitlines(), 1):
                if expression.search(line):
                    matches.append(f"{path}:{line_number}:{line[:500]}")
                    if len(matches) >= max_results:
                        return {"success": True, "matches": matches, "output": "\n".join(matches)}
        return {"success": True, "matches": matches, "output": "\n".join(matches)}

    async def _update_todo(self, value: dict[str, Any]) -> dict[str, Any]:
        todos = value.get("todos")
        self.todos = todos if isinstance(todos, list) else []
        return {"success": True, "todos": self.todos, "output": json.dumps(self.todos)}

    def _rewrite_legacy_paths(self, value: str) -> str:
        return value.replace(
            "/tmp/workspace/.skills", f"{self.work_path}/.skills"
        ).replace("/tmp/workspace", self.output_path)

    def _write_path(self, raw: str) -> str:
        rewritten = self._rewrite_legacy_paths(raw.strip())
        path = PurePosixPath(rewritten)
        if not path.is_absolute():
            path = PurePosixPath(self.output_path) / path
        if ".." in path.parts:
            raise ValueError("path traversal is not allowed")
        resolved = str(path)
        if not (
            resolved == self.output_path or resolved.startswith(f"{self.output_path}/")
        ):
            raise ValueError("writes must stay below the output directory")
        return resolved

    async def _read_resolved(self, raw: str) -> tuple[str, bytes]:
        rewritten = self._rewrite_legacy_paths(raw.strip())
        path = PurePosixPath(rewritten)
        candidates: list[str] = []
        if path.is_absolute():
            candidates.append(str(path))
        else:
            if str(path).startswith(".skills/"):
                candidates.append(f"{self.work_path}/{path}")
            candidates.extend(
                [
                    f"{self.output_path}/{path}",
                    f"{self.work_path}/{path}",
                ]
            )
            declared = self._inputs_by_name.get(str(path)) or self._inputs_by_name.get(path.name)
            if declared:
                candidates.append(declared)
        for candidate in candidates:
            if ".." in PurePosixPath(candidate).parts:
                continue
            try:
                return candidate, await self.client.read_file(candidate)
            except Exception:
                continue
        raise FileNotFoundError(raw)

    def _read_root(self, raw: str) -> str:
        rewritten = self._rewrite_legacy_paths(raw.strip())
        path = PurePosixPath(rewritten)
        if not path.is_absolute():
            path = PurePosixPath(self.output_path) / path
        resolved = str(path)
        allowed = (self.input_path, self.work_path, self.output_path)
        if not any(resolved == root or resolved.startswith(f"{root}/") for root in allowed):
            raise ValueError("read path is outside the run workspace")
        return resolved

    async def _output_snapshot(self) -> dict[str, int]:
        return await self._recursive_snapshot(self.output_path)

    async def _recursive_snapshot(self, root: str) -> dict[str, int]:
        pending = [root]
        files: dict[str, int] = {}
        while pending:
            current = pending.pop()
            for item in await self.client.list_files(current):
                path = str(item.get("path") or "")
                absolute = path if path.startswith("/workspace/") else f"/workspace/{path}"
                if item.get("kind") == "directory":
                    if "/.skills" not in absolute:
                        pending.append(absolute)
                elif item.get("kind") == "file":
                    files[absolute] = int(item.get("size_bytes") or 0)
        return files

    async def _changed_outputs(self, before: dict[str, int]) -> list[dict[str, Any]]:
        after = await self._output_snapshot()
        return [
            self._generated_file(path, size)
            for path, size in after.items()
            if before.get(path) != size and not self._ignore_output(path)
        ]

    @staticmethod
    def _ignore_output(path: str) -> bool:
        lowered = path.lower()
        return any(lowered.endswith(suffix) for suffix in IGNORED_OUTPUT_SUFFIXES)

    def _generated_file(self, path: str, size: int) -> dict[str, Any]:
        filename = PurePosixPath(path).name
        return {
            "filename": filename,
            "name": filename,
            "sandbox_path": path,
            "size": size,
            "type": self._file_type(filename),
        }

    @staticmethod
    def _file_type(filename: str) -> str:
        suffix = PurePosixPath(filename).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
            return "image"
        if suffix == ".pdf":
            return "pdf"
        if suffix in {".csv", ".xlsx", ".xls", ".json", ".txt", ".html", ".md"}:
            return "data"
        return "file"
