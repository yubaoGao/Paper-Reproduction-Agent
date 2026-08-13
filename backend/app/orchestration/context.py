"""Translate one authoritative plan step into the existing Curie context."""

from __future__ import annotations

from backend.app.domain import ExecutableCommand, ReproductionExecutionPlan
from backend.app.runtime.curie_models import (
    ConstraintLevel,
    CurieConstraint,
    CurieExecutionConstraints,
    CurieExecutionContext,
    ReproductionExecutionMode,
)
from backend.app.runtime.state import run_namespace, run_thread_id


class PlanStepContextFactory:
    def create(self, plan: ReproductionExecutionPlan, step_id: str, runtime_run_id: str, *, action=None):
        experiment = next(item for item in plan.experiments if item.id == step_id)
        command = action.command if action is not None else experiment.resolved_command
        if command is None:
            command = ExecutableCommand(
                program=experiment.command[0],
                arguments=experiment.command[1:],
            )
        dataset = (
            experiment.dataset_requirement.model_dump(mode="json")
            if experiment.dataset_requirement
            else (
                experiment.dataset.model_dump(mode="json")
                if experiment.dataset
                else None
            )
        )
        implementation_id = experiment.metadata.get("implementation_id")
        ablations = (
            dict(experiment.hyperparameters)
            if experiment.task_type.value == "ablation"
            else {}
        )
        locked = (
            self._constraint("experiment_id", experiment.id),
            self._constraint("repository_revision", plan.resolved_commit_sha),
            self._constraint("repository_snapshot_id", plan.repository_snapshot_id),
            self._constraint("implementation_id", implementation_id),
            self._constraint("task_type", experiment.task_type.value),
            self._constraint("dataset", dataset),
            self._constraint("entrypoint", experiment.entrypoint),
            self._constraint("config_ids", list(command.config_ids)),
            self._constraint("command", command.model_dump(mode="json")),
            self._constraint("expected_claim_ids", list(experiment.expected_claim_ids)),
            self._constraint("evaluation_policy",None if experiment.evaluation_policy is None else experiment.evaluation_policy.model_dump(mode="json")),
            self._constraint("action_plan",None if experiment.action_plan is None else experiment.action_plan.model_dump(mode="json")),
            self._constraint("action_id",None if action is None else action.action_id),
            self._constraint("action_type",None if action is None else action.action_type.value),
            self._constraint("seed",None if action is None else action.seed),
            *(self._constraint(f"hyperparameter:{key}", value) for key, value in experiment.hyperparameters.items()),
            self._constraint("ablation_modifications", ablations),
        )
        return CurieExecutionContext(
            mode=ReproductionExecutionMode.REPRODUCTION,
            run_id=runtime_run_id,
            experiment_id=experiment.id,
            step_id=action.action_id if action is not None else experiment.id,
            objective=experiment.description,
            repository_uri=experiment.repository.uri,
            repository_revision=plan.resolved_commit_sha,
            repository_snapshot_id=plan.repository_snapshot_id,
            implementation_id=implementation_id,
            task_type=experiment.task_type.value,
            entrypoint=experiment.entrypoint,
            config_ids=command.config_ids,
            command=command,
            dataset_requirement=dataset,
            environment_requirement=experiment.environment_requirement,
            resource_requirement=experiment.resource_requirement,
            hyperparameters=experiment.hyperparameters,
            ablation_modifications=ablations,
            expected_claim_ids=experiment.expected_claim_ids,
            planner_decisions=tuple(
                item.model_dump(mode="json")
                for item in plan.decisions
                if item.experiment_id in {None, experiment.id}
            ),
            provenance_decision_ids=experiment.provenance_decision_ids,
            constraints=CurieExecutionConstraints(
                items=(
                    *locked,
                    CurieConstraint(
                        key="resource_requirement",
                        value=experiment.resource_requirement.model_dump(mode="json"),
                        level=ConstraintLevel.ADVISORY,
                        source="execution_plan",
                    ),
                    CurieConstraint(
                        key="workspace",
                        value=None,
                        level=ConstraintLevel.RUNTIME_RESOLVED,
                        source="runtime",
                    ),
                )
            ),
            namespace="/".join(run_namespace(runtime_run_id, experiment.id)),
            thread_id=run_thread_id(runtime_run_id, experiment.id),
            execution_instruction=(
                f"Execute authoritative plan {plan.plan_id}, step {experiment.id}; "
                "do not redefine its scientific target or parameters."
            ),
        )

    @staticmethod
    def _constraint(key, value):
        return CurieConstraint(
            key=key,
            value=value,
            level=ConstraintLevel.LOCKED,
            source="execution_plan",
        )
