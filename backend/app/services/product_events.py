"""Worker-facing publisher for sanitized persistent product events."""

from __future__ import annotations

from backend.app.domain import ReproductionEventType
from backend.app.services.persistence import PersistenceEntityNotFoundError


class ProductEventPublisher:
    """Lets queue/GPU/orchestration integrations emit product events without HTTP dependencies."""

    def __init__(self, persistence) -> None:
        self.persistence = persistence

    def publish(self, job_id: str, event_type: ReproductionEventType, payload: dict):
        job = self.persistence.jobs.get(job_id)
        intake = next((item for item in self.persistence.intakes.list_by_owner(job.owner_principal) if item.job_id == job_id), None)
        if intake is None:
            raise PersistenceEntityNotFoundError(f"job {job_id!r} has no product intake")
        return self.persistence.events.append(
            intake_id=intake.intake_id, job_id=job_id,
            owner_principal=job.owner_principal,
            event_type=event_type, payload=payload,
        )
