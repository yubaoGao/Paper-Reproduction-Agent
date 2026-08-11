"""Provider-neutral structured LLM contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class LLMRole(str, Enum):
    PRIMARY = "primary"
    FAST = "fast"
    VISION = "vision"


class LLMCallSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    timeout_seconds: float = Field(default=90.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=8)
    max_output_tokens: int = Field(default=8192, ge=128)
    temperature: float = Field(default=0.0, ge=0, le=2)
    thinking_enabled: bool | None = None
    reasoning_effort: str | None = Field(default=None, pattern=r"^(high|max)$")


class LLMCallMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    model: str
    role: LLMRole
    started_at: datetime
    finished_at: datetime
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    prompt_name: str
    prompt_version: str


T = TypeVar("T", bound=BaseModel)


class StructuredLLMResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: T
    metadata: LLMCallMetadata


class StructuredLLMClient(ABC):
    @abstractmethod
    def generate_structured(
        self,
        *,
        role: LLMRole,
        system_prompt: str,
        content: str,
        output_schema: type[T],
        prompt_name: str,
        prompt_version: str,
        images: tuple[str, ...] = (),
        settings: LLMCallSettings | None = None,
    ) -> StructuredLLMResponse[T]:
        """Return a validated domain-safe value and call metadata."""


class LLMProviderError(RuntimeError):
    pass


class StructuredOutputError(LLMProviderError):
    pass
