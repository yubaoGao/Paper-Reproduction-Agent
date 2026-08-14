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
        session_id = getattr(job, "session_id", None)
        if intake is None and session_id and hasattr(self.persistence, "sessions"):
            session = self.persistence.sessions.get(session_id)
            intake = self.persistence.intakes.get(session.origin_intake_id)
        if intake is None:
            raise PersistenceEntityNotFoundError(f"job {job_id!r} has no product intake")
        try:
            return self.persistence.events.append(
                intake_id=intake.intake_id, job_id=job_id,
                session_id=session_id or intake.session_id,
                owner_principal=job.owner_principal,
                event_type=event_type, payload=payload,
            )
        except TypeError:
            return self.persistence.events.append(
                intake_id=intake.intake_id, job_id=job_id,
                owner_principal=job.owner_principal,
                event_type=event_type, payload=payload,
            )
