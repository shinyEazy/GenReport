from __future__ import annotations

import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class LocalWorkspace:
    run_id: str
    run_root: Path
    inputs_dir: Path
    work_dir: Path
    outputs_dir: Path

    @classmethod
    def create(
        cls, root: Path, run_id: str, source_files: list[Path]
    ) -> LocalWorkspace:
        workspace_root = root.expanduser().resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        run_root = workspace_root / run_id
        if run_root.exists():
            raise FileExistsError(f"Local report run already exists: {run_id}")

        inputs_dir = run_root / "inputs"
        work_dir = run_root / "work"
        outputs_dir = run_root / "outputs"
        for directory in (inputs_dir, work_dir, outputs_dir):
            directory.mkdir(parents=True)
        try:
            for source in source_files:
                shutil.copy2(source, inputs_dir / source.name)
        except Exception:
            shutil.rmtree(run_root)
            raise
        return cls(
            run_id=run_id,
            run_root=run_root,
            inputs_dir=inputs_dir,
            work_dir=work_dir,
            outputs_dir=outputs_dir,
        )

    @property
    def virtual_root(self) -> str:
        return f"/workspace/runs/{self.run_id}"

    @property
    def virtual_inputs_path(self) -> str:
        return f"{self.virtual_root}/inputs"

    @property
    def virtual_work_path(self) -> str:
        return f"{self.virtual_root}/work"

    @property
    def virtual_outputs_path(self) -> str:
        return f"{self.virtual_root}/outputs"

    def virtual_input_path(self, filename: str) -> str:
        return f"{self.virtual_inputs_path}/{filename}"

    def resolve_virtual_path(self, path: str) -> Path:
        raw = PurePosixPath(path)
        if ".." in raw.parts:
            raise ValueError("Path traversal is not allowed.")
        for virtual_root, local_root in self._virtual_roots().items():
            if path == virtual_root:
                return local_root
            prefix = f"{virtual_root}/"
            if path.startswith(prefix):
                relative = PurePosixPath(path.removeprefix(prefix))
                resolved = (local_root / relative).resolve()
                if resolved.is_relative_to(local_root.resolve()):
                    return resolved
                break
        raise ValueError("Path is outside the local report workspace.")

    def artifact_entries(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for path in sorted(self.outputs_dir.rglob("*"), key=lambda item: str(item)):
            if not path.is_file():
                continue
            relative = path.relative_to(self.outputs_dir).as_posix()
            entries.append(
                {
                    "artifact_ref": str(path.resolve()),
                    "filename": path.name,
                    "content_type": mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream",
                    "size": path.stat().st_size,
                    "path": f"{self.virtual_outputs_path}/{relative}",
                }
            )
        return entries

    def _virtual_roots(self) -> dict[str, Path]:
        return {
            self.virtual_inputs_path: self.inputs_dir,
            self.virtual_work_path: self.work_dir,
            self.virtual_outputs_path: self.outputs_dir,
        }
