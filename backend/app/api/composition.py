"""Adapters composing the already implemented scientific-analysis services."""

from __future__ import annotations

import hashlib
from pathlib import Path

from backend.app.domain import (
    GoalResolutionStatus,
    IntakeAnalysisPhase,
    PaperReference,
    PaperSourceType,
    RepositoryReference,
    RepositorySourceType,
    ReproductionEventType,
    UserReproductionGoal,
)
from backend.app.llm.budget import analysis_stage
from backend.app.services import IntakeAnalysis


class ExistingServicesAnalysisPipeline:
    """Thin sequencing adapter; it contains no paper/repository/planning logic."""

    def __init__(
        self, *, paper_ingestion, paper_extractor, repository_analyzer,
        alignment_agent, goal_intake, planner,
    ) -> None:
        self.paper_ingestion = paper_ingestion
        self.paper_extractor = paper_extractor
        self.repository_analyzer = repository_analyzer
        self.alignment_agent = alignment_agent
        self.goal_intake = goal_intake
        self.planner = planner

    def analyze(
        self, *, intake_id, source_filename, paper_pdf, repository_url, goal,
        on_event=None, on_phase=None, on_checkpoint=None, on_snapshot=None,
        paper=None, paper_catalog=None, paper_document=None,
        repository_catalog=None, alignment_catalog=None, repository_snapshot=None,
    ):
        def emit(event_type, payload):
            if on_event is not None:
                on_event(event_type, payload)

        def phase(value: IntakeAnalysisPhase):
            if on_phase is not None:
                on_phase(value)

        def checkpoint(fields: dict):
            if on_checkpoint is not None:
                on_checkpoint(fields)

        digest = hashlib.sha256(paper_pdf).hexdigest()
        paper = paper or PaperReference(
            id=f"paper:{digest[:24]}", title=source_filename,
            source_type=PaperSourceType.PDF_UPLOAD,
            source_uri=f"upload:{intake_id}",
        )
        document = paper_document
        if paper_catalog is None or document is None:
            phase(IntakeAnalysisPhase.PAPER_PARSING)
            emit(ReproductionEventType.PAPER_ANALYSIS_STARTED, {"filename": source_filename})
            with analysis_stage("paper_parsing"):
                document = self.paper_ingestion.ingest(paper, upload=paper_pdf)
            phase(IntakeAnalysisPhase.PAPER_EXTRACTING)
            with analysis_stage("paper_extracting"):
                paper_result = self.paper_extractor.extract(document)
            paper_catalog = paper_result.catalog
            emit(ReproductionEventType.PAPER_ANALYSIS_COMPLETED, {"paper_id": paper.id})
            checkpoint({
                "paper": paper, "paper_document": document, "paper_catalog": paper_catalog,
                "current_phase": IntakeAnalysisPhase.PAPER_EXTRACTING,
            })

        phase(IntakeAnalysisPhase.GOAL_RESOLVING)
        emit(ReproductionEventType.GOAL_RESOLUTION_STARTED, {"goal": goal})
        with analysis_stage("goal_resolving"):
            goal_result = self.goal_intake.intake(
                UserReproductionGoal(goal_id=f"goal:{intake_id}", text=goal),
                paper_catalog,
            )
        emit(ReproductionEventType.GOAL_RESOLUTION_COMPLETED, {
            "status": goal_result.status.value,
            "candidate_experiment_ids": list(goal_result.candidate_experiment_ids),
        })
        checkpoint({"goal_resolution": goal_result, "current_phase": IntakeAnalysisPhase.GOAL_RESOLVING})

        if goal_result.status is not GoalResolutionStatus.RESOLVED:
            return IntakeAnalysis(
                paper=paper, paper_catalog=paper_catalog, paper_document=document,
                repository_catalog=None, alignment_catalog=None,
                goal_resolution=goal_result, repository_snapshot=None,
            )

        repository = RepositoryReference(
            repository_id=f"repository:{hashlib.sha256(repository_url.encode()).hexdigest()[:24]}",
            source_type=RepositorySourceType.GIT_URL, source_uri=repository_url,
        )
        if repository_catalog is None or repository_snapshot is None:
            phase(IntakeAnalysisPhase.REPOSITORY_ANALYZING)
            emit(ReproductionEventType.REPOSITORY_ANALYSIS_STARTED, {"repository_id": repository.repository_id})
            with analysis_stage("repository_analyzing"):
                repository_result = self.repository_analyzer.analyze(
                    repository,
                    paper_catalog=paper_catalog,
                    reproduction_specification=goal_result.specification,
                )
            repository_catalog = repository_result.catalog
            repository_snapshot = repository_result.snapshot
            emit(ReproductionEventType.REPOSITORY_ANALYSIS_COMPLETED, {
                "repository_catalog_id": repository_catalog.catalog_id,
                "snapshot_id": repository_catalog.snapshot_id,
            })
            if on_snapshot is not None:
                on_snapshot(repository_snapshot)
            checkpoint({
                "repository_catalog": repository_catalog,
                "current_phase": IntakeAnalysisPhase.REPOSITORY_ANALYZING,
            })

        if alignment_catalog is None:
            phase(IntakeAnalysisPhase.ALIGNING)
            emit(ReproductionEventType.ALIGNMENT_STARTED, {
                "paper_catalog_id": paper_catalog.catalog_id,
                "repository_catalog_id": repository_catalog.catalog_id,
            })
            with analysis_stage("aligning"):
                alignment_result = self.alignment_agent.align(
                    paper_catalog, repository_catalog,
                    reproduction_specification=goal_result.specification,
                    paper_document=document,
                )
            alignment_catalog = alignment_result.catalog
            emit(ReproductionEventType.ALIGNMENT_COMPLETED, {
                "alignment_catalog_id": alignment_catalog.catalog_id,
            })
            checkpoint({
                "alignment_catalog": alignment_catalog,
                "current_phase": IntakeAnalysisPhase.ALIGNING,
            })

        return IntakeAnalysis(
            paper=paper, paper_catalog=paper_catalog,
            paper_document=document,
            repository_catalog=repository_catalog,
            alignment_catalog=alignment_catalog,
            goal_resolution=goal_result,
            repository_snapshot=repository_snapshot,
        )

    def clarify(self, *, intake, answers):
        enriched = intake.user_goal + "\nUser clarification:\n" + "\n".join(answers)
        return self.resolve_goal(
            catalog=intake.paper_catalog,
            goal=UserReproductionGoal(goal_id=f"goal:{intake.intake_id}", text=enriched),
        )

    def resolve_goal(self, *, catalog, goal):
        return self.goal_intake.intake(goal, catalog)

    def resolve_experiment_ids(self, *, catalog, goal, experiment_ids):
        resolver = getattr(self.goal_intake, "resolver", None)
        if resolver is None or not hasattr(resolver, "resolve_from_ids"):
            from backend.app.agents.paper.goals import ReproductionGoalResolver
            resolver = ReproductionGoalResolver()
        return resolver.resolve_from_ids(catalog, goal, tuple(experiment_ids))

    def plan(self, *, intake=None, specification=None, paper_catalog=None, repository_catalog=None, alignment_catalog=None):
        if intake is not None:
            specification = intake.goal_resolution.specification
            paper_catalog = intake.paper_catalog
            repository_catalog = intake.repository_catalog
            alignment_catalog = intake.alignment_catalog
        return self.planner.plan(
            specification, paper_catalog, repository_catalog, alignment_catalog,
        ).plan


