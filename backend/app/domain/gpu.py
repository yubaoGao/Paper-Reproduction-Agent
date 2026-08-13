"""GPU scheduling and controlled resource-adaptation domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, JsonValue, field_validator, model_validator

from .experiment import DomainModel, NonEmptyStr, _require_aware, utc_now


class GPUDeviceState(str, Enum):
    AVAILABLE = "available"
    LEASED = "leased"
    DRAINING = "draining"
    OFFLINE = "offline"


class MultiGPUSemantics(str, Enum):
    PERFORMANCE_ONLY = "performance_only"
    SEMANTICALLY_REQUIRED = "semantically_required"


class GPUDevice(DomainModel):
    gpu_id: NonEmptyStr
    total_memory_mb: int = Field(gt=0)
    available_memory_mb: int = Field(ge=0)
    state: GPUDeviceState = GPUDeviceState.AVAILABLE
    model_name: NonEmptyStr | None = None
    evidence: tuple[NonEmptyStr, ...] = ()
    observed_at: datetime = Field(default_factory=utc_now)

    @field_validator("observed_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "observed_at")  # type: ignore[return-value]

    @model_validator(mode="after")
    def valid_memory(self):
        if self.available_memory_mb > self.total_memory_mb:
            raise ValueError("available GPU memory cannot exceed total memory")
        return self


class GPURequirement(DomainModel):
    reference_gpu_count: int = Field(ge=0)
    preferred_gpu_count: int = Field(ge=0)
    minimum_gpu_count: int = Field(ge=0)
    estimated_memory_mb: int | None = Field(default=None, gt=0)
    reference_batch_size: int | None = Field(default=None, gt=0)
    gradient_accumulation_steps: int = Field(default=1, ge=1)
    multi_gpu_semantics: MultiGPUSemantics = MultiGPUSemantics.PERFORMANCE_ONLY
    evidence: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def valid_counts(self):
        if self.minimum_gpu_count > self.preferred_gpu_count:
            raise ValueError("minimum GPU count cannot exceed preferred count")
        if (
            self.multi_gpu_semantics is MultiGPUSemantics.SEMANTICALLY_REQUIRED
            and self.reference_gpu_count > 1
            and self.minimum_gpu_count < self.reference_gpu_count
        ):
            raise ValueError("semantically required multi-GPU execution cannot reduce the paper GPU count")
        return self

    @property
    def reference_effective_batch(self) -> int | None:
        if self.reference_batch_size is None:
            return None
        return (
            self.reference_batch_size
            * max(1, self.reference_gpu_count)
            * self.gradient_accumulation_steps
        )


class GPULease(DomainModel):
    lease_token: NonEmptyStr
    job_id: NonEmptyStr
    run_id: NonEmptyStr
    step_id: NonEmptyStr
    worker_id: NonEmptyStr
    allocated_gpu_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    heartbeat_at: datetime

    @field_validator("created_at", "expires_at", "heartbeat_at")
    @classmethod
    def aware_times(cls, value: datetime, info: object) -> datetime:
        return _require_aware(value, info.field_name)  # type: ignore[return-value,union-attr]

    @model_validator(mode="after")
    def valid_lease(self):
        if len(set(self.allocated_gpu_ids)) != len(self.allocated_gpu_ids):
            raise ValueError("GPU lease device IDs must be unique")
        if self.expires_at <= self.created_at:
            raise ValueError("GPU lease expiry must follow creation")
        if not self.created_at <= self.heartbeat_at <= self.expires_at:
            raise ValueError("GPU lease heartbeat must be inside its lease interval")
        return self


class GPURequestStatus(str, Enum):
    WAITING = "waiting"
    LEASED = "leased"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNSATISFIABLE = "unsatisfiable"


class GPUSchedulingRequest(DomainModel):
    request_id: NonEmptyStr
    job_id: NonEmptyStr
    run_id: NonEmptyStr
    step_id: NonEmptyStr
    requirement: GPURequirement
    status: GPURequestStatus = GPURequestStatus.WAITING
    queued_at: datetime = Field(default_factory=utc_now)
    skip_count: int = Field(default=0, ge=0)
    active_lease_token: NonEmptyStr | None = None

    @field_validator("queued_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "queued_at")  # type: ignore[return-value]

    @model_validator(mode="after")
    def lease_matches_status(self):
        if self.status is GPURequestStatus.LEASED and self.active_lease_token is None:
            raise ValueError("leased GPU request requires a lease token")
        if self.status is not GPURequestStatus.LEASED and self.active_lease_token is not None:
            raise ValueError("only a leased GPU request can reference an active lease")
        return self


class PrecisionMode(str, Enum):
    FP32 = "fp32"
    FP16 = "fp16"
    BF16 = "bf16"


class AdaptedExecutionConfig(DomainModel):
    gpu_count: int = Field(ge=1)
    per_gpu_batch_size: int = Field(ge=1)
    gradient_accumulation_steps: int = Field(default=1, ge=1)
    precision: PrecisionMode = PrecisionMode.FP32
    activation_checkpointing: bool = False
    cpu_offload: bool = False
    argument_overrides: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def learning_rate_is_not_implicitly_scaled(self):
        normalized = {
            key.lstrip("-").casefold().replace("-", "_")
            for key in self.argument_overrides
        }
        if normalized & {"lr", "learning_rate"}:
            raise ValueError(
                "resource adaptation cannot modify learning rate without an explicit scaling policy"
            )
        return self

    @property
    def effective_batch_size(self) -> int:
        return self.per_gpu_batch_size * self.gpu_count * self.gradient_accumulation_steps


class RepositoryResourceCapabilities(DomainModel):
    batch_size_argument: NonEmptyStr | None = None
    gradient_accumulation_argument: NonEmptyStr | None = None
    precision_argument: NonEmptyStr | None = None
    activation_checkpointing_argument: NonEmptyStr | None = None
    cpu_offload_argument: NonEmptyStr | None = None
    supports_gradient_accumulation: bool = False
    supports_fp16: bool = False
    supports_bf16: bool = False
    supports_activation_checkpointing: bool = False
    supports_cpu_offload: bool = False
    has_lr_scheduler: bool = False
    has_gradient_clipping: bool = False
    has_ema: bool = False
    coding_patch_available: bool = False
    evidence: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def evidence_backed(self):
        asserted = any(
            (
                self.batch_size_argument,
                self.gradient_accumulation_argument,
                self.precision_argument,
                self.activation_checkpointing_argument,
                self.cpu_offload_argument,
                self.supports_gradient_accumulation,
                self.supports_fp16,
                self.supports_bf16,
                self.supports_activation_checkpointing,
                self.supports_cpu_offload,
                self.has_lr_scheduler,
                self.has_gradient_clipping,
                self.has_ema,
            )
        )
        if asserted and not self.evidence:
            raise ValueError("repository resource capabilities require repository evidence")
        if self.gradient_accumulation_argument and not self.supports_gradient_accumulation:
            raise ValueError("gradient accumulation argument requires supported capability")
        return self


class ResourceAdaptationReason(str, Enum):
    RESOURCE_SHORTAGE = "resource_shortage"
    GPU_OOM = "gpu_oom"


class SemanticImpact(str, Enum):
    PRESERVED = "preserved"
    CONTROLLED_DEVIATION = "controlled_deviation"


class ResourceAdaptationOutcome(str, Enum):
    RETRY = "retry"
    PATCH_AND_RETRY = "patch_and_retry"
    WAITING_FOR_RESOURCES = "waiting_for_resources"
    RESOURCE_UNSATISFIABLE = "resource_unsatisfiable"
    BLOCKED = "blocked"


class ResourceAdaptationRecord(DomainModel):
    adaptation_id: NonEmptyStr
    reason: ResourceAdaptationReason
    original_config: AdaptedExecutionConfig
    adapted_config: AdaptedExecutionConfig
    changed_parameters: tuple[NonEmptyStr, ...]
    effective_batch_before: int = Field(ge=1)
    effective_batch_after: int = Field(ge=1)
    gpu_count_before: int = Field(ge=1)
    gpu_count_after: int = Field(ge=1)
    semantic_impact: SemanticImpact
    evidence: tuple[NonEmptyStr, ...] = ()
    warnings: tuple[NonEmptyStr, ...] = ()
    patch_reference: NonEmptyStr | None = None
    run_id: NonEmptyStr
    step_id: NonEmptyStr
    attempt_number: int = Field(ge=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "created_at")  # type: ignore[return-value]

    @model_validator(mode="after")
    def accurate_provenance(self):
        if self.effective_batch_before != self.original_config.effective_batch_size:
            raise ValueError("recorded pre-adaptation effective batch is inconsistent")
        if self.effective_batch_after != self.adapted_config.effective_batch_size:
            raise ValueError("recorded post-adaptation effective batch is inconsistent")
        if self.gpu_count_before != self.original_config.gpu_count:
            raise ValueError("recorded pre-adaptation GPU count is inconsistent")
        if self.gpu_count_after != self.adapted_config.gpu_count:
            raise ValueError("recorded post-adaptation GPU count is inconsistent")
        expected = (
            SemanticImpact.PRESERVED
            if self.effective_batch_before == self.effective_batch_after
            else SemanticImpact.CONTROLLED_DEVIATION
        )
        if self.semantic_impact is not expected:
            raise ValueError("semantic impact must reflect effective-batch preservation")
        return self


class ResourceAdaptationRequest(DomainModel):
    reason: ResourceAdaptationReason
    requirement: GPURequirement
    current_config: AdaptedExecutionConfig
    allocated_gpu_count: int = Field(ge=0)
    inventory_gpu_count: int = Field(ge=0)
    capabilities: RepositoryResourceCapabilities
    prior_adaptations: tuple[ResourceAdaptationRecord, ...] = ()
    run_id: NonEmptyStr
    step_id: NonEmptyStr
    attempt_number: int = Field(ge=1)


class ResourceAdaptationDecision(DomainModel):
    outcome: ResourceAdaptationOutcome
    record: ResourceAdaptationRecord | None = None
    updated_requirement: GPURequirement | None = None
    patch_instruction: NonEmptyStr | None = None
    reason: NonEmptyStr

    @model_validator(mode="after")
    def valid_shape(self):
        adaptable = {
            ResourceAdaptationOutcome.RETRY,
            ResourceAdaptationOutcome.PATCH_AND_RETRY,
        }
        if self.outcome in adaptable and self.record is None:
            raise ValueError("retry adaptation decision requires a structured record")
        if self.outcome is ResourceAdaptationOutcome.PATCH_AND_RETRY and not self.patch_instruction:
            raise ValueError("patch adaptation decision requires a bounded patch instruction")
        if self.outcome is ResourceAdaptationOutcome.WAITING_FOR_RESOURCES and self.updated_requirement is None:
            raise ValueError("resource wait decision requires an updated GPU requirement")
        return self
