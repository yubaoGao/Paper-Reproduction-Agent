"""Patch coordination through the Task 10 CodingAgentPort boundary only."""

from __future__ import annotations

import hashlib

from backend.app.domain import FailureRecord, PatchRecord, PatchStatus
from backend.app.runtime.curie_models import CodingRequest, CurieExecutionContext
from backend.app.runtime.guard import ExperimentSpecificationGuard


_ALLOWED_PATCH_CATEGORIES = (
    "import",
    "path",
    "api_mismatch",
    "runtime_error",
    "generated_script",
)


class PatchCoordinator:
    def __init__(self, coding_port, *, guard=None) -> None:
        self.coding_port = coding_port
        self.guard = guard or ExperimentSpecificationGuard()

    def apply(
        self,
        context: CurieExecutionContext,
        failure: FailureRecord,
        attempt_number: int,
    ) -> PatchRecord:
        try:
            result = self.coding_port.apply(
                CodingRequest(
                    run_id=context.run_id,
                    experiment_id=context.experiment_id,
                    instruction=(
                        f"Repair {failure.category.value} failure {failure.code} in the "
                        "current run-private repository workspace without changing any "
                        "locked scientific constraint."
                    ),
                    allowed_change_categories=_ALLOWED_PATCH_CATEGORIES,
                    locked_constraint_keys=tuple(
                        item.key
                        for item in context.constraints.items
                        if item.level.value == "locked"
                    ),
                )
            )
        except Exception as exc:
            return self._failed(
                context.experiment_id,
                attempt_number,
                type(exc).__name__,
            )
        guard = self.guard.validate_patch(context, result.proposed_values)
        disallowed = tuple(
            item for item in result.changed_categories if item not in _ALLOWED_PATCH_CATEGORIES
        )
        violations = (
            *(item.message for item in guard.violations),
            *(f"patch category {item!r} is not allowed" for item in disallowed),
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
    def _failed(step_id: str, attempt_number: int, exception_type: str) -> PatchRecord:
        digest = hashlib.sha256(f"{step_id}:{attempt_number}:patch".encode()).hexdigest()[:20]
        return PatchRecord(
            patch_id=f"patch-failure:{digest}",
            status=PatchStatus.FAILED,
            summary="coding adapter failed to produce a patch",
            violations=(f"coding adapter failed with {exception_type}",),
        )
