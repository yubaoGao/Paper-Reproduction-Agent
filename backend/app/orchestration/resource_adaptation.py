"""Task 11 bridge for bounded GPU OOM adaptation decisions."""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from pydantic import Field

from backend.app.domain import (
    AdaptedExecutionConfig, GPURequirement, PatchRecord, PatchStatus,
    RepositoryResourceCapabilities, ResourceAdaptationDecision,
    ResourceAdaptationOutcome, ResourceAdaptationReason,
    ResourceAdaptationRequest,
)
from backend.app.domain.experiment import DomainModel
from backend.app.runtime.curie_models import CodingRequest
from backend.app.runtime.guard import ExperimentSpecificationGuard
from backend.app.services.gpu import ExecutionConfigApplier, ResourceAdaptationAgent


class ResourceExecutionProfile(DomainModel):
    requirement: GPURequirement
    initial_config: AdaptedExecutionConfig
    allocated_gpu_count: int = Field(ge=0)
    inventory_gpu_count: int = Field(ge=0)
    capabilities: RepositoryResourceCapabilities


@runtime_checkable
class ResourceExecutionProfileProvider(Protocol):
    def profile(self, run_id: str, step_id: str) -> ResourceExecutionProfile: ...


class ResourceWaitRequired(RuntimeError):
    def __init__(self, requirement: GPURequirement, step_id: str, reason: str) -> None:
        super().__init__(reason)
        self.requirement = requirement
        self.step_id = step_id


