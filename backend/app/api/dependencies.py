"""FastAPI dependencies; routes only obtain application services and principals."""

from fastapi import Request

from .auth import Principal


def get_api_service(request: Request):
    return request.app.state.api_service


def get_principal(request: Request) -> Principal:
    return request.app.state.principal_authenticator.authenticate(request)
