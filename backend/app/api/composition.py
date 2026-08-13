"""Adapters composing the already implemented scientific-analysis services."""

from __future__ import annotations

import hashlib
from pathlib import Path

from backend.app.domain import (
    PaperReference, PaperSourceType, RepositoryReference, RepositorySourceType,
    UserReproductionGoal,
)
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

    def analyze(self, *, intake_id, source_filename, paper_pdf, repository_url, goal, on_event=None):
        def emit(event_type, payload):
            if on_event is not None:
                on_event(event_type, payload)

        digest = hashlib.sha256(paper_pdf).hexdigest()
        paper = PaperReference(
            id=f"paper:{digest[:24]}", title=source_filename,
            source_type=PaperSourceType.PDF_UPLOAD,
            source_uri=f"upload:{intake_id}",
        )
        from backend.app.domain import ReproductionEventType

        emit(ReproductionEventType.PAPER_ANALYSIS_STARTED, {"filename": source_filename})
        document = self.paper_ingestion.ingest(paper, upload=paper_pdf)
        paper_result = self.paper_extractor.extract(document)
        emit(ReproductionEventType.PAPER_ANALYSIS_COMPLETED, {"paper_id": paper.id})
        repository = RepositoryReference(
            repository_id=f"repository:{hashlib.sha256(repository_url.encode()).hexdigest()[:24]}",
            source_type=RepositorySourceType.GIT_URL, source_uri=repository_url,
        )
        emit(ReproductionEventType.REPOSITORY_ANALYSIS_STARTED, {"repository_id": repository.repository_id})
        repository_result = self.repository_analyzer.analyze(
            repository, paper_catalog=paper_result.catalog,
        )
        emit(ReproductionEventType.REPOSITORY_ANALYSIS_COMPLETED, {
            "repository_catalog_id": repository_result.catalog.catalog_id,
            "snapshot_id": repository_result.catalog.snapshot_id,
        })
        goal_result = self.goal_intake.intake(
            UserReproductionGoal(goal_id=f"goal:{intake_id}", text=goal),
            paper_result.catalog,
        )
        alignment_result = self.alignment_agent.align(
            paper_result.catalog, repository_result.catalog,
            reproduction_specification=goal_result.specification,
            paper_document=document,
        )
        return IntakeAnalysis(
            paper=paper, paper_catalog=paper_result.catalog,
            repository_catalog=repository_result.catalog,
            alignment_catalog=alignment_result.catalog,
            goal_resolution=goal_result,
            repository_snapshot=repository_result.snapshot,
        )

    def clarify(self, *, intake, answers):
        enriched = intake.user_goal + "\nUser clarification:\n" + "\n".join(answers)
        return self.goal_intake.intake(
            UserReproductionGoal(goal_id=f"goal:{intake.intake_id}", text=enriched),
            intake.paper_catalog,
        )

    def plan(self, *, intake):
        return self.planner.plan(
            intake.goal_resolution.specification, intake.paper_catalog,
            intake.repository_catalog, intake.alignment_catalog,
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
