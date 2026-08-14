"""Persisted PDF bytes for asynchronous intake analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class IntakePaperArtifactStore(Protocol):
    def store(self, intake_id: str, filename: str, data: bytes) -> str: ...
    def load(self, intake_id: str) -> bytes: ...
    def uri_for(self, intake_id: str) -> str: ...
    def delete(self, intake_id: str) -> None: ...


class FilesystemIntakePaperStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def store(self, intake_id: str, filename: str, data: bytes) -> str:
        directory = self._directory(intake_id)
        directory.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower() if filename else ".pdf"
        if suffix != ".pdf":
            suffix = ".pdf"
        path = directory / f"paper{suffix}"
        path.write_bytes(data)
        return str(path)

    def load(self, intake_id: str) -> bytes:
        path = Path(self.uri_for(intake_id))
        if not path.is_file():
            raise FileNotFoundError(f"paper artifact missing for {intake_id}")
        return path.read_bytes()

    def uri_for(self, intake_id: str) -> str:
        return str(self._directory(intake_id) / "paper.pdf")

    def delete(self, intake_id: str) -> None:
        directory = self._directory(intake_id)
        path = directory / "paper.pdf"
        if path.is_file():
            path.unlink()
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()

    def _directory(self, intake_id: str) -> Path:
        safe = intake_id.replace(":", "_").replace("/", "_")
        return self.root / safe
