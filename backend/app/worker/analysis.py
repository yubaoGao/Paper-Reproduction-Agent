"""Independent intake-analysis worker composition root."""

from __future__ import annotations

import os
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api.composition import build_default_analysis_pipeline
from backend.app.infrastructure.persistence import PostgresProductPersistence
from backend.app.llm.budget import AnalysisLLMBudgetSettings
from backend.app.services import (
    ExternalResourcePathValidator,
    ExternalResourceResolutionService,
    ReproductionAPIService,
)
from backend.app.services.analysis_worker import IntakeAnalysisWorker
from backend.app.services.paper_artifacts import FilesystemIntakePaperStore


def build_analysis_worker(*, worker_id: str, database_url: str | None = None, workspace_root: str | Path | None = None):
    url = database_url or os.environ.get("REPROPILOT_DATABASE_URL")
    if not url:
        raise RuntimeError("REPROPILOT_DATABASE_URL is required")
    if not worker_id.strip():
        raise RuntimeError("REPROPILOT_ANALYSIS_WORKER_ID is required")
    root = Path(workspace_root or os.environ.get("REPROPILOT_WORKSPACE_ROOT", "workspace"))
    settings = AnalysisLLMBudgetSettings.from_env()
    engine = create_engine(url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    persistence = PostgresProductPersistence(sessions)
    pipeline = build_default_analysis_pipeline(workspace_root=root)
    resources = ExternalResourceResolutionService(
        persistence.resources, ExternalResourcePathValidator(principal_roots={}),
    )
    service = ReproductionAPIService(
        persistence, pipeline, resources,
        analysis_queue=persistence.analysis_queue,
        paper_artifacts=persistence.paper_artifacts,
        analysis_settings=settings,
    )
    return IntakeAnalysisWorker(
        worker_id=worker_id,
        queue=persistence.analysis_queue,
        service=service,
        lease_seconds=settings.lease_seconds,
    )


def run_analysis_worker_forever(worker, *, idle_seconds: float = 1.0) -> None:
    if idle_seconds <= 0:
        raise ValueError("idle_seconds must be positive")
    while True:
        if worker.run_once() is None:
            time.sleep(idle_seconds)


def main() -> None:
    worker_id = os.environ.get("REPROPILOT_ANALYSIS_WORKER_ID") or os.environ.get("REPROPILOT_WORKER_ID")
    if not worker_id:
        raise RuntimeError("REPROPILOT_ANALYSIS_WORKER_ID is required")
    run_analysis_worker_forever(build_analysis_worker(worker_id=worker_id))


if __name__ == "__main__":
    main()
