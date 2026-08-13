"""Structured, repository-neutral reproduction result comparison models."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, JsonValue, model_validator

from .experiment import DomainModel, FinalMetric, NonEmptyStr, ResultAggregation
from .intelligence import SelectionMode
from .reproduction import EvidenceReference


class ComparisonPolicyKind(str, Enum):
    EXACT = "exact"
    ABSOLUTE_TOLERANCE = "absolute_tolerance"
    RELATIVE_TOLERANCE = "relative_tolerance"
    NO_THRESHOLD = "no_threshold"


class MetricComparisonStatus(str, Enum):
    MATCH = "match"
    WITHIN_TOLERANCE = "within_tolerance"
    OUTSIDE_TOLERANCE = "outside_tolerance"
    MEASURED_DEVIATION = "measured_deviation"
    NOT_COMPARABLE = "not_comparable"
    MISSING_PAPER_RESULT = "missing_paper_result"
    MISSING_FINAL_RESULT = "missing_final_result"
    UNAVAILABLE_FINAL_RESULT = "unavailable_final_result"
    EXECUTION_FAILED = "execution_failed"


class ExperimentComparisonStatus(str, Enum):
    COMPARED = "compared"
    FINAL_RESULT_MISSING = "final_result_missing"
    EXECUTION_FAILED = "execution_failed"


class MetricComparisonPolicy(DomainModel):
    kind: ComparisonPolicyKind = ComparisonPolicyKind.NO_THRESHOLD
    tolerance: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def threshold_shape(self):
        needs_threshold = self.kind in {
            ComparisonPolicyKind.ABSOLUTE_TOLERANCE,
            ComparisonPolicyKind.RELATIVE_TOLERANCE,
        }
        if needs_threshold and self.tolerance is None:
            raise ValueError("tolerance comparison policy requires a threshold")
        if not needs_threshold and self.tolerance is not None:
            raise ValueError("EXACT and NO_THRESHOLD cannot carry a tolerance")
        return self


class MetricIdentity(DomainModel):
    original_name: NonEmptyStr
    normalized_name: NonEmptyStr
    aliases_applied: tuple[NonEmptyStr, ...] = ()
    averaging: NonEmptyStr | None = None
    split: NonEmptyStr | None = None
    aggregation: NonEmptyStr | None = None
    original_unit: NonEmptyStr | None = None
    normalized_unit: NonEmptyStr | None = None


class ComparisonEvidenceChain(DomainModel):
    selection_mode: SelectionMode
    original_user_goal: NonEmptyStr
    selection_reason: NonEmptyStr
    paper_experiment_id: NonEmptyStr
    paper_claim_id: NonEmptyStr | None = None
    paper_evidence: tuple[EvidenceReference, ...] = ()
    final_result_id: NonEmptyStr | None = None
    run_result_ids: tuple[NonEmptyStr, ...] = ()
    run_ids: tuple[NonEmptyStr, ...] = ()
    seeds: tuple[int | None, ...] = ()
    checkpoint_references: tuple[NonEmptyStr, ...] = ()
    selected_epochs: tuple[int | None, ...] = ()
    aggregation: ResultAggregation | None = None
    final_result_evidence: tuple[JsonValue, ...] = ()
    final_result_provenance: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)
    final_metric_evidence: tuple[JsonValue, ...] = ()
    final_metric_provenance: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)
    execution_failure_id: NonEmptyStr | None = None
    execution_failure_provenance: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)


class MetricComparison(DomainModel):
    comparison_id: NonEmptyStr
    paper_experiment_id: NonEmptyStr
    paper_claim_id: NonEmptyStr | None = None
    paper_metric: MetricIdentity | None = None
    reproduced_metric: MetricIdentity | None = None
    paper_value: float | None = Field(default=None, allow_inf_nan=False)
    reproduced_value: float | None = Field(default=None, allow_inf_nan=False)
    normalized_paper_value: float | None = Field(default=None, allow_inf_nan=False)
    normalized_reproduced_value: float | None = Field(default=None, allow_inf_nan=False)
    absolute_difference: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    relative_difference: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    percentage_point_difference: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    policy: MetricComparisonPolicy
    status: MetricComparisonStatus
    reason: NonEmptyStr
    evidence_chain: ComparisonEvidenceChain

    @model_validator(mode="after")
    def consistent_values(self):
        paper_fields = (self.paper_claim_id, self.paper_metric, self.paper_value)
        if self.status is MetricComparisonStatus.MISSING_PAPER_RESULT:
            if any(value is not None for value in paper_fields):
                raise ValueError("missing-paper comparison cannot assert paper result fields")
            if self.reproduced_metric is None or self.reproduced_value is None:
                raise ValueError("missing-paper comparison requires a reproduced metric value")
        elif any(value is None for value in paper_fields):
            raise ValueError("paper-backed comparison requires claim, metric identity and value")
        numeric_statuses = {
            MetricComparisonStatus.MATCH,
            MetricComparisonStatus.WITHIN_TOLERANCE,
            MetricComparisonStatus.OUTSIDE_TOLERANCE,
            MetricComparisonStatus.MEASURED_DEVIATION,
        }
        if self.status in numeric_statuses:
            required = (
                self.reproduced_metric,
                self.reproduced_value,
                self.normalized_paper_value,
                self.normalized_reproduced_value,
                self.absolute_difference,
            )
            if any(value is None for value in required):
                raise ValueError("numeric comparison status requires reproduced values and difference")
        return self


class ExecutionFailureReference(DomainModel):
    paper_experiment_id: NonEmptyStr
    failure_id: NonEmptyStr
    message: NonEmptyStr
    provenance: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)


class ExperimentComparison(DomainModel):
    paper_experiment_id: NonEmptyStr
    status: ExperimentComparisonStatus
    metric_comparisons: tuple[MetricComparison, ...] = ()
    additional_metrics: tuple[FinalMetric, ...] = ()
    final_result_id: NonEmptyStr | None = None
    execution_failure: ExecutionFailureReference | None = None

    @model_validator(mode="after")
    def status_shape(self):
        if self.status is ExperimentComparisonStatus.EXECUTION_FAILED and self.execution_failure is None:
            raise ValueError("failed experiment comparison requires failure evidence")
        if self.status is not ExperimentComparisonStatus.EXECUTION_FAILED and self.execution_failure is not None:
            raise ValueError("execution failure evidence requires failed comparison status")
        return self


class ReproductionComparisonReport(DomainModel):
    report_id: NonEmptyStr
    selection_mode: SelectionMode
    selected_experiment_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    experiments: tuple[ExperimentComparison, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def exactly_selected_scope(self):
        compared = tuple(item.paper_experiment_id for item in self.experiments)
        if compared != self.selected_experiment_ids:
            raise ValueError("comparison report must cover exactly the selected experiments in selection order")
        if len(compared) != len(set(compared)):
            raise ValueError("comparison report contains duplicate experiments")
        return self
