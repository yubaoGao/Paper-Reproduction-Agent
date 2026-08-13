"""HTTP-independent application service for the ReproPilot product workflow."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from backend.app.domain import (
    AuthoritativePlanningSnapshot, GoalResolutionResult, GoalResolutionStatus,
    PaperCodeAlignmentCatalog, PaperExperimentCatalog, PaperReference,
    PlanStatus, RepositoryAnalysisCatalog, ReproductionEventType,
    ReproductionExecutionPlan, ReproductionIntake, ReproductionIntakeState,
    ReproductionJob, ReproductionJobStatus, UserReproductionGoal,
)
from backend.app.services.external_resources import ExternalResourceResolutionService
from backend.app.services.persistence import PersistenceEntityNotFoundError


class APIUseCaseError(RuntimeError):
    code = "api_use_case_error"


class EntityNotFoundError(APIUseCaseError):
    code = "not_found"


class InvalidIntakeStateError(APIUseCaseError):
    code = "invalid_intake_state"


class PlanningBlockedError(APIUseCaseError):
    code = "planning_blocked"


@dataclass(frozen=True)
class IntakeAnalysis:
    paper: PaperReference
    paper_catalog: PaperExperimentCatalog
    repository_catalog: RepositoryAnalysisCatalog
    alignment_catalog: PaperCodeAlignmentCatalog
    goal_resolution: GoalResolutionResult


class ReproductionAnalysisPipeline(Protocol):
    """Composition port implemented with the existing Task 05--10 services."""

    def analyze(
        self, *, intake_id: str, source_filename: str, paper_pdf: bytes,
        repository_url: str, goal: str,
    ) -> IntakeAnalysis: ...

    def clarify(
        self, *, intake: ReproductionIntake, answers: tuple[str, ...],
    ) -> GoalResolutionResult: ...

    def plan(self, *, intake: ReproductionIntake) -> ReproductionExecutionPlan: ...


class ReproductionAPIService:
    """Owns product transitions but delegates all scientific work to existing services."""

    def __init__(self, persistence, pipeline: ReproductionAnalysisPipeline, resource_service: ExternalResourceResolutionService):
        self.persistence = persistence
        self.pipeline = pipeline
        self.resource_service = resource_service

    def create_intake(self, *, principal: str, source_filename: str, paper_pdf: bytes, repository_url: str, goal: str):
        now = datetime.now(timezone.utc)
        intake = ReproductionIntake(
            intake_id=f"intake:{uuid.uuid4().hex}", owner_principal=principal,
            source_filename=source_filename, repository_url=repository_url,
            user_goal=goal, state=ReproductionIntakeState.ANALYZING,
            created_at=now, updated_at=now,
        )
        self.persistence.intakes.create(intake)
        self._event(intake, ReproductionEventType.PAPER_ANALYSIS_STARTED, {"filename": source_filename})
        analysis = self.pipeline.analyze(
            intake_id=intake.intake_id, source_filename=source_filename,
            paper_pdf=paper_pdf, repository_url=repository_url, goal=goal,
        )
        self._event(intake, ReproductionEventType.PAPER_ANALYSIS_COMPLETED, {"paper_id": analysis.paper.id})
        self._event(intake, ReproductionEventType.REPOSITORY_ANALYSIS_STARTED, {"repository_url": repository_url})
        self._event(intake, ReproductionEventType.REPOSITORY_ANALYSIS_COMPLETED, {"repository_catalog_id": analysis.repository_catalog.catalog_id})
        intake = intake.model_copy(update={
            "paper": analysis.paper, "paper_catalog": analysis.paper_catalog,
            "repository_catalog": analysis.repository_catalog,
            "alignment_catalog": analysis.alignment_catalog,
            "goal_resolution": analysis.goal_resolution, "updated_at": datetime.now(timezone.utc),
        })
        return self._continue_after_goal(intake)

    def clarify(self, intake_id: str, *, principal: str, answers: tuple[str, ...]):
        intake = self._owned_intake(intake_id, principal)
        if intake.state is not ReproductionIntakeState.AMBIGUOUS:
            raise InvalidIntakeStateError("intake is not waiting for clarification")
        resolution = self.pipeline.clarify(intake=intake, answers=answers)
        resolved_goal = (
            resolution.selection.original_user_goal
            if resolution.selection is not None
            else intake.user_goal
        )
        intake = intake.model_copy(update={
            "goal_resolution": resolution,
            "user_goal": resolved_goal,
            "clarification_answers": (*intake.clarification_answers, *answers),
            "state": ReproductionIntakeState.ANALYZING,
            "waiting_reason": None,
            "updated_at": datetime.now(timezone.utc),
        })
        return self._continue_after_goal(intake)

    def submit_resource(self, intake_id: str, *, principal: str, requirement_id: str, host_path: str):
        intake = self._owned_intake(intake_id, principal)
        if intake.state is not ReproductionIntakeState.WAITING_FOR_RESOURCE or intake.resource_resolution is None:
            raise InvalidIntakeStateError("intake is not waiting for an external resource")
        report = self.resource_service.register_user_path_and_resume(
            intake.resource_resolution, requirement_id=requirement_id,
            host_path=host_path, principal=principal,
            repository_catalog=intake.repository_catalog,
        )
        intake = intake.model_copy(update={
            "resource_resolution": report,
            "updated_at": datetime.now(timezone.utc),
        })
        if not report.ready_to_run:
            intake = intake.model_copy(update={
                "state": ReproductionIntakeState.WAITING_FOR_RESOURCE,
                "waiting_reason": "required external resources are not available",
            })
            self.persistence.intakes.update(intake)
            return intake
        self._event(intake, ReproductionEventType.RESOURCE_RESOLVED, {"requirement_id": requirement_id})
        return self._plan_and_prepare_job(intake)

    def start(self, intake_id: str, *, principal: str):
        intake = self._owned_intake(intake_id, principal)
        if intake.state is not ReproductionIntakeState.READY_TO_RUN or intake.job_id is None:
            raise InvalidIntakeStateError("intake is not ready to run")
        job = self.persistence.queue.enqueue(intake.job_id)
        intake = intake.model_copy(update={
            "state": ReproductionIntakeState.QUEUED,
            "updated_at": datetime.now(timezone.utc),
        })
        self.persistence.intakes.update(intake)
        self._event(intake, ReproductionEventType.JOB_QUEUED, {"status": job.status.value}, job_id=job.job_id)
        return job

    def get_intake(self, intake_id: str, *, principal: str):
        intake = self._owned_intake(intake_id, principal)
        if intake.job_id is None:
            return intake
        job = self.get_job(intake.job_id, principal=principal)
        state = {
            ReproductionJobStatus.READY: ReproductionIntakeState.READY_TO_RUN,
            ReproductionJobStatus.QUEUED: ReproductionIntakeState.QUEUED,
            ReproductionJobStatus.CLAIMED: ReproductionIntakeState.RUNNING,
            ReproductionJobStatus.RUNNING: ReproductionIntakeState.RUNNING,
            ReproductionJobStatus.SUCCEEDED: ReproductionIntakeState.TERMINAL,
            ReproductionJobStatus.FAILED: ReproductionIntakeState.TERMINAL,
            ReproductionJobStatus.CANCELLED: ReproductionIntakeState.TERMINAL,
        }.get(job.status, intake.state)
        if state is not intake.state:
            intake = intake.model_copy(update={"state": state, "updated_at": job.updated_at})
            self.persistence.intakes.update(intake)
        return intake

    def get_job(self, job_id: str, *, principal: str):
        try:
            job = self.persistence.jobs.get(job_id)
        except PersistenceEntityNotFoundError as exc:
            raise EntityNotFoundError("reproduction not found") from exc
        if job.owner_principal != principal:
            raise EntityNotFoundError("reproduction not found")
        return job

    def list_jobs(self, *, principal: str):
        if hasattr(self.persistence.jobs, "list_by_owner"):
            return self.persistence.jobs.list_by_owner(principal)
        return tuple(job for job in self.persistence.jobs.list() if job.owner_principal == principal)

    def job_detail(self, job_id: str, *, principal: str):
        job = self.get_job(job_id, principal=principal)
        runs = self.persistence.runs.list_by_job(job_id)
        intake = self._intake_for_job(job_id, principal)
        events = self.persistence.events.list_by_job(job_id)
        return job, runs, intake, events

    def cancel(self, job_id: str, *, principal: str):
        self.get_job(job_id, principal=principal)
        job = self.persistence.queue.request_cancel(job_id)
        if job.status is ReproductionJobStatus.CANCELLED:
            intake = self._intake_for_job(job_id, principal)
            self._event(intake, ReproductionEventType.JOB_CANCELLED, {}, job_id=job_id)
        return job

    def results(self, job_id: str, *, principal: str):
        self.get_job(job_id, principal=principal)
        return tuple(item.result for item in self.persistence.final_results.list_by_job(job_id))

    def comparison(self, job_id: str, *, principal: str):
        self.get_job(job_id, principal=principal)
        reports = self.persistence.comparisons.list_by_job(job_id)
        if not reports:
            raise EntityNotFoundError("comparison report not found")
        return reports[-1].report

    def events(self, job_id: str, *, principal: str, after_sequence: int = 0):
        self.get_job(job_id, principal=principal)
        return self.persistence.events.list_by_job(job_id, after_sequence=after_sequence)

    def _continue_after_goal(self, intake: ReproductionIntake):
        resolution = intake.goal_resolution
        if resolution is None:
            raise APIUseCaseError("analysis omitted goal resolution")
        if resolution.status is not GoalResolutionStatus.RESOLVED:
            intake = intake.model_copy(update={
                "state": ReproductionIntakeState.AMBIGUOUS,
                "waiting_reason": resolution.reason or "clarification is required",
                "updated_at": datetime.now(timezone.utc),
            })
            self.persistence.intakes.update(intake)
            self._event(intake, ReproductionEventType.CLARIFICATION_REQUIRED, {
                "candidate_experiment_ids": list(resolution.candidate_experiment_ids),
                "questions": list(resolution.clarification_questions),
            })
            return intake

        self._event(intake, ReproductionEventType.EXPERIMENT_SELECTION_RESOLVED, {
            "selected_experiment_ids": list(resolution.selection.selected_experiment_ids),
        })
        report = self.resource_service.resolve(
            intake_id=intake.intake_id, principal=intake.owner_principal,
            selection=resolution.selection, specification=resolution.specification,
            paper_catalog=intake.paper_catalog, repository_catalog=intake.repository_catalog,
        )
        intake = intake.model_copy(update={"resource_resolution": report})
        if not report.ready_to_run:
            intake = intake.model_copy(update={
                "state": ReproductionIntakeState.WAITING_FOR_RESOURCE,
                "waiting_reason": "required external resources are not available",
                "updated_at": datetime.now(timezone.utc),
            })
            self.persistence.intakes.update(intake)
            for item in report.resolutions:
                if item.binding is None:
                    self._event(intake, ReproductionEventType.RESOURCE_REQUIRED, {
                        "requirement_id": item.requirement.requirement_id,
                        "resource_name": item.requirement.canonical_name,
                        "resource_type": item.requirement.resource_type.value,
                    })
            return intake
        return self._plan_and_prepare_job(intake)

    def _plan_and_prepare_job(self, intake: ReproductionIntake):
        if (
            intake.goal_resolution is None
            or intake.goal_resolution.status is not GoalResolutionStatus.RESOLVED
            or intake.goal_resolution.selection is None
            or not intake.goal_resolution.selection.selected_experiment_ids
        ):
            raise InvalidIntakeStateError("planning requires a resolved, locked experiment selection")
        if intake.resource_resolution is None or not intake.resource_resolution.ready_to_run:
            raise InvalidIntakeStateError("planning requires all external resources to be available")
        self._event(intake, ReproductionEventType.PLANNING_STARTED, {})
        plan = self.pipeline.plan(intake=intake)
        self._event(intake, ReproductionEventType.PLANNING_COMPLETED, {
            "plan_id": plan.plan_id, "status": plan.status.value,
            "blocker_codes": [item.code for item in plan.blockers],
        })
        if plan.status is not PlanStatus.READY or plan.blockers:
            intake = intake.model_copy(update={
                "execution_plan": plan, "state": ReproductionIntakeState.AMBIGUOUS,
                "waiting_reason": "planning has unresolved blockers",
                "updated_at": datetime.now(timezone.utc),
            })
            self.persistence.intakes.update(intake)
            return intake

        job_id = intake.job_id or f"job:{uuid.uuid4().hex}"
        selection = intake.goal_resolution.selection
        job = ReproductionJob(
            job_id=job_id, owner_principal=intake.owner_principal,
            paper=intake.paper, user_goal=intake.user_goal, selection=selection,
            status=ReproductionJobStatus.READY,
        )
        if intake.job_id is None:
            self.persistence.jobs.create(job)
            if hasattr(self.persistence.events, "bind_job"):
                self.persistence.events.bind_job(intake.intake_id, job_id)
            self.persistence.planning_snapshots.create(AuthoritativePlanningSnapshot(
                snapshot_id=f"planning-snapshot:{uuid.uuid4().hex}", job_id=job_id,
                specification=intake.goal_resolution.specification, execution_plan=plan,
            ))
        intake = intake.model_copy(update={
            "execution_plan": plan, "job_id": job_id,
            "state": ReproductionIntakeState.READY_TO_RUN, "waiting_reason": None,
            "updated_at": datetime.now(timezone.utc),
        })
        self.persistence.intakes.update(intake)
        return intake

    def _owned_intake(self, intake_id: str, principal: str):
        try:
            intake = self.persistence.intakes.get(intake_id)
        except PersistenceEntityNotFoundError as exc:
            raise EntityNotFoundError("intake not found") from exc
        if intake.owner_principal != principal:
            raise EntityNotFoundError("intake not found")
        return intake

    def _intake_for_job(self, job_id: str, principal: str):
        intake = next((item for item in self.persistence.intakes.list_by_owner(principal) if item.job_id == job_id), None)
        if intake is None:
            raise EntityNotFoundError("reproduction not found")
        return intake

    def _event(self, intake, event_type, payload, *, job_id=None):
        return self.persistence.events.append(
            intake_id=intake.intake_id, owner_principal=intake.owner_principal,
            job_id=job_id or intake.job_id, event_type=event_type, payload=payload,
        )
