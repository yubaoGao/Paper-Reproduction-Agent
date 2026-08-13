"""Lightweight replaceable principal authentication protocol."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request


class AuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Principal:
    principal_id: str


class PrincipalAuthenticator(Protocol):
    def authenticate(self, request: Request) -> Principal: ...


class HeaderPrincipalAuthenticator:
    """Integration seam for JWT auth; never trusts query/body owner fields."""

    header_name = "X-ReproPilot-Principal"
    _valid = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,254}$")

    def authenticate(self, request: Request) -> Principal:
        value = request.headers.get(self.header_name, "").strip()
        if not value or self._valid.fullmatch(value) is None:
            raise AuthenticationError(f"a valid {self.header_name} header is required")
        return Principal(value)
