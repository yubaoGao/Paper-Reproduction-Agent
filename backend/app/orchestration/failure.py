"""Deterministic failure classification for execution and validation results."""

from __future__ import annotations

import hashlib

from backend.app.domain import FailureCategory, FailureRecord
from backend.app.runtime.curie_models import CommandExecutionResult, ExecutionStatus


_PATTERNS = (
    (FailureCategory.RESOURCE, ("out of memory", "oom", "disk quota", "no space left", "killed")),
    (FailureCategory.DEPENDENCY, ("modulenotfounderror", "importerror", "no matching distribution", "dependency")),
    (FailureCategory.DATA, ("dataset", "data file", "corrupt data", "missing data")),
    (FailureCategory.CONFIG, ("configuration", "config file", "yaml", "toml", "unrecognized arguments")),
    (FailureCategory.ENVIRONMENT, ("cuda", "python version", "environment", "glibc", "abi")),
    (FailureCategory.CODE, ("traceback", "syntaxerror", "typeerror", "nameerror", "attributeerror")),
)


class FailureClassifier:
    def classify_execution(
        self,
        result: CommandExecutionResult,
        *,
        step_id: str,
        attempt_number: int,
    ) -> FailureRecord | None:
        if result.status is ExecutionStatus.SUCCEEDED and result.exit_code == 0:
            return None
        if result.status is ExecutionStatus.TIMED_OUT:
            return self.record(
                step_id,
                attempt_number,
                FailureCategory.TIMEOUT,
                "EXECUTION_TIMEOUT",
                "experiment command timed out",
                retryable=False,
            )
        text = f"{result.stderr or ''}\n{result.stdout or ''}".casefold()
        category = next(
            (category for category, patterns in _PATTERNS if any(item in text for item in patterns)),
            FailureCategory.UNKNOWN,
        )
        return self.record(
            step_id,
            attempt_number,
            category,
            "EXECUTION_FAILED",
            "experiment command returned a non-zero exit code",
            retryable=category not in {FailureCategory.DATA, FailureCategory.RESOURCE},
            details={"exit_code": result.exit_code, "stderr_reference": result.stderr_reference},
        )

    def validation_failure(
        self,
        *,
        step_id: str,
        attempt_number: int,
        violations: tuple[str, ...],
    ) -> FailureRecord:
        return self.record(
            step_id,
            attempt_number,
            FailureCategory.VALIDATION,
            "VALIDATION_FAILED",
            "experiment output failed validation",
            retryable=True,
            details={"violations": list(violations)},
        )

    @staticmethod
    def record(
        step_id: str,
        attempt_number: int,
        category: FailureCategory,
        code: str,
        message: str,
        *,
        retryable: bool,
        details=None,
    ) -> FailureRecord:
        digest = hashlib.sha256(
            f"{step_id}:{attempt_number}:{category.value}:{code}".encode()
        ).hexdigest()[:20]
        return FailureRecord(
            failure_id=f"failure:{digest}",
            category=category,
            code=code,
            message=message,
            retryable=retryable,
            details=dict(details or {}),
        )


def dependency_blocked_failure(step_id: str, parent_ids: tuple[str, ...]) -> FailureRecord:
    return FailureClassifier.record(
        step_id,
        1,
        FailureCategory.DEPENDENCY,
        "DEPENDENCY_BLOCKED",
        "step cannot run because a dependency did not succeed",
        retryable=False,
        details={"blocking_step_ids": list(parent_ids)},
    )
