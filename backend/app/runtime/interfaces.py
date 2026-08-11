"""Stable boundary between platform orchestration and experiment runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class RuntimeRequest:
    """Provider-neutral input required to start an isolated experiment run."""

    run_id: str
    workspace: Path
    specification_ref: str
    environment: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeResult:
    """Minimal result returned to the platform orchestration layer."""

    run_id: str
    exit_code: int
    artifact_paths: tuple[Path, ...] = ()


@runtime_checkable
class ExperimentRuntime(Protocol):
    """Interface future sandbox runtimes must implement.

    The retained legacy Curie runtime intentionally does not implement this
    contract yet, preventing accidental use by new platform code.
    """

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        """Run one already-admitted experiment in an isolated environment."""
