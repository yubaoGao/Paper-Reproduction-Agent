"""Repository-neutral conversion of heterogeneous outputs into FinalResult."""

from __future__ import annotations

import hashlib
import statistics
from typing import Protocol, runtime_checkable

from pydantic import Field, JsonValue

from backend.app.domain.experiment import (
    Artifact,
    DomainModel,
    EvaluationPolicy,
    FinalMetric,
    FinalMetricStatus,
    FinalResult,
    Metric,
    NonEmptyStr,
    ResultAggregation,
    RunFinalResult,
)


class ResultResolutionRequest(DomainModel):
    repository_id: NonEmptyStr
    repository_snapshot_id: NonEmptyStr
    paper_experiment_id: NonEmptyStr
    orchestration_run_id: NonEmptyStr
    evaluation_policy: EvaluationPolicy
    observed_metrics: tuple[Metric, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    stdout_reference: NonEmptyStr | None = None
    stderr_reference: NonEmptyStr | None = None
    provenance: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)


@runtime_checkable
class RepositoryResultAdapter(Protocol):
    """Repository-specific bounded interpretation; format details stay here."""

    def resolve_runs(
        self,
        request: ResultResolutionRequest,
    ) -> tuple[RunFinalResult, ...]: ...


class RepositoryResultAdapterRegistry:
    """Explicit repository-specific adapter routing; unknown repositories fail closed."""

    def __init__(self, adapters: dict[str, RepositoryResultAdapter] | None = None) -> None:
        self._adapters = dict(adapters or {})

    def resolve_runs(self, request: ResultResolutionRequest):
        adapter = self._adapters.get(request.repository_id)
        if adapter is None:
            raise ValueError(
                f"repository {request.repository_id!r} has no registered result adapter"
            )
        return adapter.resolve_runs(request)


@runtime_checkable
class ResultResolver(Protocol):
    def resolve(self, request: ResultResolutionRequest) -> FinalResult: ...


class CanonicalResultResolver:
    """Validate adapter output and deterministically aggregate every configured run."""

    def __init__(self, adapter: RepositoryResultAdapter) -> None:
        self.adapter = adapter

    def resolve(self, request: ResultResolutionRequest) -> FinalResult:
        runs = self.adapter.resolve_runs(request)
        return aggregate_final_result(
            request.paper_experiment_id,
            request.evaluation_policy,
            runs,
            provenance={
                "repository_id": request.repository_id,
                "repository_snapshot_id": request.repository_snapshot_id,
                "orchestration_run_id": request.orchestration_run_id,
                **request.provenance,
            },
        )


def aggregate_final_result(
    paper_experiment_id: str,
    policy: EvaluationPolicy,
    runs: tuple[RunFinalResult, ...],
    *,
    provenance: dict[str, JsonValue] | None = None,
) -> FinalResult:
    """Aggregate all runs in policy order; best-seed selection is not representable."""

    if not policy.is_resolved:
        raise ValueError("cannot resolve FinalResult from an unresolved EvaluationPolicy")
    if len(runs) != policy.run_count:
        raise ValueError("adapter must return every configured run; best seed is forbidden")
    if policy.seeds and tuple(item.seed for item in runs) != policy.seeds:
        raise ValueError("adapter run seeds differ from EvaluationPolicy")
    expected = tuple(policy.reporting_metrics)
    for run in runs:
        metrics_by_name = {item.name: item for item in run.reporting_metrics}
        if any(name not in metrics_by_name for name in expected):
            raise ValueError("adapter omitted a required reporting metric record")
        if any(metrics_by_name[name].split != policy.reporting_split for name in expected):
            raise ValueError("adapter required reporting split differs from EvaluationPolicy")

    if policy.aggregation is ResultAggregation.NONE:
        metrics = runs[0].reporting_metrics
    else:
        aggregated = []
        ordered_names = tuple(dict.fromkeys((*expected,*(item.name for run in runs for item in run.reporting_metrics))))
        metrics_by_run = tuple({item.name:item for item in run.reporting_metrics} for run in runs)
        for name in ordered_names:
            observed = tuple(metrics.get(name) for metrics in metrics_by_run)
            template = next(item for item in observed if item is not None)
            available = all(item is not None and item.status is FinalMetricStatus.AVAILABLE for item in observed)
            if available:
                values = [item.value for item in observed]
                status = FinalMetricStatus.AVAILABLE
                value = statistics.fmean(values)
                std = statistics.stdev(values) if policy.aggregation is ResultAggregation.MEAN_STD else None
            else:
                status = FinalMetricStatus.UNAVAILABLE if any(item is None or item.status is FinalMetricStatus.UNAVAILABLE for item in observed) else FinalMetricStatus.MISSING
                value = None
                std = None
            aggregated.append(
                FinalMetric(
                    name=name,
                    status=status,
                    value=value,
                    split=policy.reporting_split if name in expected else template.split,
                    unit=template.unit,
                    std=std,
                    evidence=tuple(
                        evidence
                        for metric in observed if metric is not None
                        for evidence in metric.evidence
                    ),
                    provenance={"aggregation": policy.aggregation.value,"run_count":len(runs)},
                )
            )
        metrics = tuple(aggregated)

    digest = hashlib.sha256(
        "\x1f".join((paper_experiment_id, *(item.result_id for item in runs))).encode()
    ).hexdigest()[:20]
    return FinalResult(
        result_id=f"final-result:{digest}",
        paper_experiment_id=paper_experiment_id,
        evaluation_policy=policy,
        runs=runs,
        reporting_metrics=tuple(metrics),
        aggregation=policy.aggregation,
        evidence=tuple(evidence for run in runs for evidence in run.evidence),
        provenance=dict(provenance or {}),
    )
