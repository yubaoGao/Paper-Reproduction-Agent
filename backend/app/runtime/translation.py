"""Translate platform specifications into structured Curie reproduction state."""

from __future__ import annotations

from backend.app.domain import ExecutableCommand, RunRequest

from .curie_models import (
    ConstraintLevel,
    CurieConstraint,
    CurieExecutionConstraints,
    CurieExecutionContext,
    ReproductionExecutionMode,
)
from .state import run_namespace, run_thread_id


class CurieInputTranslator:
    """Build planner-authoritative Curie input without consulting an LLM."""

    def translate(self, request: RunRequest) -> CurieExecutionContext:
        specification = request.experiment
        command = self._resolve_command(request)
        dataset = (
            specification.dataset_requirement.model_dump(mode="json")
            if specification.dataset_requirement
            else (
                request.dataset_source.model_dump(mode="json")
                if request.dataset_source
                else None
            )
        )
        config_ids = command.config_ids
        snapshot_id = specification.metadata.get("repository_snapshot_id")
        implementation_id = specification.metadata.get("implementation_id")
        paper_experiment_id = specification.metadata.get("paper_experiment_id")
        ablations = (
            dict(specification.hyperparameters)
            if specification.task_type.value == "ablation"
            else {}
        )

        locked = [
            self._constraint("experiment_id", specification.id),
            self._constraint("paper_experiment_id", paper_experiment_id),
            self._constraint("repository_revision", request.repository_source.revision),
            self._constraint("repository_snapshot_id", snapshot_id),
            self._constraint("implementation_id", implementation_id),
            self._constraint("task_type", specification.task_type.value),
            self._constraint("dataset", dataset),
            self._constraint("entrypoint", specification.entrypoint),
            self._constraint("config_ids", list(config_ids)),
            self._constraint("command", command.model_dump(mode="json")),
            self._constraint(
                "expected_claim_ids",
                list(specification.expected_claim_ids),
            ),
        ]
        locked.extend(
            self._constraint(
                f"hyperparameter:{key}",
                value,
                source="planner_decision",
            )
            for key, value in specification.hyperparameters.items()
        )
        locked.append(self._constraint("ablation_modifications", ablations))

        constraints = CurieExecutionConstraints(
            items=tuple(
                [
                    *locked,
                    CurieConstraint(
                        key="resource_requirement",
                        value=specification.resource_requirement.model_dump(mode="json"),
                        level=ConstraintLevel.ADVISORY,
                        source="experiment_specification",
                    ),
                    CurieConstraint(
                        key="workspace",
                        value=None,
                        level=ConstraintLevel.RUNTIME_RESOLVED,
                        source="runtime",
                    ),
                ]
            )
        )
        instruction = (
            f"Execute the locked reproduction specification {specification.id}. "
            "Organize validation and retries without changing its scientific "
            "target or locked values."
        )

        return CurieExecutionContext(
            mode=ReproductionExecutionMode.REPRODUCTION,
            run_id=request.run_id,
            experiment_id=specification.id,
            objective=specification.description,
            repository_uri=request.repository_source.uri,
            repository_revision=request.repository_source.revision,
            repository_snapshot_id=snapshot_id,
            implementation_id=implementation_id,
            task_type=specification.task_type.value,
            entrypoint=specification.entrypoint,
            config_ids=config_ids,
            command=command,
            dataset_requirement=dataset,
            environment_requirement=specification.environment_requirement,
            resource_requirement=specification.resource_requirement,
            hyperparameters=specification.hyperparameters,
            ablation_modifications=ablations,
            expected_claim_ids=specification.expected_claim_ids,
            expected_claims=tuple(
                claim.model_dump(mode="json") for claim in request.expected_claims
            ),
            expected_metrics=specification.expected_metrics,
            provenance_decision_ids=specification.provenance_decision_ids,
            planner_decisions=tuple(
                decision.model_dump(mode="json")
                for decision in request.planner_decisions
            ),
            constraints=constraints,
            namespace="/".join(run_namespace(request.run_id, specification.id)),
            thread_id=run_thread_id(request.run_id, specification.id),
            execution_instruction=instruction,
        )

    @staticmethod
    def _resolve_command(request: RunRequest) -> ExecutableCommand:
        command = request.experiment.resolved_command
        if command is not None:
            return command
        tokens = request.experiment.command
        if not tokens:
            raise ValueError(
                "experiment specification has no structured or legacy command"
            )
        return ExecutableCommand(
            program=tokens[0],
            arguments=tokens[1:],
            entrypoint_id=None,
        )

    @staticmethod
    def _constraint(
        key: str,
        value,
        *,
        source: str = "experiment_specification",
    ) -> CurieConstraint:
        return CurieConstraint(
            key=key,
            value=value,
            level=ConstraintLevel.LOCKED,
            source=source,
        )
