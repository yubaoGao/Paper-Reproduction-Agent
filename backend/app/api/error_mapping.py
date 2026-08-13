"""Stable application/domain error mapping."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from backend.app.services import (
    APIUseCaseError, EntityNotFoundError, InvalidJobQueueTransition,
    PaperCodeAlignmentError, PaperIngestionError, RepositoryAnalysisError,
    ReproductionIntakeError, ReproductionPlanningError,
    ResourceAccessDeniedError, ResourcePathValidationError, ResourceRegistryError,
)

from .auth import AuthenticationError


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthenticationError)
    async def authentication_error(_request: Request, exc: AuthenticationError):
        return JSONResponse(status_code=401, content={"code": "unauthenticated", "message": str(exc)})

    @app.exception_handler(EntityNotFoundError)
    async def not_found(_request: Request, exc: EntityNotFoundError):
        return JSONResponse(status_code=404, content={"code": exc.code, "message": str(exc)})

    @app.exception_handler(ResourceAccessDeniedError)
    async def resource_denied(_request: Request, _exc: ResourceAccessDeniedError):
        return JSONResponse(status_code=404, content={"code": "not_found", "message": "resource not found"})

    @app.exception_handler(ResourcePathValidationError)
    async def invalid_resource(_request: Request, exc: ResourcePathValidationError):
        return JSONResponse(status_code=422, content={"code": "invalid_resource_path", "message": str(exc)})

    @app.exception_handler(ValidationError)
    async def invalid_domain_input(_request: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={"code": "invalid_input", "message": str(exc)})

    async def analysis_failed(_request: Request, exc: Exception):
        return JSONResponse(status_code=422, content={"code": "analysis_failed", "message": str(exc)})

    for exception_type in (
        PaperIngestionError, RepositoryAnalysisError, PaperCodeAlignmentError,
        ReproductionIntakeError, ReproductionPlanningError,
    ):
        app.add_exception_handler(exception_type, analysis_failed)

    async def conflict(_request: Request, exc: Exception):
        return JSONResponse(status_code=409, content={"code": getattr(exc, "code", "conflict"), "message": str(exc)})

    for exception_type in (APIUseCaseError, InvalidJobQueueTransition, ResourceRegistryError):
        app.add_exception_handler(exception_type, conflict)
