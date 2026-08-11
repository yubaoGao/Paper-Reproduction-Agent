"""Translation boundary between the platform contract and retained Curie Core."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.domain import RunRequest, RunResult

from .interfaces import RunEventSink


@dataclass(frozen=True)
class CurieRuntimeInput:
    """Minimal input shape the future Curie workflow integration can consume."""

    run_id: str
    experiment_id: str
    objective: str
    entrypoint: str | None
    command: tuple[str, ...]
    seed: int | None


class CurieRuntimeAdapter:
    """Curie adapter skeleton implementing the stable runtime contract.

    Task 02 deliberately stops at the translation seam. The retained workflow
    still assumes Docker, OpenHands, global logging and Linux-specific setup, so
    invoking it here would create an unsafe fake production path.
    """

    def translate_request(self, request: RunRequest) -> CurieRuntimeInput:
        specification = request.experiment
        return CurieRuntimeInput(
            run_id=request.run_id,
            experiment_id=specification.id,
            objective=specification.description,
            entrypoint=specification.entrypoint,
            command=specification.command,
            seed=specification.seed,
        )

    def run(self, request: RunRequest, event_sink: RunEventSink) -> RunResult:
        translated = self.translate_request(request)
        raise NotImplementedError(
            "Curie execution is intentionally deferred; "
            f"translated run {translated.run_id!r} is not executed"
        )
