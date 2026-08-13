"""Provider-neutral ports required by the production reproduction orchestrator."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import Field, JsonValue

from backend.app.domain import (
    ArtifactReference,
    ExperimentSpecification,
    ReproductionRun,
    ValidationRecord,
)
from backend.app.domain.experiment import DomainModel, NonEmptyStr
from backend.app.runtime.curie_models import CommandExecutionResult
from backend.app.runtime.ports import WorkspacePort


class ConcurrentRunUpdateError(RuntimeError):
    pass


@runtime_checkable
class ReproductionRunRepository(Protocol):
    """Durable, optimistic-concurrency persistence boundary."""

    def create(self, run: ReproductionRun) -> None: ...

    def save(self, run: ReproductionRun, *, expected_revision: int) -> None: ...

    def get(self, run_id: str) -> ReproductionRun: ...

    def list_by_job(self, job_id: str) -> tuple[ReproductionRun, ...]: ...

    def list_by_status(self, status: str) -> tuple[ReproductionRun, ...]: ...


@runtime_checkable
class CancellationPort(Protocol):
    def is_cancel_requested(self, run_id: str) -> bool: ...


@runtime_checkable
class WorkspaceLifecyclePort(WorkspacePort, Protocol):
    def cleanup(self, run_id: str) -> None: ...


class SemanticValidationRequest(DomainModel):
    run_id: NonEmptyStr
    step_id: NonEmptyStr
    attempt_number: int = Field(ge=1)
    experiment: ExperimentSpecification
    execution_result: CommandExecutionResult
    artifacts: tuple[ArtifactReference, ...] = ()
    deterministic_validations: tuple[ValidationRecord, ...]


class SemanticValidationResult(DomainModel):
    passed: bool
    status: NonEmptyStr
    violations: tuple[NonEmptyStr, ...] = ()
    details: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)


@runtime_checkable
class SemanticValidationPort(Protocol):
    def validate(self, request: SemanticValidationRequest) -> SemanticValidationResult: ...
