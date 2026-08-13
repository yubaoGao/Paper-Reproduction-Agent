"""SQL-free GPU scheduler contracts and deterministic resource adaptation."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Protocol, runtime_checkable

from backend.app.domain import (
    AdaptedExecutionConfig, GPUDevice, GPULease, GPUSchedulingRequest,
    GPURequirement, MultiGPUSemantics, PrecisionMode, ResourceAdaptationDecision,
    ResourceAdaptationOutcome, ResourceAdaptationRecord,
    ResourceAdaptationRequest, SemanticImpact,
)
from backend.app.runtime.curie_models import CommandExecutionRequest


class GPUAllocationConflictError(RuntimeError):
    pass


class GPULeaseLostError(GPUAllocationConflictError):
    pass


class GPUInventoryUnavailableError(RuntimeError):
    pass


@runtime_checkable
class GPUInventoryProvider(Protocol):
    def discover(self) -> tuple[GPUDevice, ...]: ...


@runtime_checkable
class GPUScheduler(Protocol):
    def refresh_inventory(self, devices: tuple[GPUDevice, ...]) -> None: ...
    def submit(self, request: GPUSchedulingRequest) -> None: ...
    def allocate_next(
        self, worker_id: str, *, lease_seconds: int, now: datetime | None = None,
    ) -> GPULease | None: ...
    def heartbeat(
        self, lease_token: str, worker_id: str, *, lease_seconds: int,
        now: datetime | None = None,
    ) -> GPULease: ...
    def release(
        self, lease_token: str, worker_id: str, *, now: datetime | None = None,
    ) -> None: ...
    def recover_expired(self, *, now: datetime | None = None) -> int: ...
    def get_active_lease(self, job_id: str, step_id: str) -> GPULease | None: ...


@runtime_checkable
class WorkerGPUResourcePort(Protocol):
    def defer(
        self, job_id: str, step_id: str, worker_id: str, job_lease_token: str,
        requirement, *, now: datetime | None = None,
    ) -> None: ...
    def release_job(
        self, job_id: str, worker_id: str, *, now: datetime | None = None,
    ) -> None: ...


class ResourceAdaptationAgent:
    """Bounded policy which never changes model, dataset, loss, or evaluation."""

    def __init__(self, *, max_adaptations: int = 6) -> None:
        if not 1 <= max_adaptations <= 10:
            raise ValueError("max_adaptations must be between one and ten")
        self.max_adaptations = max_adaptations

    @property
    def max_attempts(self) -> int:
        return self.max_adaptations + 1

    def adapt(self, request: ResourceAdaptationRequest) -> ResourceAdaptationDecision:
        if len(request.prior_adaptations) >= self.max_adaptations:
            return self._terminal(
                ResourceAdaptationOutcome.RESOURCE_UNSATISFIABLE,
                "bounded GPU resource adaptations were exhausted",
            )
        requirement = request.requirement
        allocated = request.allocated_gpu_count
        if (
            requirement.multi_gpu_semantics is MultiGPUSemantics.SEMANTICALLY_REQUIRED
            and allocated < requirement.minimum_gpu_count
        ):
            if request.inventory_gpu_count >= requirement.minimum_gpu_count:
                return ResourceAdaptationDecision(
                    outcome=ResourceAdaptationOutcome.WAITING_FOR_RESOURCES,
                    updated_requirement=requirement,
                    reason="algorithmically required multi-GPU capacity is not currently free",
                )
            return self._terminal(
                ResourceAdaptationOutcome.RESOURCE_UNSATISFIABLE,
                "server inventory cannot satisfy algorithmically required multi-GPU execution",
            )
        if allocated < requirement.minimum_gpu_count:
            if request.inventory_gpu_count >= requirement.minimum_gpu_count:
                return ResourceAdaptationDecision(
                    outcome=ResourceAdaptationOutcome.WAITING_FOR_RESOURCES,
                    updated_requirement=requirement,
                    reason="minimum GPU capacity is not currently free",
                )
            return self._terminal(
                ResourceAdaptationOutcome.RESOURCE_UNSATISFIABLE,
                "server inventory is below the minimum GPU requirement",
            )
        target_gpu_count = max(1, allocated)
        current = request.current_config
        if request.reason.value == "resource_shortage":
            if requirement.multi_gpu_semantics is MultiGPUSemantics.SEMANTICALLY_REQUIRED:
                return self._terminal(
                    ResourceAdaptationOutcome.BLOCKED,
                    "multi-GPU semantics forbid reducing the allocated GPU count",
                )
            target_batch = max(
                1, current.per_gpu_batch_size * target_gpu_count // current.gpu_count,
            )
        else:
            target_batch = max(1, current.per_gpu_batch_size // 2)

        if target_batch < current.per_gpu_batch_size or target_gpu_count != current.gpu_count:
            return self._batch_decision(request, target_gpu_count, target_batch)

        capabilities = request.capabilities
        if current.precision is PrecisionMode.FP32 and capabilities.supports_bf16:
            return self._feature_decision(request, precision=PrecisionMode.BF16)
        if current.precision is PrecisionMode.FP32 and capabilities.supports_fp16:
            return self._feature_decision(request, precision=PrecisionMode.FP16)
        if not current.activation_checkpointing and capabilities.supports_activation_checkpointing:
            return self._feature_decision(request, activation_checkpointing=True)
        if not current.cpu_offload and capabilities.supports_cpu_offload:
            return self._feature_decision(request, cpu_offload=True)

        if current.gpu_count == 1 and requirement.multi_gpu_semantics is MultiGPUSemantics.PERFORMANCE_ONLY:
            increased = max(2, requirement.minimum_gpu_count)
            if request.inventory_gpu_count >= increased:
                updated = requirement.model_copy(
                    update={
                        "minimum_gpu_count": increased,
                        "preferred_gpu_count": max(increased, requirement.preferred_gpu_count),
                    }
                )
                accumulation = current.gradient_accumulation_steps
                desired = current.effective_batch_size
                divisor = current.per_gpu_batch_size * increased
                if desired % divisor == 0:
                    accumulation = max(1, desired // divisor)
                overrides = dict(current.argument_overrides)
                if (
                    request.capabilities.gradient_accumulation_argument
                    and accumulation != current.gradient_accumulation_steps
                ):
                    overrides[request.capabilities.gradient_accumulation_argument] = accumulation
                adapted = current.model_copy(
                    update={
                        "gpu_count": increased,
                        "gradient_accumulation_steps": accumulation,
                        "argument_overrides": overrides,
                    }
                )
                return ResourceAdaptationDecision(
                    outcome=ResourceAdaptationOutcome.WAITING_FOR_RESOURCES,
                    record=self._record(request, adapted),
                    updated_requirement=updated,
                    reason="single-GPU adaptations were exhausted; wait for two GPUs",
                )
        return self._terminal(
            ResourceAdaptationOutcome.RESOURCE_UNSATISFIABLE,
            "no evidence-backed bounded GPU adaptation remains",
        )

    def _batch_decision(self, request, gpu_count: int, batch_size: int):
        current = request.current_config
        capabilities = request.capabilities
        desired = current.effective_batch_size
        batch_size, accumulation = self._preserving_pair(
            desired, gpu_count, batch_size, current.gradient_accumulation_steps,
        )
        needs_accumulation = accumulation > current.gradient_accumulation_steps
        unsupported_accumulation = (
            needs_accumulation and not capabilities.supports_gradient_accumulation
        )
        patch_needed = unsupported_accumulation and capabilities.coding_patch_available
        if unsupported_accumulation and not capabilities.coding_patch_available:
            accumulation = current.gradient_accumulation_steps
        overrides = dict(current.argument_overrides)
        if capabilities.batch_size_argument:
            overrides[capabilities.batch_size_argument] = batch_size
        elif batch_size != current.per_gpu_batch_size:
            return self._terminal(
                ResourceAdaptationOutcome.BLOCKED,
                "repository evidence does not identify a batch-size execution control",
            )
        if capabilities.gradient_accumulation_argument and accumulation != current.gradient_accumulation_steps:
            overrides[capabilities.gradient_accumulation_argument] = accumulation
        adapted = current.model_copy(
            update={
                "gpu_count": gpu_count,
                "per_gpu_batch_size": batch_size,
                "gradient_accumulation_steps": accumulation,
                "argument_overrides": overrides,
            }
        )
        instruction = None
        outcome = ResourceAdaptationOutcome.RETRY
        if patch_needed:
            outcome = ResourceAdaptationOutcome.PATCH_AND_RETRY
            instruction = (
                "Add gradient accumulation support only in the current run-private workspace; "
                f"expose {accumulation} accumulation steps without changing model, dataset, loss, "
                "evaluation behavior, or learning rate. Divide each micro-batch loss by the "
                "accumulation count before backward; run optimizer.step and zero_grad only at an "
                "accumulation boundary or final remainder; step any LR scheduler, gradient clipping, "
                "and EMA at optimizer-update frequency. Flush final remainder micro-batches. Return "
                "the structured resource_adaptation:* control-flow contract and passing bounded "
                "control-flow, smoke, and locked-constraint validations."
            )
        record = self._record(request, adapted)
        return ResourceAdaptationDecision(
            outcome=outcome,
            record=record,
            patch_instruction=instruction,
            reason=(
                "reduced micro batch and compensated with gradient accumulation"
                if adapted.effective_batch_size == desired
                else "reduced effective batch as a controlled deviation"
            ),
        )

    def _feature_decision(self, request, **change):
        current = request.current_config
        capabilities = request.capabilities
        overrides = dict(current.argument_overrides)
        if "precision" in change and capabilities.precision_argument:
            overrides[capabilities.precision_argument] = change["precision"].value
        if change.get("activation_checkpointing") and capabilities.activation_checkpointing_argument:
            overrides[capabilities.activation_checkpointing_argument] = True
        if change.get("cpu_offload") and capabilities.cpu_offload_argument:
            overrides[capabilities.cpu_offload_argument] = True
        adapted = current.model_copy(update={**change, "argument_overrides": overrides})
        return ResourceAdaptationDecision(
            outcome=ResourceAdaptationOutcome.RETRY,
            record=self._record(request, adapted),
            reason="enabled an evidence-backed memory-saving repository capability",
        )

    @staticmethod
    def _preserving_pair(desired: int, gpu_count: int, maximum_batch: int, minimum_accumulation: int):
        for batch in range(maximum_batch, 0, -1):
            divisor = batch * gpu_count
            if desired % divisor == 0:
                accumulation = desired // divisor
                if accumulation >= minimum_accumulation:
                    return batch, accumulation
        return maximum_batch, max(
            minimum_accumulation, math.ceil(desired / (maximum_batch * gpu_count))
        )

    @staticmethod
    def _record(request, adapted):
        current = request.current_config
        fields = (
            "gpu_count", "per_gpu_batch_size", "gradient_accumulation_steps",
            "precision", "activation_checkpointing", "cpu_offload",
        )
        changed = tuple(name for name in fields if getattr(current, name) != getattr(adapted, name))
        digest = hashlib.sha256(
            f"{request.run_id}:{request.step_id}:{request.attempt_number}:{len(request.prior_adaptations)}".encode()
        ).hexdigest()[:20]
        impact = (
            SemanticImpact.PRESERVED
            if current.effective_batch_size == adapted.effective_batch_size
            else SemanticImpact.CONTROLLED_DEVIATION
        )
        warnings = () if impact is SemanticImpact.PRESERVED else ("EFFECTIVE_BATCH_REDUCED",)
        return ResourceAdaptationRecord(
            adaptation_id=f"resource-adaptation:{digest}",
            reason=request.reason,
            original_config=current,
            adapted_config=adapted,
            changed_parameters=changed,
            effective_batch_before=current.effective_batch_size,
            effective_batch_after=adapted.effective_batch_size,
            gpu_count_before=current.gpu_count,
            gpu_count_after=adapted.gpu_count,
            semantic_impact=impact,
            evidence=request.capabilities.evidence or request.requirement.evidence,
            warnings=warnings,
            run_id=request.run_id,
            step_id=request.step_id,
            attempt_number=request.attempt_number,
        )

    @staticmethod
    def _terminal(outcome, reason):
        return ResourceAdaptationDecision(outcome=outcome, reason=reason)


class MultiGPUSemanticsAnalyzer:
    """Small evidence classifier; common phrases are hints, never silent assumptions."""

    _SEMANTIC_MARKERS = (
        "cross-gpu negative", "cross gpu negative", "all_gather negatives",
        "tensor parallel", "model parallel", "pipeline parallel",
        "cross-device communication", "cross device communication",
    )
    _PERFORMANCE_MARKERS = (
        "distributed data parallel", "torch.nn.parallel.distributeddataparallel",
        "data parallel only", "ordinary ddp", "replicated model",
    )

    def build_requirement(
        self,
        *,
        reference_gpu_count: int,
        estimated_memory_mb: int | None,
        reference_batch_size: int | None,
        gradient_accumulation_steps: int,
        repository_evidence: tuple[str, ...],
    ):
        if reference_gpu_count < 0:
            raise ValueError("reference GPU count cannot be negative")
        text = "\n".join(repository_evidence).casefold()
        semantic = any(marker in text for marker in self._SEMANTIC_MARKERS)
        performance = any(marker in text for marker in self._PERFORMANCE_MARKERS)
        if reference_gpu_count <= 1:
            semantics = MultiGPUSemantics.PERFORMANCE_ONLY
            minimum = reference_gpu_count
            warnings = ()
        elif semantic:
            semantics = MultiGPUSemantics.SEMANTICALLY_REQUIRED
            minimum = reference_gpu_count
            warnings = ()
        elif performance:
            semantics = MultiGPUSemantics.PERFORMANCE_ONLY
            minimum = 1
            warnings = ()
        else:
            semantics = MultiGPUSemantics.SEMANTICALLY_REQUIRED
            minimum = reference_gpu_count
            warnings = ("MULTI_GPU_SEMANTICS_INCONCLUSIVE_CONSERVATIVE_MINIMUM",)
        evidence = (*repository_evidence, *warnings)
        return GPURequirement(
            reference_gpu_count=reference_gpu_count,
            preferred_gpu_count=reference_gpu_count,
            minimum_gpu_count=minimum,
            estimated_memory_mb=estimated_memory_mb,
            reference_batch_size=reference_batch_size,
            gradient_accumulation_steps=gradient_accumulation_steps,
            multi_gpu_semantics=semantics,
            evidence=evidence,
        )


class ExecutionConfigApplier:
    """Apply evidence-backed CLI overrides without parsing or invoking a shell."""

    @staticmethod
    def apply(request: CommandExecutionRequest, config: AdaptedExecutionConfig) -> CommandExecutionRequest:
        argv = list(request.argv)
        for argument, value in sorted(config.argument_overrides.items()):
            if not argument.startswith("-"):
                raise ValueError("resource adaptation argument must be an explicit CLI option")
            rendered = str(value).lower() if isinstance(value, bool) else str(value)
            if argument in argv:
                index = argv.index(argument)
                if isinstance(value, bool):
                    if not value:
                        argv.pop(index)
                elif index + 1 < len(argv):
                    argv[index + 1] = rendered
                else:
                    argv.append(rendered)
            elif isinstance(value, bool):
                if value:
                    argv.append(argument)
            else:
                argv.extend((argument, rendered))
        return request.model_copy(update={"argv": tuple(argv)})
