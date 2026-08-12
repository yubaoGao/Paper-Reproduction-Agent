"""Deterministic DAG routing derived only from ReproductionExecutionPlan."""

from __future__ import annotations

from backend.app.domain import ReproductionRun, StepRun, StepStatus

from .state_machine import RunStateMachine


class ExecutionDispatcher:
    """Select runnable steps with control-first then priority then plan order."""

    def __init__(self, state_machine: RunStateMachine | None = None) -> None:
        self.state_machine = state_machine or RunStateMachine()

    def reconcile(self, run: ReproductionRun) -> ReproductionRun:
        current = run
        for step in run.steps:
            if step.status is not StepStatus.PENDING:
                continue
            parents = tuple(
                self.state_machine.step(current, parent)
                for parent in step.depends_on_step_ids
            )
            failed = tuple(
                parent
                for parent in parents
                if parent.status in {
                    StepStatus.FAILED,
                    StepStatus.BLOCKED,
                    StepStatus.CANCELLED,
                }
            )
            if failed:
                from .failure import dependency_blocked_failure

                current = self.state_machine.transition_step(
                    current,
                    step.step_id,
                    StepStatus.BLOCKED,
                    failure=dependency_blocked_failure(
                        step.step_id,
                        tuple(item.step_id for item in failed),
                    ),
                )
                continue
            if all(parent.status is StepStatus.SUCCEEDED for parent in parents):
                inputs = tuple(item for parent in parents for item in parent.artifacts)
                current = self.state_machine.transition_step(
                    current,
                    step.step_id,
                    StepStatus.READY,
                    input_artifacts=inputs,
                )
        return current

    @staticmethod
    def next_runnable(run: ReproductionRun) -> StepRun | None:
        positions = {value: index for index, value in enumerate(run.manifest.ordered_step_ids)}
        candidates = [item for item in run.steps if item.status is StepStatus.READY]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (
                0 if item.control_first else 1,
                item.priority,
                positions[item.step_id],
            ),
        )