def build_default_analysis_pipeline(*, workspace_root: str | Path = "workspace"):
    """Compose the existing production analysis modules without test doubles."""
    from backend.app.agents.alignment import PaperCodeAlignmentAgent
    from backend.app.agents.paper import PaperExperimentExtractionAgent
    from backend.app.agents.planner import ReproductionPlannerAgent
    from backend.app.agents.repository import RepositoryAnalyzerAgent
    from backend.app.infrastructure.paper import (
        DoclingPaperParser, PypdfPaperParser, SecurePaperSourceResolver,
    )
    from backend.app.llm import (
        DeepSeekStructuredLLMAdapter, LLMPlatformSettings, LLMRouter,
        QwenStructuredLLMAdapter,
    )
    from backend.app.services import (
        CompositePaperParser, PaperIngestionService, PaperIngestionSettings,
        RepositoryAnalysisSettings, ReproductionIntakeService,
    )

    root = Path(workspace_root)
    llm = LLMPlatformSettings.from_env()
    router = LLMRouter(
        DeepSeekStructuredLLMAdapter(llm.primary),
        QwenStructuredLLMAdapter(llm.fast),
    )
    paper_settings = PaperIngestionSettings(
        figure_artifact_directory=root / "paper-assets",
    )
    repository_settings = RepositoryAnalysisSettings(
        materialization_root=root / "repositories",
    )
    return ExistingServicesAnalysisPipeline(
        paper_ingestion=PaperIngestionService(
            SecurePaperSourceResolver(paper_settings),
            CompositePaperParser(
                DoclingPaperParser(paper_settings),
                PypdfPaperParser(paper_settings),
            ),
        ),
        paper_extractor=PaperExperimentExtractionAgent(router),
        repository_analyzer=RepositoryAnalyzerAgent(
            router, settings=repository_settings,
        ),
        alignment_agent=PaperCodeAlignmentAgent(router),
        goal_intake=ReproductionIntakeService(),
        planner=ReproductionPlannerAgent(router),
    )
