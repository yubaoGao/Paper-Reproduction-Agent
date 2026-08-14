"""Intake-scoped LLM call budget. Transport retries count as distinct HTTP calls."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Iterator

from pydantic import BaseModel, ConfigDict, Field

from .contracts import LLMCallRecord, LLMRole


ANALYSIS_LLM_BUDGET_EXCEEDED = "ANALYSIS_LLM_BUDGET_EXCEEDED"
ANALYSIS_TIMEOUT = "ANALYSIS_TIMEOUT"
ANALYSIS_LEASE_LOST = "ANALYSIS_LEASE_LOST"

_current_budget: ContextVar["AnalysisLLMBudget | None"] = ContextVar(
    "repropilot_analysis_llm_budget", default=None,
)
_current_stage: ContextVar[str | None] = ContextVar(
    "repropilot_analysis_llm_stage", default=None,
)


class AnalysisLLMBudgetExceeded(RuntimeError):
    code = ANALYSIS_LLM_BUDGET_EXCEEDED


class AnalysisTimeoutError(RuntimeError):
    code = ANALYSIS_TIMEOUT


class AnalysisLeaseLostError(RuntimeError):
    code = ANALYSIS_LEASE_LOST


ANALYSIS_CONTROL_FLOW_ERRORS = (
    AnalysisLLMBudgetExceeded,
    AnalysisTimeoutError,
    AnalysisLeaseLostError,
)


class AnalysisLLMBudgetSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_llm_calls: int = Field(default=100, ge=1)
    timeout_seconds: float = Field(default=1800.0, gt=0)
    max_job_attempts: int = Field(default=2, ge=1, le=8)
    lease_seconds: int = Field(default=300, ge=30)

    @classmethod
    def from_env(cls) -> "AnalysisLLMBudgetSettings":
        return cls(
            max_llm_calls=int(os.getenv("REPROPILOT_ANALYSIS_MAX_LLM_CALLS", "100")),
            timeout_seconds=float(os.getenv("REPROPILOT_ANALYSIS_TIMEOUT_SECONDS", "1800")),
            max_job_attempts=int(os.getenv("REPROPILOT_ANALYSIS_MAX_ATTEMPTS", "2")),
            lease_seconds=int(os.getenv("REPROPILOT_ANALYSIS_LEASE_SECONDS", "300")),
        )


class AnalysisLLMBudget:
    """Hard stop for one analysis phase. Crash retries share this phase budget."""

    def __init__(
        self,
        settings: AnalysisLLMBudgetSettings | None = None,
        *,
        initial_phase_count: int = 0,
        analysis_started_at: datetime | None = None,
        on_http_attempt: Callable[[], int] | None = None,
        interrupt_check: Callable[[], None] | None = None,
    ) -> None:
        if initial_phase_count < 0:
            raise ValueError("initial_phase_count cannot be negative")
        self.settings = settings or AnalysisLLMBudgetSettings()
        self.analysis_started_at = analysis_started_at or datetime.now(timezone.utc)
        self._phase_count = initial_phase_count
        self._on_http_attempt = on_http_attempt
        self._interrupt_check = interrupt_check
        self.calls: list[LLMCallRecord] = []

    @property
    def call_count(self) -> int:
        return self._phase_count

    def remaining_seconds(self) -> float:
        elapsed = (datetime.now(timezone.utc) - self.analysis_started_at).total_seconds()
        return self.settings.timeout_seconds - elapsed

    def preflight(self) -> None:
        if self._interrupt_check is not None:
            self._interrupt_check()
        if self.remaining_seconds() <= 0:
            raise AnalysisTimeoutError(
                f"intake analysis exceeded {self.settings.timeout_seconds:.0f}s"
            )
        if self._phase_count >= self.settings.max_llm_calls:
            raise AnalysisLLMBudgetExceeded(
                f"intake analysis exceeded {self.settings.max_llm_calls} LLM calls"
            )

    def consume_http_attempt(
        self,
        *,
        provider: str,
        model: str,
        role: LLMRole,
        prompt_name: str,
        attempt: int,
        retry_reason: str | None = None,
    ) -> LLMCallRecord:
        self.preflight()
        if self._on_http_attempt is not None:
            self._phase_count = self._on_http_attempt()
        else:
            self._phase_count += 1
        now = datetime.now(timezone.utc)
        record = LLMCallRecord(
            provider=provider,
            model=model,
            role=role,
            stage=_current_stage.get(),
            prompt_name=prompt_name,
            attempt=attempt,
            retry_reason=retry_reason,
            started_at=now,
        )
        self.calls.append(record)
        return record

    def complete_http_attempt(
        self,
        record: LLMCallRecord,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        finished = datetime.now(timezone.utc)
        elapsed_ms = max(0.0, (finished - record.started_at).total_seconds() * 1000)
        updated = record.model_copy(update={
            "input_tokens": max(0, input_tokens),
            "output_tokens": max(0, output_tokens),
            "elapsed_ms": elapsed_ms,
            "finished_at": finished,
        })
        for index, item in enumerate(self.calls):
            if item is record:
                self.calls[index] = updated
                return

    def as_records(self) -> tuple[dict, ...]:
        return tuple(item.model_dump(mode="json") for item in self.calls)

    @contextmanager
    def activate(self) -> Iterator["AnalysisLLMBudget"]:
        token = _current_budget.set(self)
        try:
            yield self
        finally:
            _current_budget.reset(token)


def current_analysis_budget() -> AnalysisLLMBudget | None:
    return _current_budget.get()


@contextmanager
def analysis_stage(name: str) -> Iterator[None]:
    token = _current_stage.set(name)
    try:
        yield
    finally:
        _current_stage.reset(token)


def record_analysis_llm_http_attempt(
    *,
    provider: str,
    model: str,
    role: LLMRole,
    prompt_name: str,
    attempt: int,
    retry_reason: str | None = None,
) -> LLMCallRecord | None:
    budget = current_analysis_budget()
    if budget is None:
        return None
    return budget.consume_http_attempt(
        provider=provider, model=model, role=role, prompt_name=prompt_name,
        attempt=attempt, retry_reason=retry_reason,
    )


def complete_analysis_llm_http_attempt(
    record: LLMCallRecord | None, *, input_tokens: int = 0, output_tokens: int = 0,
) -> None:
    budget = current_analysis_budget()
    if budget is None or record is None:
        return
    budget.complete_http_attempt(record, input_tokens=input_tokens, output_tokens=output_tokens)
