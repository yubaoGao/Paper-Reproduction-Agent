"""Deterministic Task 08 expansion of one locked policy into a small action DAG."""

from __future__ import annotations

import hashlib

from backend.app.domain import (
    CheckpointPolicy,
    EvaluationPolicy,
    ExecutableCommand,
    ExperimentAction,
    ExperimentActionPlan,
    ExperimentActionType,
)


def _id(*parts: object) -> str:
    digest = hashlib.sha256("\x1f".join(str(value) for value in parts).encode()).hexdigest()[:16]
    return f"action:{digest}"


class EvaluationActionPlanner:
    """Build TRAIN/EVALUATE/AGGREGATE only; Task 11 executes the resulting DAG."""

    def build(
        self,
        paper_experiment_id: str,
        policy: EvaluationPolicy,
        train_command: ExecutableCommand,
        evaluation_command: ExecutableCommand | None = None,
    ) -> ExperimentActionPlan:
        if not policy.is_resolved:
            raise ValueError("action planning requires a resolved EvaluationPolicy")
        actions = []
        terminal = []
        for index in range(policy.run_count):
            seed = policy.seeds[index] if policy.seeds else None
            train_id = _id(paper_experiment_id, "train", index, seed)
            has_evaluation = evaluation_command is not None
            actions.append(
                ExperimentAction(
                    action_id=train_id,
                    paper_experiment_id=paper_experiment_id,
                    action_type=ExperimentActionType.TRAIN,
                    command=train_command,
                    seed=seed,
                    produces_checkpoint=policy.checkpoint_policy is not CheckpointPolicy.UNKNOWN,
                    produces_run_result=not has_evaluation,
                    produces_final_result=not has_evaluation and policy.run_count == 1,
                )
            )
            current = train_id
            if has_evaluation:
                evaluate_id = _id(paper_experiment_id, "evaluate", index, seed)
                actions.append(
                    ExperimentAction(
                        action_id=evaluate_id,
                        paper_experiment_id=paper_experiment_id,
                        action_type=ExperimentActionType.EVALUATE,
                        depends_on_action_ids=(train_id,),
                        command=evaluation_command,
                        seed=seed,
                        produces_run_result=True,
                        produces_final_result=policy.run_count == 1,
                    )
                )
                current = evaluate_id
            terminal.append(current)
        if policy.run_count > 1:
            final_id = _id(paper_experiment_id, "aggregate")
            actions.append(
                ExperimentAction(
                    action_id=final_id,
                    paper_experiment_id=paper_experiment_id,
                    action_type=ExperimentActionType.AGGREGATE,
                    depends_on_action_ids=tuple(terminal),
                    produces_final_result=True,
                )
            )
        else:
            final_id = terminal[0]
        return ExperimentActionPlan(
            paper_experiment_id=paper_experiment_id,
            actions=tuple(actions),
            execution_order=tuple(item.action_id for item in actions),
            final_action_id=final_id,
        )
