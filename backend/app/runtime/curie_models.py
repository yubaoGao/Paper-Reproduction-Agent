"""Stable models at the PaperReproAgent-to-Curie execution boundary."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, JsonValue, model_validator

from backend.app.domain import (
    Artifact,
    EnvironmentRequirement,
    ExecutableCommand,
    Metric,
    MetricExpectation,
    ResourceRequirement,
    RunError,
    RunStatus,
)
from backend.app.domain.experiment import DomainModel, NonEmptyStr


class ReproductionExecutionMode(str, Enum):
    REPRODUCTION = "reproduction"


class ConstraintLevel(str, Enum):
    LOCKED = "locked"
    ADVISORY = "advisory"
    RUNTIME_RESOLVED = "runtime_resolved"


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class ComponentType(str, Enum):
    AGENT = "agent"
    SERVICE = "service"


class CurieConstraint(DomainModel):
    key: NonEmptyStr
    value: JsonValue | None = None
    level: ConstraintLevel
    source: NonEmptyStr


class CurieExecutionConstraints(DomainModel):
    items: tuple[CurieConstraint, ...]

    @model_validator(mode="after")
    def unique_keys(self) -> CurieExecutionConstraints:
        keys = [item.key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("constraint keys must be unique")
        return self

    def values(self, level: ConstraintLevel) -> dict[str, JsonValue | None]:
        return {item.key: item.value for item in self.items if item.level is level}


class CurieExecutionContext(DomainModel):
    mode: ReproductionExecutionMode = ReproductionExecutionMode.REPRODUCTION
    run_id: NonEmptyStr
    experiment_id: NonEmptyStr
    step_id: NonEmptyStr | None = None
    objective: NonEmptyStr
    repository_uri: NonEmptyStr
    repository_revision: NonEmptyStr | None = None
    repository_snapshot_id: NonEmptyStr | None = None
    implementation_id: NonEmptyStr | None = None
    task_type: NonEmptyStr
    entrypoint: NonEmptyStr | None = None
    config_ids: tuple[NonEmptyStr, ...] = ()
    command: ExecutableCommand
    dataset_requirement: JsonValue | None = None
    environment_requirement: EnvironmentRequirement
    resource_requirement: ResourceRequirement
    hyperparameters: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)
    ablation_modifications: dict[NonEmptyStr, JsonValue] = Field(
        default_factory=dict
    )
    expected_claim_ids: tuple[NonEmptyStr, ...] = ()
    expected_claims: tuple[JsonValue, ...] = ()
    expected_metrics: tuple[MetricExpectation, ...] = ()
    provenance_decision_ids: tuple[NonEmptyStr, ...] = ()
    planner_decisions: tuple[JsonValue, ...] = ()
    constraints: CurieExecutionConstraints
    namespace: NonEmptyStr
    thread_id: NonEmptyStr
    execution_instruction: NonEmptyStr


class WorkspaceReferences(DomainModel):
    run_workspace: NonEmptyStr
    repository_workspace: NonEmptyStr
    artifact_output: NonEmptyStr


class CommandExecutionRequest(DomainModel):
    run_id: NonEmptyStr
    experiment_id: NonEmptyStr
    command_id: NonEmptyStr
    program: NonEmptyStr
    argv: tuple[NonEmptyStr, ...] = ()
    working_directory_reference: NonEmptyStr
    environment_references: tuple[NonEmptyStr, ...] = ()
    timeout_seconds: int = Field(gt=0)


class CommandExecutionResult(DomainModel):
    status: ExecutionStatus
    exit_code: int | None = None
    stdout: NonEmptyStr | None = None
    stderr: NonEmptyStr | None = None
    stdout_reference: NonEmptyStr | None = None
    stderr_reference: NonEmptyStr | None = None
    duration_seconds: float = Field(ge=0)
    metrics: tuple[Metric, ...] = ()
    artifacts: tuple[Artifact, ...] = ()

    @model_validator(mode="after")
    def consistent(self) -> CommandExecutionResult:
        if self.status is ExecutionStatus.SUCCEEDED and self.exit_code != 0:
            raise ValueError("successful command requires exit code zero")
        if self.status is ExecutionStatus.FAILED and self.exit_code in (None, 0):
            raise ValueError("failed command requires non-zero exit code")
        if self.status is ExecutionStatus.TIMED_OUT and self.exit_code == 0:
            raise ValueError("timed-out command cannot have successful exit code")
        return self


class CodingRequest(DomainModel):
    run_id: NonEmptyStr
    experiment_id: NonEmptyStr
    instruction: NonEmptyStr
    allowed_change_categories: tuple[NonEmptyStr, ...]
    locked_constraint_keys: tuple[NonEmptyStr, ...]


class CodingValidationResult(DomainModel):
    name: NonEmptyStr
    passed: bool
    details: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)


class CodingResult(DomainModel):
    patch_id: NonEmptyStr
    summary: NonEmptyStr
    changed_categories: tuple[NonEmptyStr, ...] = ()
    proposed_values: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)
    artifact: Artifact | None = None
    validations: tuple[CodingValidationResult, ...] = ()


class CuriePlanRecord(DomainModel):
    plan_id: NonEmptyStr
    summary: NonEmptyStr
    tasks: tuple[NonEmptyStr, ...]
    locked_snapshot: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)


class CurieValidationRecord(DomainModel):
    validator_name: NonEmptyStr
    valid: bool
    status: NonEmptyStr
    violations: tuple[NonEmptyStr, ...] = ()
    details: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)


class CuriePatchRecord(DomainModel):
    patch_id: NonEmptyStr
    summary: NonEmptyStr
    accepted: bool
    violations: tuple[NonEmptyStr, ...] = ()


class CurieAgentTraceRecord(DomainModel):
    component_name: NonEmptyStr
    component_type: ComponentType
    status: NonEmptyStr
    message: NonEmptyStr | None = None


class CurieExecutionResult(DomainModel):
    run_id: NonEmptyStr
    experiment_id: NonEmptyStr
    status: RunStatus
    plans: tuple[CuriePlanRecord, ...] = ()
    attempts: int = Field(default=0, ge=0)
    validation_results: tuple[CurieValidationRecord, ...] = ()
    patches: tuple[CuriePatchRecord, ...] = ()
    metrics: tuple[Metric, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    analysis: JsonValue | None = None
    conclusion: NonEmptyStr | None = None
    warnings: tuple[NonEmptyStr, ...] = ()
    agent_trace: tuple[CurieAgentTraceRecord, ...] = ()
    error: RunError | None = None
    exit_code: int | None = None
    started_at: datetime
    finished_at: datetime

    @model_validator(mode="after")
    def terminal(self) -> CurieExecutionResult:
        if self.finished_at < self.started_at:
            raise ValueError("Curie execution time range is inverted")
        if self.status is RunStatus.SUCCEEDED and (
            self.error is not None or self.exit_code != 0
        ):
            raise ValueError("successful Curie result is inconsistent")
        if self.status is RunStatus.FAILED and self.error is None:
            raise ValueError("failed Curie result requires error")
        return self
