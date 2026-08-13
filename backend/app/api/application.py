"""FastAPI composition root."""

from __future__ import annotations

import os

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.infrastructure.persistence import PostgresProductPersistence
from backend.app.services import ExternalResourcePathValidator, ExternalResourceResolutionService, ReproductionAPIService

from .auth import HeaderPrincipalAuthenticator
from .composition import ExistingServicesAnalysisPipeline
from .error_mapping import install_error_handlers
from .routes import router


def create_app(api_service, *, principal_authenticator=None, max_paper_upload_bytes: int = 50 * 1024 * 1024) -> FastAPI:
    app = FastAPI(title="ReproPilot API", version="1.0.0")
    app.state.api_service = api_service
    app.state.principal_authenticator = principal_authenticator or HeaderPrincipalAuthenticator()
    app.state.max_paper_upload_bytes = max_paper_upload_bytes
    install_error_handlers(app)
    app.include_router(router)
    return app


def build_production_app(
    *, pipeline=None, analysis_components: dict | None = None,
    database_url: str | None = None, principal_resource_roots=None,
) -> FastAPI:
    """Wire PostgreSQL and existing analysis services without executing jobs in HTTP."""
    if pipeline is None:
        if analysis_components is None:
            raise RuntimeError("pipeline or existing analysis_components are required")
        pipeline = ExistingServicesAnalysisPipeline(**analysis_components)
    url = database_url or os.environ.get("REPROPILOT_DATABASE_URL")
    if not url:
        raise RuntimeError("REPROPILOT_DATABASE_URL is required")
    engine = create_engine(url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    path_validator = ExternalResourcePathValidator(principal_roots=principal_resource_roots or {})
    persistence = PostgresProductPersistence(
        sessions, external_resource_path_validator=path_validator,
    )
    resources = ExternalResourceResolutionService(persistence.resources, path_validator)
    return create_app(ReproductionAPIService(persistence, pipeline, resources))
