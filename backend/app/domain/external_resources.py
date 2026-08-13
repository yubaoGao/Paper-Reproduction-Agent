"""External scientific-resource identities and resolution outcomes."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath

from pydantic import Field, field_validator, model_validator

from .experiment import DomainModel, NonEmptyStr, _require_aware, utc_now
from .reproduction import EvidenceReference
from .intelligence import GoalResolutionResult, GoalResolutionStatus


def normalize_resource_name(value: str) -> str:
    """Unicode-safe lookup key; never replaces the original resource identity."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class ExternalResourceType(str, Enum):
    DATASET = "dataset"
    CHECKPOINT = "checkpoint"
    PRETRAINED_MODEL = "pretrained_model"


class ResourceAccess(str, Enum):
    READ_ONLY = "read_only"


class ResourceBindingValidationStatus(str, Enum):
    VALIDATED = "validated"
    INVALID = "invalid"


class ResourceResolutionStatus(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    INVALID = "invalid"


class ResourceIntakeState(str, Enum):
    GOAL_RESOLVED = "goal_resolved"
    RESOURCES_RESOLVED = "resources_resolved"
    READY_TO_RUN = "ready_to_run"
    MISSING_RESOURCE = "missing_resource"
    WAITING_FOR_USER_RESOURCE = "waiting_for_user_resource"


class ExternalResourceRequirement(DomainModel):
    requirement_id: NonEmptyStr
    resource_type: ExternalResourceType
    canonical_name: NonEmptyStr
    paper_experiment_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    required: bool = True
    expected_structure: tuple[NonEmptyStr, ...] = ()
    hints: tuple[NonEmptyStr, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()

    @field_validator("expected_structure")
    @classmethod
    def safe_relative_structure(cls, values):
        if len(values) > 64:
            raise ValueError("resource structure validation is bounded to 64 entries")
        for value in values:
            path = PurePosixPath(value.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or value.startswith(("/", "\\")):
                raise ValueError("expected resource structure paths must be safe and relative")
        return values

    @model_validator(mode="after")
    def unique_scope(self):
        if len(set(self.paper_experiment_ids)) != len(self.paper_experiment_ids):
            raise ValueError("resource experiment scope must be unique")
        return self


class ResourceBinding(DomainModel):
    resource_id: NonEmptyStr
    canonical_name: NonEmptyStr
    resource_type: ExternalResourceType
    host_path: NonEmptyStr
    access: ResourceAccess = ResourceAccess.READ_ONLY
    owner_principal: NonEmptyStr | None = None
    shared: bool = False
    validation_status: ResourceBindingValidationStatus
    validation_messages: tuple[NonEmptyStr, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_times(cls, value: datetime, info):
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def safe_binding(self):
        if self.access is not ResourceAccess.READ_ONLY:
            raise ValueError("external scientific resources must be read-only")
        if self.shared and self.owner_principal is not None:
            raise ValueError("approved shared resources cannot have a private owner")
        if not self.shared and self.owner_principal is None:
            raise ValueError("private resources require an owner principal")
        if self.updated_at < self.created_at:
            raise ValueError("resource binding updated_at cannot precede created_at")
        return self

    def accessible_to(self, principal: str) -> bool:
        return self.shared or self.owner_principal == principal


class ResourcePreparationHint(DomainModel):
    resource_type: ExternalResourceType
    canonical_name: NonEmptyStr
    repository_instructions: tuple[NonEmptyStr, ...] = ()
    source_urls: tuple[NonEmptyStr, ...] = ()
    expected_structure: tuple[NonEmptyStr, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
    user_action: NonEmptyStr


class ResourceResolution(DomainModel):
    requirement: ExternalResourceRequirement
    status: ResourceResolutionStatus
    binding: ResourceBinding | None = None
    preparation_hint: ResourcePreparationHint | None = None
    messages: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def consistent(self):
        if self.status is ResourceResolutionStatus.AVAILABLE:
            if self.binding is None:
                raise ValueError("available resource resolution requires a binding")
            if self.binding.validation_status is not ResourceBindingValidationStatus.VALIDATED:
                raise ValueError("available resource binding must be validated")
            if self.binding.resource_type is not self.requirement.resource_type:
                raise ValueError("resource binding type differs from requirement")
            if normalize_resource_name(self.binding.canonical_name) != normalize_resource_name(
                self.requirement.canonical_name
            ):
                raise ValueError("resource binding identity differs from requirement")
        elif self.binding is not None:
            raise ValueError("missing or invalid resources cannot carry an active binding")
        if self.status is ResourceResolutionStatus.MISSING and self.preparation_hint is None:
            raise ValueError("missing resources require preparation guidance")
        return self


class ExternalResourceReference(DomainModel):
    """Path-free internal reference passed across the runtime boundary."""

    resource_id: NonEmptyStr
    canonical_name: NonEmptyStr
    resource_type: ExternalResourceType
    logical_mount_path: NonEmptyStr
    access: ResourceAccess = ResourceAccess.READ_ONLY

    @model_validator(mode="after")
    def approved_logical_path(self):
        path = PurePosixPath(self.logical_mount_path)
        expected_root = "/datasets" if self.resource_type is ExternalResourceType.DATASET else "/checkpoints"
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("logical resource mount path must be absolute and normalized")
        if path == PurePosixPath(expected_root) or PurePosixPath(expected_root) not in path.parents:
            raise ValueError("logical resource mount path is outside its approved sandbox root")
        if self.access is not ResourceAccess.READ_ONLY:
            raise ValueError("external resource references must be read-only")
        return self


class ResourceResolutionReport(DomainModel):
    intake_id: NonEmptyStr
    principal: NonEmptyStr
    specification_id: NonEmptyStr
    selected_experiment_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    resolutions: tuple[ResourceResolution, ...] = ()
    states: tuple[ResourceIntakeState, ...] = Field(min_length=1)
    ready_to_run: bool

    @model_validator(mode="after")
    def scoped_and_ready(self):
        selected = set(self.selected_experiment_ids)
        if len(selected) != len(self.selected_experiment_ids):
            raise ValueError("resource report selected experiments must be unique")
        if any(not set(item.requirement.paper_experiment_ids) <= selected for item in self.resolutions):
            raise ValueError("resource report contains an unselected experiment")
        expected_ready = all(
            not item.requirement.required or item.status is ResourceResolutionStatus.AVAILABLE
            for item in self.resolutions
        )
        if self.ready_to_run is not expected_ready:
            raise ValueError("resource readiness differs from required resolution statuses")
        terminal = (
            ResourceIntakeState.READY_TO_RUN
            if self.ready_to_run
            else ResourceIntakeState.WAITING_FOR_USER_RESOURCE
        )
        if self.states[-1] is not terminal:
            raise ValueError("resource intake state history has an inconsistent terminal state")
        return self


class ExternalResourceIntakeResult(DomainModel):
    """One reproduction intake; resource resume never creates a new request."""

    goal_resolution: GoalResolutionResult
    resource_resolution: ResourceResolutionReport | None = None

    @model_validator(mode="after")
    def aligned_intake(self):
        if self.goal_resolution.status is GoalResolutionStatus.RESOLVED:
            if self.resource_resolution is None:
                raise ValueError("resolved goal requires external resource resolution")
            if self.goal_resolution.specification.id != self.resource_resolution.specification_id:
                raise ValueError("goal and resource resolution specifications differ")
        elif self.resource_resolution is not None:
            raise ValueError("unresolved goal cannot resolve external resources")
        return self
