"""Deterministic role router for the two fixed production model roles."""

from __future__ import annotations

from .contracts import LLMRole, StructuredLLMClient


class LLMRouter:
    def __init__(self, primary: StructuredLLMClient, fast: StructuredLLMClient) -> None:
        self.primary = primary
        self.fast = fast

    def for_role(self, role: LLMRole) -> StructuredLLMClient:
        if role is LLMRole.PRIMARY:
            return self.primary
        if role in {LLMRole.FAST, LLMRole.VISION}:
            return self.fast
        raise ValueError(f"unsupported LLM role: {role}")
