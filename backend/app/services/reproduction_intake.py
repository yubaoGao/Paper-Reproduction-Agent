"""Application use case for catalog-bounded reproduction experiment intake."""

from __future__ import annotations

from typing import Protocol

from backend.app.domain import (
    GoalResolutionResult,
    GoalResolutionStatus,
    PaperExperimentCatalog,
    UserReproductionGoal,
)


class GoalResolver(Protocol):
    def resolve(
        self,
        catalog: PaperExperimentCatalog,
        goal: UserReproductionGoal,
    ) -> GoalResolutionResult: ...


class ReproductionIntakeError(RuntimeError):
    pass


class ReproductionIntakeService:
    """Compose user goal resolution into one authoritative WHICH result."""

    def __init__(self, resolver: GoalResolver | None = None) -> None:
        if resolver is None:
            # Local import prevents the services/agents package initializers from
            # creating a dependency cycle during planner imports.
            from backend.app.agents.paper.goals import ReproductionGoalResolver

            resolver = ReproductionGoalResolver()
        self.resolver = resolver

    def intake(
        self,
        goal: UserReproductionGoal,
        catalog: PaperExperimentCatalog,
    ) -> GoalResolutionResult:
        result = self.resolver.resolve(catalog, goal)
        if result.selection is None:
            raise ReproductionIntakeError("goal resolver omitted ExperimentSelection")
        if result.selection.resolution_status is not result.status:
            raise ReproductionIntakeError("selection status differs from intake status")
        if result.status is GoalResolutionStatus.RESOLVED:
            selected = result.selection.selected_experiment_ids
            if result.specification is None:
                raise ReproductionIntakeError("resolved intake omitted specification")
            if tuple(result.specification.selected_experiment_ids) != tuple(selected):
                raise ReproductionIntakeError(
                    "specification differs from authoritative experiment selection"
                )
            bound = tuple(target.paper_experiment_id for target in result.specification.targets)
            if bound != tuple(selected):
                raise ReproductionIntakeError(
                    "specification targets do not exactly bind the selection"
                )
        return result

    resolve = intake
