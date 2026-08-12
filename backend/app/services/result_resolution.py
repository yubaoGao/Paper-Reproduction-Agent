"""Repository-neutral conversion of heterogeneous outputs into FinalResult."""

from __future__ import annotations

import hashlib
import statistics
from typing import Protocol, runtime_checkable

from pydantic import Field

from backend.app.domain.experiment import (
    Artifact,
    DomainModel,
    EvaluationPolicy,
    FinalMetric,
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
    provenance: dict[NonEmptyStr, str] = Field(default_factory=dict)


@runtime_checkable
class RepositoryResultAdapter(Protocol):
    """Repository-specific bounded interpretation; format details stay here."""

    def resolve_runs(
        self,
        request: ResultResolutionRequest,
    ) -> tuple[RunFinalResult, ...]: ...


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
    provenance: dict[str, str] | None = None,
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
        if tuple(item.name for item in run.reporting_metrics) != expected:
            raise ValueError("adapter reporting metrics differ from EvaluationPolicy")
        if any(item.split != policy.reporting_split for item in run.reporting_metrics):
            raise ValueError("adapter reporting split differs from EvaluationPolicy")

    if policy.aggregation is ResultAggregation.NONE:
        metrics = runs[0].reporting_metrics
    else:
        aggregated = []
        for index, name in enumerate(expected):
            values = [run.reporting_metrics[index].value for run in runs]
            template = runs[0].reporting_metrics[index]
            std = statistics.stdev(values) if policy.aggregation is ResultAggregation.MEAN_STD else None
            aggregated.append(
                FinalMetric(
                    name=name,
                    value=statistics.fmean(values),
                    split=policy.reporting_split,
                    unit=template.unit,
                    std=std,
                    evidence=tuple(
                        evidence
                        for run in runs
                        for metric in run.reporting_metrics[index:index + 1]
                        for evidence in metric.evidence
                    ),
                    provenance={"aggregation": policy.aggregation.value},
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
