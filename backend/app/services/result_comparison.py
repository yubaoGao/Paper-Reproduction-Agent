"""Deterministic comparison of selected paper claims with canonical FinalResult values."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from decimal import Decimal

from backend.app.domain.comparison import (
    ComparisonEvidenceChain,
    ComparisonPolicyKind,
    ExecutionFailureReference,
    ExperimentComparison,
    ExperimentComparisonStatus,
    MetricComparison,
    MetricComparisonPolicy,
    MetricComparisonStatus,
    MetricIdentity,
    ReproductionComparisonReport,
)
from backend.app.domain.experiment import FinalMetricStatus, FinalResult, ResultAggregation
from backend.app.domain.intelligence import ExperimentSelection, GoalResolutionStatus, PaperExperimentCatalog


_ALIASES = {
    "acc": "accuracy",
    "accuracyscore": "accuracy",
    "f1score": "f1",
    "fscore": "f1",
    "rocauc": "auc",
    "areaunderroc": "auc",
}
_AVERAGING = {"macro", "micro", "weighted"}
_PERCENT_UNITS = {"%", "percent", "percentage", "percentagepoint", "percentagepoints", "百分比"}
_RATIO_UNITS = {"ratio", "proportion", "fraction", "比例"}


def _comparison_id(*parts: object) -> str:
    digest = hashlib.sha256("\x1f".join(str(value) for value in parts).encode("utf-8")).hexdigest()[:20]
    return f"metric-comparison:{digest}"


def _text_key(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(character for character in normalized if character.isalnum())


def _tokens(value: str | None) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return tuple(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


class MetricIdentityNormalizer:
    """Create matching metadata while preserving the source metric identity."""

    def identify(
        self,
        name: str,
        *,
        split: str | None,
        unit: str | None,
        condition: str | None = None,
        aggregation: ResultAggregation | None = None,
    ) -> MetricIdentity:
        name_tokens = _tokens(name)
        condition_tokens = _tokens(condition)
        averaging_values = tuple(value for value in (*name_tokens, *condition_tokens) if value in _AVERAGING)
        averaging = averaging_values[0] if averaging_values else None
        base_tokens = tuple(value for value in name_tokens if value not in _AVERAGING)
        raw_key = "".join(base_tokens) or _text_key(name) or unicodedata.normalize("NFKC", name).casefold()
        normalized_name = _ALIASES.get(raw_key, raw_key)
        aliases = (f"{raw_key}->{normalized_name}",) if normalized_name != raw_key else ()
        paper_aggregation = self._paper_aggregation(condition)
        result_aggregation = self._result_aggregation(aggregation)
        return MetricIdentity(
            original_name=name,
            normalized_name=normalized_name,
            aliases_applied=aliases,
            averaging=averaging,
            split=_text_key(split) or None,
            aggregation=paper_aggregation or result_aggregation,
            original_unit=unit,
            normalized_unit=self._unit(unit),
        )

    @staticmethod
    def _paper_aggregation(condition: str | None) -> str | None:
        tokens = set(_tokens(condition))
        if "median" in tokens:
            return "median"
        if "mean" in tokens or "average" in tokens:
            return "mean"
        return None

    @staticmethod
    def _result_aggregation(aggregation: ResultAggregation | None) -> str | None:
        if aggregation in {ResultAggregation.MEAN, ResultAggregation.MEAN_STD}:
            return "mean"
        return None

    @staticmethod
    def _unit(unit: str | None) -> str | None:
        key = _text_key(unit)
        if not key and unit != "%":
            return None
        if unit == "%" or key in {_text_key(value) for value in _PERCENT_UNITS}:
            return "proportion"
        if key in {_text_key(value) for value in _RATIO_UNITS}:
            return "proportion"
        return key or unit

    @staticmethod
    def compatible(left: MetricIdentity, right: MetricIdentity) -> tuple[bool, str]:
        if left.normalized_name != right.normalized_name:
            return False, "metric names do not align after conservative alias normalization"
        if left.averaging != right.averaging and (left.averaging is not None or right.averaging is not None):
            return False, "metric averaging definitions differ"
        if left.split and right.split and left.split != right.split:
            return False, "reporting splits differ"
        if left.aggregation and left.aggregation != right.aggregation:
            return False, "result aggregations differ"
        if left.normalized_unit and right.normalized_unit and left.normalized_unit != right.normalized_unit:
            return False, "metric units are not compatible"
        return True, "metric identity, split and aggregation are compatible"


class DeterministicResultComparator:
    def __init__(self, normalizer: MetricIdentityNormalizer | None = None) -> None:
        self.normalizer = normalizer or MetricIdentityNormalizer()

    def compare(
        self,
        selection: ExperimentSelection,
        catalog: PaperExperimentCatalog,
        final_results: tuple[FinalResult, ...] = (),
        *,
        execution_failures: tuple[ExecutionFailureReference, ...] = (),
        policies: dict[str, MetricComparisonPolicy] | None = None,
        default_policy: MetricComparisonPolicy | None = None,
    ) -> ReproductionComparisonReport:
        if selection.resolution_status is not GoalResolutionStatus.RESOLVED:
            raise ValueError("result comparison requires a resolved experiment selection")
        selected_ids = tuple(selection.selected_experiment_ids)
        selected = set(selected_ids)
        catalog_ids = {item.experiment_id for item in catalog.experiments}
        if not selected <= catalog_ids:
            raise ValueError("selection references experiments absent from PaperExperimentCatalog")
        if any(item.paper_experiment_id not in selected for item in final_results):
            raise ValueError("FinalResult lies outside the selected experiment scope")
        if any(item.paper_experiment_id not in selected for item in execution_failures):
            raise ValueError("execution failure lies outside the selected experiment scope")
        result_by_experiment = self._unique_by_experiment(final_results, "FinalResult")
        failure_by_experiment = self._unique_by_experiment(execution_failures, "execution failure")
        if set(result_by_experiment) & set(failure_by_experiment):
            raise ValueError("selected experiment cannot have both FinalResult and execution failure")

        policies = policies or {}
        default_policy = default_policy or MetricComparisonPolicy()
        experiments = tuple(
            self._compare_experiment(
                selection,
                catalog,
                experiment_id,
                result_by_experiment.get(experiment_id),
                failure_by_experiment.get(experiment_id),
                policies,
                default_policy,
            )
            for experiment_id in selected_ids
        )
        report_fingerprint: list[str] = [selection.selection_mode.value, *selected_ids]
        for item in experiments:
            report_fingerprint.extend(
                (
                    item.final_result_id or "",
                    item.execution_failure.failure_id if item.execution_failure else "",
                    *(comparison.comparison_id for comparison in item.metric_comparisons),
                )
            )
        digest = hashlib.sha256("\x1f".join(report_fingerprint).encode("utf-8")).hexdigest()[:20]
        return ReproductionComparisonReport(
            report_id=f"reproduction-comparison:{digest}",
            selection_mode=selection.selection_mode,
            selected_experiment_ids=selected_ids,
            experiments=experiments,
        )

    @staticmethod
    def _unique_by_experiment(values, label):
        result = {}
        for item in values:
            if item.paper_experiment_id in result:
                raise ValueError(f"multiple {label} records exist for one selected experiment")
            result[item.paper_experiment_id] = item
        return result

    def _compare_experiment(self, selection, catalog, experiment_id, final_result, failure, policies, default_policy):
        claims = self._claims_for_experiment(catalog, experiment_id)
        if failure is not None:
            comparisons = tuple(
                self._non_numeric_comparison(
                    selection,
                    experiment_id,
                    claim,
                    MetricComparisonStatus.EXECUTION_FAILED,
                    "experiment execution failed before a canonical FinalResult was available",
                    policies.get(claim.id, default_policy),
                    failure=failure,
                )
                for claim in claims
            )
            return ExperimentComparison(
                paper_experiment_id=experiment_id,
                status=ExperimentComparisonStatus.EXECUTION_FAILED,
                metric_comparisons=comparisons,
                execution_failure=failure,
            )
        if final_result is None:
            comparisons = tuple(
                self._non_numeric_comparison(
                    selection,
                    experiment_id,
                    claim,
                    MetricComparisonStatus.MISSING_FINAL_RESULT,
                    "selected experiment has no canonical FinalResult",
                    policies.get(claim.id, default_policy),
                )
                for claim in claims
            )
            return ExperimentComparison(
                paper_experiment_id=experiment_id,
                status=ExperimentComparisonStatus.FINAL_RESULT_MISSING,
                metric_comparisons=comparisons,
            )

        metrics = tuple(final_result.reporting_metrics)
        metric_identities = tuple(
            self.normalizer.identify(
                item.name,
                split=item.split,
                unit=item.unit,
                aggregation=final_result.aggregation,
            )
            for item in metrics
        )
        used = set()
        comparisons = []
        for claim in claims:
            comparison, matched_index = self._compare_claim(
                selection,
                experiment_id,
                claim,
                final_result,
                metrics,
                metric_identities,
                used,
                policies.get(claim.id, default_policy),
            )
            comparisons.append(comparison)
            if matched_index is not None:
                used.add(matched_index)
        additional = tuple(metric for index, metric in enumerate(metrics) if index not in used)
        return ExperimentComparison(
            paper_experiment_id=experiment_id,
            status=ExperimentComparisonStatus.COMPARED,
            metric_comparisons=tuple(comparisons),
            additional_metrics=additional,
            final_result_id=final_result.result_id,
        )

    @staticmethod
    def _claims_for_experiment(catalog, experiment_id):
        record = next(item for item in catalog.experiments if item.experiment_id == experiment_id)
        claims = (*record.claims, *(item for item in catalog.paper_claims if item.target_id == experiment_id))
        by_id = {}
        for item in claims:
            by_id.setdefault(item.id, item)
        return tuple(by_id.values())

    def _compare_claim(self, selection, experiment_id, claim, final_result, metrics, identities, used, policy):
        paper_identity = self.normalizer.identify(
            claim.metric_name,
            split=claim.split,
            unit=claim.unit,
            condition=claim.condition,
        )
        same_name = tuple(
            index for index, identity in enumerate(identities)
            if index not in used and identity.normalized_name == paper_identity.normalized_name
        )
        compatible = tuple(
            index for index in same_name if self.normalizer.compatible(paper_identity, identities[index])[0]
        )
        if not compatible:
            if same_name:
                index = same_name[0]
                _, reason = self.normalizer.compatible(paper_identity, identities[index])
                return self._non_numeric_comparison(
                    selection,
                    experiment_id,
                    claim,
                    MetricComparisonStatus.NOT_COMPARABLE,
                    reason,
                    policy,
                    final_result=final_result,
                    final_metric=metrics[index],
                    reproduced_identity=identities[index],
                ), index
            return self._non_numeric_comparison(
                selection,
                experiment_id,
                claim,
                MetricComparisonStatus.MISSING_FINAL_RESULT,
                "canonical FinalResult has no record matching the paper metric identity",
                policy,
                final_result=final_result,
            ), None

        index = self._preferred_match(paper_identity, identities, compatible)
        metric = metrics[index]
        if metric.status is FinalMetricStatus.MISSING:
            status = MetricComparisonStatus.MISSING_FINAL_RESULT
            reason = "required final metric is explicitly marked missing"
        elif metric.status is FinalMetricStatus.UNAVAILABLE:
            status = MetricComparisonStatus.UNAVAILABLE_FINAL_RESULT
            reason = "required final metric is explicitly marked unavailable"
        else:
            return self._numeric_comparison(selection, experiment_id, claim, final_result, metric, paper_identity, identities[index], policy), index
        return self._non_numeric_comparison(
            selection,
            experiment_id,
            claim,
            status,
            reason,
            policy,
            final_result=final_result,
            final_metric=metric,
            reproduced_identity=identities[index],
        ), index

    @staticmethod
    def _preferred_match(paper_identity, identities, candidates):
        exact = tuple(index for index in candidates if identities[index].original_name.casefold() == paper_identity.original_name.casefold())
        return (exact or candidates)[0]

    def _numeric_comparison(self, selection, experiment_id, claim, final_result, metric, paper_identity, reproduced_identity, policy):
        paper_value, reproduced_value, proportion = self._normalize_values(
            claim.value,
            claim.unit,
            metric.value,
            metric.unit,
        )
        difference = abs(paper_value - reproduced_value)
        relative = Decimal(0) if difference == 0 else (difference / abs(paper_value) if paper_value != 0 else None)
        status, reason = self._apply_policy(difference, relative, policy)
        return MetricComparison(
            comparison_id=_comparison_id(experiment_id, claim.id, final_result.result_id, metric.name),
            paper_experiment_id=experiment_id,
            paper_claim_id=claim.id,
            paper_metric=paper_identity,
            reproduced_metric=reproduced_identity,
            paper_value=claim.value,
            reproduced_value=metric.value,
            normalized_paper_value=float(paper_value),
            normalized_reproduced_value=float(reproduced_value),
            absolute_difference=float(difference),
            relative_difference=None if relative is None else float(relative),
            percentage_point_difference=float(difference * 100) if proportion else None,
            policy=policy,
            status=status,
            reason=reason,
            evidence_chain=self._evidence(selection, experiment_id, claim, final_result, metric),
        )

    @staticmethod
    def _normalize_values(paper_value, paper_unit, reproduced_value, reproduced_unit):
        left = Decimal(str(paper_value))
        right = Decimal(str(reproduced_value))
        percent_keys = {_text_key(value) for value in _PERCENT_UNITS if _text_key(value)}
        ratio_keys = {_text_key(value) for value in _RATIO_UNITS if _text_key(value)}
        left_key = _text_key(paper_unit)
        right_key = _text_key(reproduced_unit)
        left_percent = paper_unit == "%" or bool(left_key and left_key in percent_keys)
        right_percent = reproduced_unit == "%" or bool(right_key and right_key in percent_keys)
        left_ratio = bool(left_key and left_key in ratio_keys)
        right_ratio = bool(right_key and right_key in ratio_keys)
        if left_percent:
            left /= Decimal(100)
        if right_percent:
            right /= Decimal(100)
        inferred = False
        if not any((paper_unit, reproduced_unit)):
            if abs(left) > 1 and abs(left) <= 100 and abs(right) <= 1:
                left /= Decimal(100); inferred = True
            elif abs(right) > 1 and abs(right) <= 100 and abs(left) <= 1:
                right /= Decimal(100); inferred = True
        return left, right, bool(left_percent or right_percent or left_ratio or right_ratio or inferred)

    @staticmethod
    def _apply_policy(difference, relative, policy):
        if difference == 0:
            return MetricComparisonStatus.MATCH, "normalized paper and reproduced values are equal"
        if policy.kind is ComparisonPolicyKind.NO_THRESHOLD:
            return MetricComparisonStatus.MEASURED_DEVIATION, "deviation is measured without an explicit success threshold"
        if policy.kind is ComparisonPolicyKind.EXACT:
            return MetricComparisonStatus.OUTSIDE_TOLERANCE, "EXACT policy requires equal normalized values"
        threshold = Decimal(str(policy.tolerance))
        if policy.kind is ComparisonPolicyKind.ABSOLUTE_TOLERANCE:
            within = difference <= threshold
            reason = "absolute difference is within tolerance" if within else "absolute difference exceeds tolerance"
        else:
            within = relative is not None and relative <= threshold
            reason = "relative difference is within tolerance" if within else "relative difference exceeds tolerance or is undefined"
        return (MetricComparisonStatus.WITHIN_TOLERANCE if within else MetricComparisonStatus.OUTSIDE_TOLERANCE), reason

    def _non_numeric_comparison(
        self,
        selection,
        experiment_id,
        claim,
        status,
        reason,
        policy,
        *,
        final_result=None,
        final_metric=None,
        reproduced_identity=None,
        failure=None,
    ):
        return MetricComparison(
            comparison_id=_comparison_id(experiment_id, claim.id, getattr(final_result, "result_id", "none"), status.value),
            paper_experiment_id=experiment_id,
            paper_claim_id=claim.id,
            paper_metric=self.normalizer.identify(claim.metric_name, split=claim.split, unit=claim.unit, condition=claim.condition),
            reproduced_metric=reproduced_identity,
            paper_value=claim.value,
            reproduced_value=getattr(final_metric, "value", None),
            policy=policy,
            status=status,
            reason=reason,
            evidence_chain=self._evidence(selection, experiment_id, claim, final_result, final_metric, failure),
        )

    @staticmethod
    def _evidence(selection, experiment_id, claim, final_result=None, final_metric=None, failure=None):
        runs = () if final_result is None else final_result.runs
        checkpoints = tuple(dict.fromkeys(item.selected_checkpoint for item in runs if item.selected_checkpoint))
        return ComparisonEvidenceChain(
            selection_mode=selection.selection_mode,
            original_user_goal=selection.original_user_goal,
            selection_reason=selection.per_experiment_reasons.get(experiment_id, selection.selection_reason),
            paper_experiment_id=experiment_id,
            paper_claim_id=claim.id,
            paper_evidence=claim.evidence,
            final_result_id=getattr(final_result, "result_id", None),
            run_result_ids=tuple(item.result_id for item in runs),
            run_ids=tuple(item.run_id for item in runs),
            seeds=tuple(item.seed for item in runs),
            checkpoint_references=checkpoints,
            selected_epochs=tuple(item.selected_epoch for item in runs),
            aggregation=getattr(final_result, "aggregation", None),
            final_result_evidence=getattr(final_result, "evidence", ()),
            final_result_provenance=getattr(final_result, "provenance", {}),
            final_metric_evidence=getattr(final_metric, "evidence", ()),
            final_metric_provenance=getattr(final_metric, "provenance", {}),
            execution_failure_id=getattr(failure, "failure_id", None),
            execution_failure_provenance=getattr(failure, "provenance", {}),
        )
