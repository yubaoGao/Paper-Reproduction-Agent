"""Canonical result/comparison closure required before a durable job succeeds."""

from __future__ import annotations

from backend.app.domain import (
    PersistedComparisonReport,
    ReproductionEventType,
    ResultValidationStatus,
)

from .result_comparison import DeterministicResultComparator


class JobFinalizationError(RuntimeError):
    pass


class JobResultFinalizer:
    """Validate persisted resolver output and create one deterministic comparison."""

    def __init__(self, persistence, *, comparator=None, event_publisher=None) -> None:
        self.persistence = persistence
        self.comparator = comparator or DeterministicResultComparator()
        self.event_publisher = event_publisher

    def finalize(self, job, run):
        if not run.final_results:
            raise JobFinalizationError("successful execution produced no canonical FinalResult")
        intake = next(
            (
                item
                for item in self.persistence.intakes.list_by_owner(job.owner_principal)
                if item.job_id == job.job_id
            ),
            None,
        )
        paper_catalog = None if intake is None else intake.paper_catalog
        if paper_catalog is None and getattr(job, "session_id", None) and hasattr(self.persistence, "sessions"):
            session = self.persistence.sessions.get(job.session_id)
            paper_catalog = session.paper_catalog
        if paper_catalog is None:
            raise JobFinalizationError("job has no authoritative paper catalog")

        # Aggregate actions follow their run-producing actions in the locked
        # manifest, so the last result for an experiment is the canonical one.
        selected = set(job.selection.selected_experiment_ids)
        by_experiment = {}
        for result in run.final_results:
            if result.paper_experiment_id in selected:
                by_experiment[result.paper_experiment_id] = result
        missing = selected - set(by_experiment)
        if missing:
            raise JobFinalizationError(
                "canonical FinalResult is missing for selected experiments: "
                + ", ".join(sorted(missing))
            )
        final_results = tuple(
            by_experiment[experiment_id]
            for experiment_id in job.selection.selected_experiment_ids
        )

        persisted = {
            item.result.result_id: item
            for item in self.persistence.final_results.list_by_run(run.run_id)
        }
        for result in final_results:
            record = persisted.get(result.result_id)
            if record is None:
                raise JobFinalizationError(
                    f"canonical FinalResult {result.result_id!r} was not durably persisted"
                )
            if record.validation_status is not ResultValidationStatus.VALID:
                self.persistence.final_results.update_validation(
                    result.result_id, ResultValidationStatus.VALID,
                )
                self._publish(job.job_id, ReproductionEventType.FINAL_RESULT_ACQUIRED, {
                    "result_id": result.result_id,
                    "paper_experiment_id": result.paper_experiment_id,
                })

        report = self.comparator.compare(
            job.selection,
            paper_catalog,
            final_results,
        )
        existing = {
            item.report.report_id: item
            for item in self.persistence.comparisons.list_by_job(job.job_id)
        }
        if report.report_id not in existing:
            self.persistence.comparisons.create(
                PersistedComparisonReport(job_id=job.job_id, report=report)
            )
            self._publish(job.job_id, ReproductionEventType.COMPARISON_COMPLETED, {
                "report_id": report.report_id,
                "selected_experiment_ids": list(report.selected_experiment_ids),
            })
        return report

    def _publish(self, job_id, event_type, payload) -> None:
        if self.event_publisher is not None:
            self.event_publisher.publish(job_id, event_type, payload)
