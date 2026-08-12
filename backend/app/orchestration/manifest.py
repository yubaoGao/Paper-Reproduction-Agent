"""Authoritative immutable manifest construction from Task 08 plans."""

from __future__ import annotations

import hashlib
import json

from backend.app.domain import (
    PlanStatus,
    ReproductionExecutionPlan,
    ReproductionRun,
    RunManifest,
    StepRun,
)


class ExecutionPlanAdmissionError(ValueError):
    pass


def create_reproduction_run(plan: ReproductionExecutionPlan, run_id: str) -> ReproductionRun:
    if plan.status is not PlanStatus.READY:
        raise ExecutionPlanAdmissionError("only a READY execution plan can be dispatched")
    declared_ids = tuple(item.id for item in plan.experiments)
    if not declared_ids:
        raise ExecutionPlanAdmissionError("execution plan must contain at least one experiment")
    if any(item.resolved_command is None and not item.command for item in plan.experiments):
        raise ExecutionPlanAdmissionError(
            "every planned experiment requires an authoritative structured command"
        )
    experiment_ids = tuple(plan.execution_order)
    if set(experiment_ids) != set(declared_ids) or len(experiment_ids) != len(declared_ids):
        raise ExecutionPlanAdmissionError(
            "execution_order must cover every experiment exactly and define manifest order"
        )
    dependency_values = {item.experiment_id: item.depends_on_experiment_ids for item in plan.dependencies}
    by_id = {item.id: item for item in plan.experiments}
    action_mode = any(item.action_plan is not None for item in plan.experiments)
    if action_mode and any(item.action_plan is None for item in plan.experiments):
        raise ExecutionPlanAdmissionError("action-plan execution cannot mix planned and legacy steps")
    if action_mode:
        step_ids = tuple(
            action_id
            for experiment_id in experiment_ids
            for action_id in by_id[experiment_id].action_plan.execution_order
        )
        action_owner = {
            action.action_id: (experiment, action)
            for experiment in plan.experiments
            for action in experiment.action_plan.actions
        }
        dependencies = {}
        for step_id in step_ids:
            experiment, action = action_owner[step_id]
            parents = list(action.depends_on_action_ids)
            if not parents:
                for parent_experiment_id in dependency_values.get(experiment.id, ()):
                    parents.append(by_id[parent_experiment_id].action_plan.final_action_id)
            dependencies[step_id] = tuple(dict.fromkeys(parents))
        required_final_result_step_ids = tuple(
            by_id[experiment_id].action_plan.final_action_id for experiment_id in experiment_ids
        )
    else:
        step_ids = experiment_ids
        dependencies = {step_id: tuple(dependency_values.get(step_id, ())) for step_id in experiment_ids}
        action_owner = {}
        required_final_result_step_ids = tuple(
            item.id for item in plan.experiments
            if bool(item.metadata.get("requires_final_result"))
        )
    serialized = plan.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(serialized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = RunManifest(
        plan_id=plan.plan_id,
        reproduction_specification_id=plan.reproduction_specification_id,
        repository_snapshot_id=plan.repository_snapshot_id,
        resolved_commit_sha=plan.resolved_commit_sha,
        ordered_step_ids=step_ids,
        dependencies=dependencies,
        required_final_result_step_ids=required_final_result_step_ids,
        plan_digest=f"sha256:{digest}",
    )
    if action_mode:
        steps=tuple(
            StepRun(step_id=step_id,experiment_id=action_owner[step_id][0].id,action_type=action_owner[step_id][1].action_type,seed=action_owner[step_id][1].seed,depends_on_step_ids=dependencies[step_id],priority=_priority(action_owner[step_id][0].metadata),control_first=("control" in {item.casefold() for item in action_owner[step_id][0].tags} or str(action_owner[step_id][0].metadata.get("group","")).casefold()=="control"))
            for step_id in step_ids
        )
    else:
        steps = tuple(
            StepRun(
                step_id=experiment.id,
                experiment_id=experiment.id,
                depends_on_step_ids=dependencies[experiment.id],
                priority=_priority(experiment.metadata),
                control_first=(
                    "control" in {item.casefold() for item in experiment.tags}
                    or str(experiment.metadata.get("group", "")).casefold() == "control"
                ),
            )
            for experiment in (by_id[step_id] for step_id in experiment_ids)
        )
    return ReproductionRun(
        run_id=run_id,
        plan_id=plan.plan_id,
        manifest=manifest,
        steps=steps,
    )


def verify_plan(run: ReproductionRun, plan: ReproductionExecutionPlan) -> None:
    candidate = create_reproduction_run(plan, run.run_id).manifest
    if candidate.plan_digest != run.manifest.plan_digest:
        raise ExecutionPlanAdmissionError("execution plan no longer matches the run manifest")


def _priority(metadata) -> int:
    value = metadata.get("priority", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value
