"""Project catalog experiments onto session job history without mutating catalogs."""

from __future__ import annotations

from backend.app.domain import (
    ExperimentJobHistoryItem,
    ReproductionJob,
    ReproductionJobStatus,
    SessionExperimentProjection,
    SessionExperimentStatus,
)


_RUNNING = {
    ReproductionJobStatus.CLAIMED,
    ReproductionJobStatus.RUNNING,
    ReproductionJobStatus.CANCEL_REQUESTED,
}
_QUEUED = {
    ReproductionJobStatus.PENDING,
    ReproductionJobStatus.PLANNING,
    ReproductionJobStatus.READY,
    ReproductionJobStatus.QUEUED,
}


def job_status_to_session_status(status: ReproductionJobStatus) -> SessionExperimentStatus:
    if status in _RUNNING:
        return SessionExperimentStatus.RUNNING
    if status in _QUEUED:
        return SessionExperimentStatus.QUEUED
    if status is ReproductionJobStatus.SUCCEEDED:
        return SessionExperimentStatus.COMPLETED
    if status is ReproductionJobStatus.FAILED:
        return SessionExperimentStatus.FAILED
    if status is ReproductionJobStatus.CANCELLED:
        return SessionExperimentStatus.CANCELLED
    return SessionExperimentStatus.QUEUED


def completed_experiment_ids(jobs: tuple[ReproductionJob, ...]) -> frozenset[str]:
    """Experiments whose latest non-active job succeeded and that are not currently queued/running."""
    latest: dict[str, ReproductionJob] = {}
    active: set[str] = set()
    for job in sorted(jobs, key=lambda item: (item.created_at, item.job_id)):
        status = job_status_to_session_status(job.status)
        for experiment_id in job.selection.selected_experiment_ids:
            latest[experiment_id] = job
            if status in {SessionExperimentStatus.QUEUED, SessionExperimentStatus.RUNNING}:
                active.add(experiment_id)
            else:
                active.discard(experiment_id)
    return frozenset(
        experiment_id
        for experiment_id, job in latest.items()
        if experiment_id not in active
        and job_status_to_session_status(job.status) is SessionExperimentStatus.COMPLETED
    )


def project_session_experiments(catalog, jobs: tuple[ReproductionJob, ...]) -> tuple[SessionExperimentProjection, ...]:
    history_by_experiment: dict[str, list[ExperimentJobHistoryItem]] = {
        record.experiment_id: [] for record in catalog.experiments
    }
    for job in sorted(jobs, key=lambda item: (item.created_at, item.job_id)):
        item = ExperimentJobHistoryItem(
            job_id=job.job_id,
            goal=job.user_goal,
            status=job_status_to_session_status(job.status),
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        for experiment_id in job.selection.selected_experiment_ids:
            history_by_experiment.setdefault(experiment_id, []).append(item)

    projections = []
    for record in catalog.experiments:
        history = tuple(history_by_experiment.get(record.experiment_id, ()))
        status = SessionExperimentStatus.NOT_SELECTED
        current_job_id = None
        if history:
            if any(item.status is SessionExperimentStatus.RUNNING for item in history):
                running = next(item for item in reversed(history) if item.status is SessionExperimentStatus.RUNNING)
                status = SessionExperimentStatus.RUNNING
                current_job_id = running.job_id
            elif any(item.status is SessionExperimentStatus.QUEUED for item in history):
                queued = next(item for item in reversed(history) if item.status is SessionExperimentStatus.QUEUED)
                status = SessionExperimentStatus.QUEUED
                current_job_id = queued.job_id
            else:
                latest = history[-1]
                status = latest.status
                current_job_id = latest.job_id
        projections.append(
            SessionExperimentProjection(
                experiment_id=record.experiment_id,
                name=record.name,
                experiment_type=record.experiment_type.value,
                status=status,
                current_job_id=current_job_id,
                job_history=history,
            )
        )
    return tuple(projections)
