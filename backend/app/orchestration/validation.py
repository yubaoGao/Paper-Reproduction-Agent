"""Deterministic validation that always precedes semantic/agent judgment."""

from __future__ import annotations

import hashlib

from backend.app.domain import (
    ArtifactReference,
    ExperimentSpecification,
    ValidationPhase,
    ValidationRecord,
)
from backend.app.runtime.curie_models import CommandExecutionResult, ExecutionStatus


class DeterministicValidator:
    def validate(
        self,
        experiment: ExperimentSpecification,
        result: CommandExecutionResult,
        artifacts: tuple[ArtifactReference, ...],
        *,
        step_id: str,
        attempt_number: int,
    ) -> tuple[ValidationRecord, ...]:
        records = [
            self._record(
                step_id,
                attempt_number,
                "exit_code",
                result.status is ExecutionStatus.SUCCEEDED and result.exit_code == 0,
                "passed" if result.status is ExecutionStatus.SUCCEEDED and result.exit_code == 0 else result.status.value,
                ()
                if result.status is ExecutionStatus.SUCCEEDED and result.exit_code == 0
                else ("command did not complete with exit code zero",),
                {"exit_code": result.exit_code},
            )
        ]
        if not records[0].passed:
            return tuple(records)

        required = self._required_artifacts(experiment)
        available = {item.artifact.name for item in artifacts}
        missing = tuple(name for name in required if name not in available)
        records.append(
            self._record(
                step_id,
                attempt_number,
                "artifact_existence",
                not missing,
                "passed" if not missing else "missing",
                tuple(f"required artifact {name!r} is missing" for name in missing),
                {"required": list(required), "available": sorted(available)},
            )
        )
        if missing:
            return tuple(records)

        schema = experiment.metadata.get("artifact_metadata_schema", {})
        violations = []
        if isinstance(schema, dict):
            by_name = {item.artifact.name: item.artifact for item in artifacts}
            for name, required_keys in schema.items():
                artifact = by_name.get(str(name))
                if artifact is None or not isinstance(required_keys, list):
                    continue
                for key in required_keys:
                    if str(key) not in artifact.metadata:
                        violations.append(f"artifact {name!r} metadata is missing {key!r}")
        records.append(
            self._record(
                step_id,
                attempt_number,
                "artifact_schema",
                not violations,
                "passed" if not violations else "invalid",
                tuple(violations),
                {},
            )
        )
        return tuple(records)

    @staticmethod
    def _required_artifacts(experiment: ExperimentSpecification) -> tuple[str, ...]:
        value = experiment.metadata.get("required_artifacts", [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            return ()
        return tuple(value)

    @staticmethod
    def _record(step_id, attempt_number, name, passed, status, violations, details):
        digest = hashlib.sha256(
            f"{step_id}:{attempt_number}:deterministic:{name}".encode()
        ).hexdigest()[:20]
        return ValidationRecord(
            validation_id=f"validation:{digest}",
            validator_name=name,
            phase=ValidationPhase.DETERMINISTIC,
            passed=passed,
            status=status,
            violations=violations,
            details=details,
        )
