"""FastAPI composition root."""

from __future__ import annotations

import json
import os

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.infrastructure.persistence import PostgresProductPersistence
from backend.app.services import ExternalResourcePathValidator, ExternalResourceResolutionService, ReproductionAPIService

from .auth import HeaderPrincipalAuthenticator
from .composition import ExistingServicesAnalysisPipeline, build_default_analysis_pipeline
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
    url = database_url or os.environ.get("REPROPILOT_DATABASE_URL")
    if not url:
        raise RuntimeError("REPROPILOT_DATABASE_URL is required")
    engine = create_engine(url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    roots = (
        principal_resource_roots
        if principal_resource_roots is not None
        else _principal_resource_roots_from_env()
    )
    path_validator = ExternalResourcePathValidator(principal_roots=roots)
    persistence = PostgresProductPersistence(
        sessions, external_resource_path_validator=path_validator,
    )
    if pipeline is None:
        pipeline = (
            ExistingServicesAnalysisPipeline(**analysis_components)
            if analysis_components is not None
            else build_default_analysis_pipeline(
                workspace_root=os.environ.get("REPROPILOT_WORKSPACE_ROOT", "workspace")
            )
        )
    resources = ExternalResourceResolutionService(persistence.resources, path_validator)
    return create_app(ReproductionAPIService(persistence, pipeline, resources))


def _principal_resource_roots_from_env():
    raw = os.environ.get("REPROPILOT_PRINCIPAL_RESOURCE_ROOTS_JSON", "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("REPROPILOT_PRINCIPAL_RESOURCE_ROOTS_JSON must be valid JSON") from exc
    if not isinstance(value, dict) or any(
        not isinstance(principal, str)
        or not isinstance(roots, list)
        or any(not isinstance(root, str) for root in roots)
        for principal, roots in value.items()
    ):
        raise RuntimeError(
            "REPROPILOT_PRINCIPAL_RESOURCE_ROOTS_JSON must map principals to path lists"
        )
    return value
