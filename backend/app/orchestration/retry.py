"""Bounded, category-aware retry policy."""

from __future__ import annotations

from pydantic import Field

from backend.app.domain import FailureCategory, FailureRecord
from backend.app.domain.experiment import DomainModel


class RetryPolicy(DomainModel):
    max_attempts: int = Field(default=2, ge=1, le=10)
    retryable_categories: tuple[FailureCategory, ...] = (
        FailureCategory.ENVIRONMENT,
        FailureCategory.DEPENDENCY,
        FailureCategory.CODE,
        FailureCategory.CONFIG,
        FailureCategory.VALIDATION,
        FailureCategory.UNKNOWN,
    )
    patchable_categories: tuple[FailureCategory, ...] = (
        FailureCategory.DEPENDENCY,
        FailureCategory.CODE,
        FailureCategory.CONFIG,
        FailureCategory.VALIDATION,
    )

    def allows(self, failure: FailureRecord, completed_attempts: int) -> bool:
        return (
            completed_attempts < self.max_attempts
            and failure.retryable
            and failure.category in self.retryable_categories
        )

    def requires_patch(self, failure: FailureRecord) -> bool:
        return failure.category in self.patchable_categories
