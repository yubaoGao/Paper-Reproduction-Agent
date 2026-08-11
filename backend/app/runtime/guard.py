"""Deterministic protection for planner-authoritative constraints."""

from __future__ import annotations

from pydantic import JsonValue

from backend.app.domain.experiment import DomainModel, NonEmptyStr

from .curie_models import ConstraintLevel, CurieExecutionContext


class SpecificationViolation(DomainModel):
    key: NonEmptyStr
    expected: JsonValue | None = None
    actual: JsonValue | None = None
    message: NonEmptyStr


class SpecificationGuardResult(DomainModel):
    valid: bool
    violations: tuple[SpecificationViolation, ...] = ()


class SpecificationViolationError(RuntimeError):
    def __init__(self, violations: tuple[SpecificationViolation, ...]) -> None:
        self.violations = tuple(violations)
        super().__init__("; ".join(item.message for item in self.violations))


class ExperimentSpecificationGuard:
    """Reject changes to fields classified as locked by the translator."""

    def validate_values(
        self,
        context: CurieExecutionContext,
        candidate: dict[str, JsonValue],
    ) -> SpecificationGuardResult:
        locked = context.constraints.values(ConstraintLevel.LOCKED)
        violations = tuple(
            SpecificationViolation(
                key=key,
                expected=expected,
                actual=candidate[key],
                message=f"locked constraint {key!r} cannot change",
            )
            for key, expected in locked.items()
            if key in candidate and candidate[key] != expected
        )
        return SpecificationGuardResult(valid=not violations, violations=violations)

    def require_values(
        self,
        context: CurieExecutionContext,
        candidate: dict[str, JsonValue],
    ) -> SpecificationGuardResult:
        result = self.validate_values(context, candidate)
        if not result.valid:
            raise SpecificationViolationError(result.violations)
        return result

    def validate_patch(
        self,
        context: CurieExecutionContext,
        proposed_values: dict[str, JsonValue],
    ) -> SpecificationGuardResult:
        return self.validate_values(context, proposed_values)
