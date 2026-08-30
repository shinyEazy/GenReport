from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from app.services.local_workspace import LocalWorkspace


class LocalExecutionClient:
    """Execution adapter for a host-local, run-scoped workspace."""

    def __init__(self, workspace: LocalWorkspace, timeout_seconds: float) -> None:
        self.workspace = workspace
        self.timeout_seconds = timeout_seconds

    async def close(self) -> None:
        return None

    async def execute(
        self,
        *,
        language: str,
        code: str,
        cwd: str,
        timeout_seconds: int | None = None,
        dependencies: list[str] | None = None,
    ) -> dict[str, Any]:
        del dependencies
        local_cwd = self.workspace.resolve_virtual_path(cwd)
        translated_code = self._translate_virtual_paths(code)
        if language == "python":
            command = (sys.executable, "-c", translated_code)
        elif language in {"shell", "bash"}:
            command = ("/bin/sh", "-lc", translated_code)
        else:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Unsupported language: {language}",
                "exit_code": -1,
            }

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=local_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds or self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {
                "success": False,
                "stdout": "",
                "stderr": "Execution timed out.",
                "exit_code": -1,
            }
        except OSError as exc:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(exc),
                "exit_code": -1,
            }

        return {
            "success": process.returncode == 0,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "exit_code": process.returncode,
        }

    async def list_files(self, path: str) -> list[dict[str, Any]]:
        local_path = self.workspace.resolve_virtual_path(path)
        if not local_path.is_dir():
            return []
        entries: list[dict[str, Any]] = []
        for child in sorted(local_path.iterdir(), key=lambda item: item.name):
            if child.is_dir():
                kind = "directory"
                size = 0
            elif child.is_file():
                kind = "file"
                size = child.stat().st_size
            else:
                continue
            entries.append(
                {
                    "path": self._virtual_path(child),
                    "kind": kind,
                    "size_bytes": size,
                }
            )
        return entries

    async def read_file(self, path: str) -> bytes:
        local_path = self.workspace.resolve_virtual_path(path)
        if local_path.is_dir():
            lines = ["Directory contents:"]
            for child in sorted(local_path.iterdir(), key=lambda item: item.name):
                suffix = "/" if child.is_dir() else ""
                lines.append(f"- {child.name}{suffix}")
            return ("\n".join(lines) + "\n").encode("utf-8")
        return local_path.read_bytes()

    async def write_file(self, path: str, content: bytes | str) -> int:
        local_path = self.workspace.resolve_virtual_path(path)
        value = content.encode("utf-8") if isinstance(content, str) else content
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(value)
        return len(value)

    async def finalize(
        self, entries: list[dict[str, Any]], *, workspace_id: str | None = None
    ) -> list[dict[str, object]]:
        del entries, workspace_id
        return self.workspace.artifact_entries()

    def _translate_virtual_paths(self, value: str) -> str:
        replacements = {
            self.workspace.virtual_inputs_path: str(
                self.workspace.inputs_dir.resolve()
            ),
            self.workspace.virtual_work_path: str(self.workspace.work_dir.resolve()),
            self.workspace.virtual_outputs_path: str(
                self.workspace.outputs_dir.resolve()
            ),
        }
        for virtual_path, local_path in replacements.items():
            value = value.replace(virtual_path, local_path)
        return value

    def _virtual_path(self, path: Path) -> str:
        for virtual_root, local_root in (
            (self.workspace.virtual_inputs_path, self.workspace.inputs_dir),
            (self.workspace.virtual_work_path, self.workspace.work_dir),
            (self.workspace.virtual_outputs_path, self.workspace.outputs_dir),
        ):
            resolved_root = local_root.resolve()
            resolved_path = path.resolve()
            if resolved_path.is_relative_to(resolved_root):
                relative = resolved_path.relative_to(resolved_root).as_posix()
                return f"{virtual_root}/{relative}" if relative else virtual_root
        raise ValueError("Path is outside the local report workspace.")