class OOMAdaptationCoordinator:
    """Coordinates policy, optional run-private patching, and deterministic CLI changes."""

    def __init__(
        self,
        profile_provider: ResourceExecutionProfileProvider,
        *,
        agent: ResourceAdaptationAgent | None = None,
        coding_port=None,
        guard=None,
    ) -> None:
        self.profile_provider = profile_provider
        self.agent = agent or ResourceAdaptationAgent()
        self.coding_port = coding_port
        self.guard = guard or ExperimentSpecificationGuard()
        self.applier = ExecutionConfigApplier()
        self._pending_records = {}

    @property
    def max_attempts(self) -> int:
        return self.agent.max_attempts

    def apply(self, context, command_request, attempts):
        step_id = context.step_id or context.experiment_id
        profile = self.profile_provider.profile(context.run_id, step_id)
        records = tuple(
            record for attempt in attempts for record in attempt.resource_adaptations
        )
        pending = self._pending_records.get((context.run_id, step_id))
        config = (
            records[-1].adapted_config
            if records
            else pending.adapted_config if pending is not None else profile.initial_config
        )
        return self.applier.apply(command_request, config)

    def prepare_for_allocation(self, context, *, orchestration_run_id: str, attempts=()):
        """Adapt paper reference configuration to a smaller scheduler allocation."""
        step_id = context.step_id or context.experiment_id
        profile = self.profile_provider.profile(context.run_id, step_id)
        records = tuple(
            record for attempt in attempts for record in attempt.resource_adaptations
        )
        current = records[-1].adapted_config if records else profile.initial_config
        if profile.allocated_gpu_count >= current.gpu_count:
            return None, None
        decision = self.agent.adapt(
            ResourceAdaptationRequest(
                reason=ResourceAdaptationReason.RESOURCE_SHORTAGE,
                requirement=profile.requirement,
                current_config=current,
                allocated_gpu_count=profile.allocated_gpu_count,
                inventory_gpu_count=profile.inventory_gpu_count,
                capabilities=profile.capabilities,
                prior_adaptations=records,
                run_id=orchestration_run_id,
                step_id=step_id,
                attempt_number=max(1, len(attempts) + 1),
            )
        )
        patch = None
        if decision.outcome is ResourceAdaptationOutcome.PATCH_AND_RETRY:
            patch = self._apply_patch(
                context, decision.patch_instruction, max(1, len(attempts) + 1),
                profile.capabilities,
            )
            if patch.status is PatchStatus.APPLIED:
                decision = decision.model_copy(
                    update={
                        "record": decision.record.model_copy(
                            update={"patch_reference": patch.patch_id}
                        )
                    }
                )
            else:
                decision = ResourceAdaptationDecision(
                    outcome=ResourceAdaptationOutcome.BLOCKED,
                    record=decision.record,
                    reason="gradient accumulation patch was rejected or failed",
                )
        if decision.record is not None:
            self._pending_records[(context.run_id, step_id)] = decision.record
        return decision, patch

    def clear_pending(self, context):
        step_id = context.step_id or context.experiment_id
        self._pending_records.pop((context.run_id, step_id), None)

    def handle_oom(
        self, context, *, orchestration_run_id: str, attempt_number: int, attempts,
    ):
        step_id = context.step_id or context.experiment_id
        profile = self.profile_provider.profile(context.run_id, step_id)
        records = tuple(
            record for attempt in attempts for record in attempt.resource_adaptations
        )
        current = records[-1].adapted_config if records else profile.initial_config
        decision = self.agent.adapt(
            ResourceAdaptationRequest(
                reason=ResourceAdaptationReason.GPU_OOM,
                requirement=profile.requirement,
                current_config=current,
                allocated_gpu_count=profile.allocated_gpu_count,
                inventory_gpu_count=profile.inventory_gpu_count,
                capabilities=profile.capabilities,
                prior_adaptations=records,
                run_id=orchestration_run_id,
                step_id=step_id,
                attempt_number=attempt_number,
            )
        )
        if decision.outcome is not ResourceAdaptationOutcome.PATCH_AND_RETRY:
            return decision, None
        patch = self._apply_patch(
            context, decision.patch_instruction, attempt_number, profile.capabilities,
        )
        if patch.status is PatchStatus.APPLIED:
            record = decision.record.model_copy(update={"patch_reference": patch.patch_id})
            return decision.model_copy(update={"record": record}), patch
        return ResourceAdaptationDecision(
            outcome=ResourceAdaptationOutcome.BLOCKED,
            record=decision.record,
            reason="gradient accumulation patch was rejected or failed",
        ), patch

    def _apply_patch(self, context, instruction, attempt_number, capabilities):
        if self.coding_port is None:
            return self._failed(context.experiment_id, attempt_number, "coding port unavailable")
        try:
            result = self.coding_port.apply(
                CodingRequest(
                    run_id=context.run_id,
                    experiment_id=context.experiment_id,
                    instruction=instruction,
                    allowed_change_categories=("gradient_accumulation", "generated_script"),
                    locked_constraint_keys=tuple(
                        item.key for item in context.constraints.items if item.level.value == "locked"
                    ),
                )
            )
        except Exception as exc:
            return self._failed(context.experiment_id, attempt_number, type(exc).__name__)
        guard = self.guard.validate_patch(context, result.proposed_values)
        disallowed = tuple(
            category for category in result.changed_categories
            if category not in {"gradient_accumulation", "generated_script"}
        )
        violations = (
            *(item.message for item in guard.violations),
            *(f"resource patch category {item!r} is not allowed" for item in disallowed),
            *self._accumulation_contract_violations(result, capabilities),
        )
        return PatchRecord(
            patch_id=result.patch_id,
            status=PatchStatus.APPLIED if not violations else PatchStatus.REJECTED,
            summary=result.summary,
            changed_categories=result.changed_categories,
            violations=tuple(violations),
            artifact=result.artifact,
        )

    @staticmethod
    def _accumulation_contract_violations(result, capabilities):
        proposed = result.proposed_values
        expected = {
            "resource_adaptation:loss_scaling": "divide_by_accumulation_steps",
            "resource_adaptation:optimizer_step_frequency": "accumulation_boundary_or_final_remainder",
            "resource_adaptation:zero_grad_frequency": "optimizer_step",
            "resource_adaptation:remainder_flush": True,
            "resource_adaptation:learning_rate_changed": False,
        }
        if capabilities.has_lr_scheduler:
            expected["resource_adaptation:lr_scheduler_step_frequency"] = "optimizer_step"
        if capabilities.has_gradient_clipping:
            expected["resource_adaptation:gradient_clipping_frequency"] = "optimizer_step"
        if capabilities.has_ema:
            expected["resource_adaptation:ema_update_frequency"] = "optimizer_step"
        violations = [
            f"gradient accumulation patch contract {key!r} is missing or invalid"
            for key, value in expected.items()
            if proposed.get(key) != value
        ]
        normalized = {
            key.lstrip("-").casefold().replace("-", "_")
            for key in proposed
        }
        if normalized & {"lr", "learning_rate"}:
            violations.append("gradient accumulation patch must not change learning rate")
        validations = {item.name: item.passed for item in result.validations}
        for name in (
            "gradient_accumulation_control_flow",
            "gradient_accumulation_smoke_test",
            "locked_scientific_constraints",
        ):
            if validations.get(name) is not True:
                violations.append(f"bounded patch validation {name!r} did not pass")
        return tuple(violations)

    @staticmethod
    def _failed(step_id, attempt_number, cause):
        digest = hashlib.sha256(
            f"{step_id}:{attempt_number}:resource-patch".encode()
        ).hexdigest()[:20]
        return PatchRecord(
            patch_id=f"resource-patch-failure:{digest}",
            status=PatchStatus.FAILED,
            summary="resource adaptation coding request failed",
            violations=(f"resource adaptation patch failed: {cause}",),
        )
