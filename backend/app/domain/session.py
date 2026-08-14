"""Persistent reproduction workspace spanning multiple independent jobs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator, model_validator

from .alignment import PaperCodeAlignmentCatalog
from .experiment import DomainModel, NonEmptyStr, _require_aware, utc_now
from .external_resources import ResourceResolutionReport
from .intelligence import GoalResolutionResult, PaperExperimentCatalog
from .paper import PaperDocument
from .planner import ReproductionExecutionPlan
from .product_events import ReproductionIntakeState
from .repository import RepositoryAnalysisCatalog
from .reproduction import PaperReference


class ReproductionSessionStatus(str, Enum):
    ACTIVE = "active"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    WAITING_FOR_RESOURCE = "waiting_for_resource"


class SessionExperimentStatus(str, Enum):
    NOT_SELECTED = "not_selected"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReproductionSession(DomainModel):
    """Long-lived scientific workspace. Jobs are independent experiment selections."""

    session_id: NonEmptyStr
    owner_principal: NonEmptyStr
    origin_intake_id: NonEmptyStr
    source_filename: NonEmptyStr
    repository_url: NonEmptyStr
    paper: PaperReference
    paper_content_hash: NonEmptyStr
    paper_document: PaperDocument | None = None
    paper_catalog: PaperExperimentCatalog
    repository_catalog: RepositoryAnalysisCatalog | None = None
    alignment_catalog: PaperCodeAlignmentCatalog | None = None
    repository_snapshot_id: NonEmptyStr | None = None
    repository_commit_sha: NonEmptyStr | None = None
    status: ReproductionSessionStatus = ReproductionSessionStatus.ACTIVE
    pending_goal: NonEmptyStr | None = None
    pending_goal_resolution: GoalResolutionResult | None = None
    pending_resource_resolution: ResourceResolutionReport | None = None
    pending_execution_plan: ReproductionExecutionPlan | None = None
    pending_clarification_answers: tuple[NonEmptyStr, ...] = ()
    pending_job_id: NonEmptyStr | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_times(cls, value: datetime, info: object) -> datetime:
        return _require_aware(value, info.field_name)  # type: ignore[return-value,union-attr]

    @model_validator(mode="after")
    def consistent(self):
        if self.updated_at < self.created_at:
            raise ValueError("session updated_at cannot precede created_at")
        if self.paper_catalog.paper.id != self.paper.id:
            raise ValueError("session paper catalog belongs to a different paper")
        if self.paper_document is not None:
            if self.paper_document.document_id != self.paper_catalog.document_id:
                raise ValueError("session paper document does not match the experiment catalog")
            if self.paper_document.content_hash != self.paper_content_hash:
                raise ValueError("session paper content hash does not match the stored document")
        if self.repository_catalog is None:
            if self.repository_snapshot_id is not None or self.repository_commit_sha is not None:
                raise ValueError("session snapshot identity requires a repository catalog")
            if self.alignment_catalog is not None:
                raise ValueError("alignment catalog requires a repository catalog")
        else:
            if self.repository_snapshot_id is None or self.repository_commit_sha is None:
                raise ValueError("session with a repository catalog requires a locked snapshot")
            if self.repository_catalog.snapshot_id != self.repository_snapshot_id:
                raise ValueError("session repository catalog snapshot differs from the locked snapshot")
            if self.repository_catalog.resolved_commit_sha != self.repository_commit_sha:
                raise ValueError("session repository catalog commit differs from the locked commit")
            if self.alignment_catalog is not None:
                if self.alignment_catalog.repository_snapshot_id != self.repository_snapshot_id:
                    raise ValueError("session alignment catalog snapshot differs from the locked snapshot")
                if self.alignment_catalog.resolved_commit_sha != self.repository_commit_sha:
                    raise ValueError("session alignment catalog commit differs from the locked commit")
        if (
            self.pending_resource_resolution is not None
            and self.pending_resource_resolution.principal != self.owner_principal
        ):
            raise ValueError("session resource resolution owner differs from session owner")
        if self.status is ReproductionSessionStatus.AWAITING_CLARIFICATION and self.pending_goal is None:
            raise ValueError("clarification session requires a pending goal")
        if self.status is ReproductionSessionStatus.WAITING_FOR_RESOURCE:
            if self.pending_resource_resolution is None:
                raise ValueError("resource-waiting session requires a resource resolution")
        return self


class ExperimentJobHistoryItem(DomainModel):
    job_id: NonEmptyStr
    goal: NonEmptyStr
    status: SessionExperimentStatus
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_times(cls, value: datetime, info: object) -> datetime:
        return _require_aware(value, info.field_name)  # type: ignore[return-value,union-attr]


class SessionExperimentProjection(DomainModel):
    experiment_id: NonEmptyStr
    name: NonEmptyStr
    experiment_type: NonEmptyStr
    status: SessionExperimentStatus
    current_job_id: NonEmptyStr | None = None
    job_history: tuple[ExperimentJobHistoryItem, ...] = ()


def intake_state_for_session(status: ReproductionSessionStatus) -> ReproductionIntakeState:
    return {
        ReproductionSessionStatus.ACTIVE: ReproductionIntakeState.READY_TO_RUN,
        ReproductionSessionStatus.AWAITING_CLARIFICATION: ReproductionIntakeState.AMBIGUOUS,
        ReproductionSessionStatus.WAITING_FOR_RESOURCE: ReproductionIntakeState.WAITING_FOR_RESOURCE,
    }[status]
