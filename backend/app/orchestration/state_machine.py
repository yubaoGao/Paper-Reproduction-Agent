"""Strict immutable state transitions for reproduction and step runs."""

from __future__ import annotations

from backend.app.domain import ReproductionRun, RunStatus, StepRun, StepStatus
from backend.app.domain.experiment import utc_now


class InvalidRunTransition(ValueError):
    pass


_RUN_TRANSITIONS = {
    RunStatus.PENDING: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.QUEUED: {RunStatus.PREPARING, RunStatus.CANCELLED},
    RunStatus.PREPARING: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.RUNNING: {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.SUCCEEDED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}

_STEP_TRANSITIONS = {
    StepStatus.PENDING: {StepStatus.READY, StepStatus.BLOCKED, StepStatus.CANCELLED},
    StepStatus.READY: {StepStatus.PREPARING, StepStatus.BLOCKED, StepStatus.CANCELLED},
    StepStatus.PREPARING: {StepStatus.RUNNING, StepStatus.FAILED, StepStatus.CANCELLED},
    StepStatus.RUNNING: {
        StepStatus.VALIDATING,
        StepStatus.RETRYING,
        StepStatus.FAILED,
        StepStatus.CANCELLED,
    },
    StepStatus.VALIDATING: {
        StepStatus.SUCCEEDED,
        StepStatus.PATCHING,
        StepStatus.RETRYING,
        StepStatus.FAILED,
        StepStatus.CANCELLED,
    },
    StepStatus.PATCHING: {StepStatus.RETRYING, StepStatus.FAILED, StepStatus.CANCELLED},
    StepStatus.RETRYING: {StepStatus.RUNNING, StepStatus.FAILED, StepStatus.CANCELLED},
    StepStatus.SUCCEEDED: set(),
    StepStatus.FAILED: set(),
    StepStatus.BLOCKED: set(),
    StepStatus.CANCELLED: set(),
}


class RunStateMachine:
    def transition_run(self, run: ReproductionRun, target: RunStatus, **changes) -> ReproductionRun:
        if target not in _RUN_TRANSITIONS[run.status]:
            raise InvalidRunTransition(f"run transition {run.status.value} -> {target.value} is forbidden")
        values = {"status": target, "revision": run.revision + 1, **changes}
        if target is RunStatus.RUNNING and run.started_at is None:
            values["started_at"] = utc_now()
        if target in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            values.setdefault("finished_at", utc_now())
        return _copy(run, **values)

    def transition_step(
        self,
        run: ReproductionRun,
        step_id: str,
        target: StepStatus,
        **changes,
    ) -> ReproductionRun:
        step = self.step(run, step_id)
        if target not in _STEP_TRANSITIONS[step.status]:
            raise InvalidRunTransition(
                f"step transition {step.status.value} -> {target.value} is forbidden"
            )
        values = {"status": target, **changes}
        if target is StepStatus.RUNNING and step.started_at is None:
            values["started_at"] = utc_now()
        if target in {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED,
            StepStatus.BLOCKED,
            StepStatus.CANCELLED,
        }:
            values.setdefault("finished_at", utc_now())
        return self.replace_step(run, _copy(step, **values))

    @staticmethod
    def step(run: ReproductionRun, step_id: str) -> StepRun:
        try:
            return next(item for item in run.steps if item.step_id == step_id)
        except StopIteration as exc:
            raise KeyError(f"unknown reproduction step {step_id!r}") from exc

    @staticmethod
    def replace_step(run: ReproductionRun, replacement: StepRun) -> ReproductionRun:
        steps = tuple(
            replacement if item.step_id == replacement.step_id else item
            for item in run.steps
        )
        if steps == run.steps:
            raise KeyError(f"unknown reproduction step {replacement.step_id!r}")
        artifacts = tuple(item for step in steps for item in step.artifacts)
        final_results = tuple(step.final_result for step in steps if step.final_result is not None)
        return _copy(
            run,
            steps=steps,
            artifacts=artifacts,
            final_results=final_results,
            revision=run.revision + 1,
        )


def _copy(model, **changes):
    values = model.model_dump(mode="python")
    values.update(changes)
    return type(model).model_validate(values)
