"""HTTP-independent application service for the ReproPilot product workflow."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from backend.app.domain import (
    ANALYSIS_ARTIFACT_STORE_FAILED, ANALYSIS_ENQUEUE_FAILED, ANALYSIS_FAILED,
    ANALYSIS_LLM_BUDGET_EXCEEDED, ANALYSIS_TIMEOUT, GOAL_NOT_FOUND,
    REPOSITORY_SNAPSHOT_MISSING,
    AnalysisJobStatus, AuthoritativePlanningSnapshot, GoalResolutionResult, GoalResolutionStatus,
    IntakeAnalysisJob, IntakeAnalysisPhase, PaperCodeAlignmentCatalog, PaperDocument,
    PaperExperimentCatalog, PaperReference,
    PlanStatus, RepositoryAnalysisCatalog, ReproductionEventType,
    ReproductionExecutionPlan, ReproductionIntake, ReproductionIntakeState,
    ReproductionJob, ReproductionJobStatus, ReproductionSession,
    ReproductionSessionStatus, RepositorySnapshot, UserReproductionGoal,
    ResultValidationStatus,
)
from backend.app.llm.budget import (
    AnalysisLLMBudget, AnalysisLLMBudgetExceeded, AnalysisLLMBudgetSettings,
    AnalysisLeaseLostError, AnalysisTimeoutError,
)
from backend.app.services.analysis_queue import AnalysisJobLeaseLostError
from backend.app.services.external_resources import ExternalResourceResolutionService
from backend.app.services.persistence import PersistenceEntityNotFoundError
from backend.app.services.session_projection import completed_experiment_ids, project_session_experiments


class APIUseCaseError(RuntimeError):
    code = "api_use_case_error"


class EntityNotFoundError(APIUseCaseError):
    code = "not_found"


class InvalidIntakeStateError(APIUseCaseError):
    code = "invalid_intake_state"


class InvalidSessionStateError(APIUseCaseError):
    code = "invalid_session_state"


class PlanningBlockedError(APIUseCaseError):
    code = "planning_blocked"


class IntakeBootstrapError(APIUseCaseError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


_ANALYSIS_SKIP_STATES = {
    ReproductionIntakeState.AMBIGUOUS,
    ReproductionIntakeState.WAITING_FOR_RESOURCE,
    ReproductionIntakeState.READY_TO_RUN,
    ReproductionIntakeState.QUEUED,
    ReproductionIntakeState.RUNNING,
    ReproductionIntakeState.FAILED,
    ReproductionIntakeState.TERMINAL,
}


_REMAINING_PHRASES = (
    "remaining",
    "the rest",
    "rest of",
    "not yet",
    "remaining experiments",
    "剩余",
    "剩下",
    "其余",
    "还未",
    "尚未",
    "未完成",
    "还没复现",
    "尚未复现",
)


@dataclass(frozen=True)
class IntakeAnalysis:
    paper: PaperReference
    paper_catalog: PaperExperimentCatalog
    goal_resolution: GoalResolutionResult
    repository_catalog: RepositoryAnalysisCatalog | None = None
    alignment_catalog: PaperCodeAlignmentCatalog | None = None
    repository_snapshot: RepositorySnapshot | None = None
    paper_document: PaperDocument | None = None


class ReproductionAnalysisPipeline(Protocol):
    """Composition port implemented with the existing Task 05--10 services."""

    def analyze(
        self, *, intake_id: str, source_filename: str, paper_pdf: bytes,
        repository_url: str, goal: str, on_event=None, on_phase=None,
        on_checkpoint=None, on_snapshot=None, paper=None, paper_catalog=None,
        paper_document=None, repository_catalog=None, alignment_catalog=None,
        repository_snapshot=None,
    ) -> IntakeAnalysis: ...

    def clarify(
        self, *, intake: ReproductionIntake, answers: tuple[str, ...],
    ) -> GoalResolutionResult: ...

    def plan(self, *, intake: ReproductionIntake | None = None, specification=None,
             paper_catalog=None, repository_catalog=None, alignment_catalog=None) -> ReproductionExecutionPlan: ...

    def resolve_goal(self, *, catalog, goal) -> GoalResolutionResult: ...

    def resolve_experiment_ids(self, *, catalog, goal, experiment_ids) -> GoalResolutionResult: ...


class ReproductionAPIService:
    """Owns product transitions but delegates all scientific work to existing services."""

    def __init__(
        self, persistence, pipeline: ReproductionAnalysisPipeline,
        resource_service: ExternalResourceResolutionService, *,
        analysis_queue=None, paper_artifacts=None,
        analysis_settings: AnalysisLLMBudgetSettings | None = None,
    ):
        self.persistence = persistence
        self.pipeline = pipeline
        self.resource_service = resource_service
        self.analysis_queue = analysis_queue if analysis_queue is not None else getattr(persistence, "analysis_queue", None)
        self.paper_artifacts = paper_artifacts if paper_artifacts is not None else getattr(persistence, "paper_artifacts", None)
        self.analysis_settings = analysis_settings or AnalysisLLMBudgetSettings.from_env()

    def create_intake(self, *, principal: str, source_filename: str, paper_pdf: bytes, repository_url: str, goal: str):
        if self.analysis_queue is None or self.paper_artifacts is None:
            raise APIUseCaseError("production persistence omitted the intake analysis queue")
        now = datetime.now(timezone.utc)
        intake = ReproductionIntake(
            intake_id=f"intake:{uuid.uuid4().hex}", owner_principal=principal,
            source_filename=source_filename, repository_url=repository_url,
            user_goal=goal, state=ReproductionIntakeState.ANALYZING,
            current_phase=IntakeAnalysisPhase.PENDING,
            created_at=now, updated_at=now,
        )
        created = False
        stored = False
        try:
            self.persistence.intakes.create(intake)
            created = True
            try:
                artifact_uri = self.paper_artifacts.store(intake.intake_id, source_filename, paper_pdf)
            except Exception as exc:
                raise IntakeBootstrapError(
                    ANALYSIS_ARTIFACT_STORE_FAILED, f"failed to persist paper PDF: {exc}",
                ) from exc
            stored = True
            try:
                self.analysis_queue.enqueue(
                    IntakeAnalysisJob(
                        job_id=f"analysis:{intake.intake_id}",
                        intake_id=intake.intake_id,
                        owner_principal=principal,
                        status=AnalysisJobStatus.QUEUED,
                        paper_artifact_uri=artifact_uri,
                        max_attempts=self.analysis_settings.max_job_attempts,
                        enqueued_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
            except Exception as exc:
                raise IntakeBootstrapError(
                    ANALYSIS_ENQUEUE_FAILED, f"failed to enqueue intake analysis: {exc}",
                ) from exc
            return intake
        except Exception as exc:
            if created:
                code = getattr(exc, "code", ANALYSIS_FAILED) or ANALYSIS_FAILED
                try:
                    self.fail_analysis(
                        intake.intake_id, error_code=code,
                        error_message=str(exc) or "intake analysis could not be started",
                        failed_phase=IntakeAnalysisPhase.PENDING,
                    )
                except Exception:
                    pass
            if stored:
                try:
                    self.paper_artifacts.delete(intake.intake_id)
                except Exception:
                    pass
            raise

    def execute_analysis_job(self, job: IntakeAnalysisJob, *, interrupt_check=None) -> ReproductionIntake:
        intake = self.persistence.intakes.get(job.intake_id)
        if intake.state in _ANALYSIS_SKIP_STATES:
            return intake
        if intake.repository_catalog is not None:
            snapshot = self._load_repository_snapshot(intake.repository_catalog.snapshot_id)
            if snapshot is None:
                return self.fail_analysis(
                    intake.intake_id, error_code=REPOSITORY_SNAPSHOT_MISSING,
                    error_message="repository snapshot is missing after repository analysis completed",
                    failed_phase=intake.current_phase or IntakeAnalysisPhase.REPOSITORY_ANALYZING,
                )
        else:
            snapshot = None
        paper_pdf = self.paper_artifacts.load(job.intake_id)
        job = self.analysis_queue.mark_analysis_started(job.job_id, job.worker_id, job.lease_token)
        budget = AnalysisLLMBudget(
            self.analysis_settings,
            initial_phase_count=job.llm_call_count,
            analysis_started_at=job.analysis_started_at,
            on_http_attempt=lambda: self._persist_llm_http_attempt(job),
            interrupt_check=interrupt_check,
        )
        try:
            with budget.activate():
                budget.preflight()
                analysis = self.pipeline.analyze(
                    intake_id=intake.intake_id,
                    source_filename=intake.source_filename,
                    paper_pdf=paper_pdf,
                    repository_url=intake.repository_url,
                    goal=intake.user_goal,
                    on_event=lambda event_type, payload: self._event(intake, event_type, payload),
                    on_phase=lambda phase: self._set_phase(intake.intake_id, phase),
                    on_checkpoint=lambda fields: self._checkpoint(intake.intake_id, fields),
                    on_snapshot=self._register_snapshot,
                    paper=intake.paper,
                    paper_catalog=intake.paper_catalog,
                    paper_document=intake.paper_document,
                    repository_catalog=intake.repository_catalog,
                    alignment_catalog=intake.alignment_catalog,
                    repository_snapshot=snapshot,
                )
                intake = self.complete_analysis(intake.intake_id, analysis)
        except AnalysisLLMBudgetExceeded as exc:
            return self.fail_analysis(
                intake.intake_id, error_code=ANALYSIS_LLM_BUDGET_EXCEEDED,
                error_message=str(exc), failed_phase=self._current_phase(intake.intake_id),
            )
        except AnalysisTimeoutError as exc:
            return self.fail_analysis(
                intake.intake_id, error_code=ANALYSIS_TIMEOUT,
                error_message=str(exc), failed_phase=self._current_phase(intake.intake_id),
            )
        return intake

    def complete_analysis(self, intake_id: str, analysis: IntakeAnalysis, *, llm_call_count: int | None = None) -> ReproductionIntake:
        intake = self.persistence.intakes.get(intake_id)
        if analysis.repository_snapshot is not None:
            self._register_snapshot(analysis.repository_snapshot)
        persisted = intake.llm_call_count if llm_call_count is None else max(intake.llm_call_count, llm_call_count)
        intake = intake.model_copy(update={
            "paper": analysis.paper,
            "paper_document": analysis.paper_document,
            "paper_catalog": analysis.paper_catalog,
            "repository_catalog": analysis.repository_catalog,
            "alignment_catalog": analysis.alignment_catalog,
            "goal_resolution": analysis.goal_resolution,
            "llm_call_count": persisted,
            "updated_at": datetime.now(timezone.utc),
        })
        session = self._upsert_session(intake, analysis)
        intake = intake.model_copy(update={"session_id": session.session_id, "updated_at": datetime.now(timezone.utc)})
        self.persistence.intakes.update(intake)
        if hasattr(self.persistence.events, "bind_session"):
            self.persistence.events.bind_session(intake.intake_id, session.session_id)
        return self._continue_after_goal(intake, session=session)

    def fail_analysis(
        self, intake_id: str, *, error_code: str, error_message: str,
        failed_phase: IntakeAnalysisPhase | None = None, llm_call_count: int | None = None,
    ) -> ReproductionIntake:
        intake = self.persistence.intakes.get(intake_id)
        if intake.state in {ReproductionIntakeState.FAILED, ReproductionIntakeState.TERMINAL, ReproductionIntakeState.READY_TO_RUN}:
            return intake
        phase = failed_phase or intake.current_phase or IntakeAnalysisPhase.FAILED
        persisted = intake.llm_call_count if llm_call_count is None else max(intake.llm_call_count, llm_call_count)
        intake = intake.model_copy(update={
            "state": ReproductionIntakeState.FAILED,
            "current_phase": IntakeAnalysisPhase.FAILED,
            "error_code": error_code,
            "error_message": error_message,
            "failed_phase": phase,
            "waiting_reason": error_message,
            "llm_call_count": persisted,
            "updated_at": datetime.now(timezone.utc),
        })
        self.persistence.intakes.update(intake)
        self._event(intake, ReproductionEventType.ANALYSIS_FAILED, {
            "error_code": error_code,
            "error_message": error_message,
            "failed_phase": phase.value,
        })
        return intake

    def clarify(self, intake_id: str, *, principal: str, answers: tuple[str, ...]):
        intake = self._owned_intake(intake_id, principal)
        if intake.state is not ReproductionIntakeState.AMBIGUOUS:
            raise InvalidIntakeStateError("intake is not waiting for clarification")
        if self.analysis_queue is None or self.paper_artifacts is None:
            raise APIUseCaseError("production persistence omitted the intake analysis queue")
        now = datetime.now(timezone.utc)
        enriched = intake.user_goal + "\nUser clarification:\n" + "\n".join(answers)
        intake = intake.model_copy(update={
            "user_goal": enriched,
            "clarification_answers": (*intake.clarification_answers, *answers),
            "state": ReproductionIntakeState.ANALYZING,
            "current_phase": IntakeAnalysisPhase.GOAL_RESOLVING,
            "waiting_reason": None,
            "error_code": None,
            "error_message": None,
            "failed_phase": None,
            "updated_at": now,
        })
        self.persistence.intakes.update(intake)
        artifact_uri = self.paper_artifacts.uri_for(intake.intake_id)
        self.analysis_queue.enqueue_for_clarification(
            IntakeAnalysisJob(
                job_id=f"analysis:{intake.intake_id}",
                intake_id=intake.intake_id,
                owner_principal=intake.owner_principal,
                status=AnalysisJobStatus.QUEUED,
                paper_artifact_uri=artifact_uri,
                max_attempts=self.analysis_settings.max_job_attempts,
                enqueued_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        return intake

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
            self._sync_session_from_intake(intake)
            return intake
        self._event(intake, ReproductionEventType.RESOURCE_RESOLVED, {"requirement_id": requirement_id})
        return self._plan_and_prepare_job(intake, session=self._session_for_intake(intake))

    def start(self, intake_id: str, *, principal: str):
        intake = self._owned_intake(intake_id, principal)
        if intake.state is not ReproductionIntakeState.READY_TO_RUN or intake.job_id is None:
            raise InvalidIntakeStateError("intake is not ready to run")
        return self._enqueue_job(intake.job_id, intake=intake, session=self._session_for_intake(intake))

    def list_intakes(self, *, principal: str):
        return self.persistence.intakes.list_by_owner(principal)

    def intake_events(self, intake_id: str, *, principal: str, after_sequence: int = 0):
        self._owned_intake(intake_id, principal)
        return self.persistence.events.list_by_intake(intake_id, after_sequence=after_sequence)

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
        return tuple(
            item.result
            for item in self.persistence.final_results.list_by_job(job_id)
            if item.validation_status is ResultValidationStatus.VALID
        )

    def comparison(self, job_id: str, *, principal: str):
        self.get_job(job_id, principal=principal)
        reports = self.persistence.comparisons.list_by_job(job_id)
        if not reports:
            raise EntityNotFoundError("comparison report not found")
        return reports[-1].report

    def events(self, job_id: str, *, principal: str, after_sequence: int = 0):
        self.get_job(job_id, principal=principal)
        return self.persistence.events.list_by_job(job_id, after_sequence=after_sequence)

    def get_session(self, session_id: str, *, principal: str):
        session = self._owned_session(session_id, principal)
        jobs = self._jobs_for_session(session.session_id)
        events = ()
        if hasattr(self.persistence.events, "list_by_session"):
            events = self.persistence.events.list_by_session(session.session_id)
        return session, jobs, project_session_experiments(session.paper_catalog, jobs), events

    def list_sessions(self, *, principal: str):
        if not hasattr(self.persistence, "sessions"):
            return ()
        return self.persistence.sessions.list_by_owner(principal)

    def append_experiments(
        self, session_id: str, *, principal: str,
        goal: str | None = None, experiment_ids: tuple[str, ...] | None = None,
    ):
        session = self._owned_session(session_id, principal)
        if not goal and not experiment_ids:
            raise InvalidSessionStateError("append requires a goal or explicit experiment ids")
        if session.repository_catalog is None:
            raise InvalidSessionStateError("session cannot append experiments before repository analysis")
        if session.pending_job_id is not None:
            pending = self.persistence.jobs.get(session.pending_job_id)
            if pending.status is ReproductionJobStatus.READY and self._same_append_request(pending, goal, experiment_ids):
                return session, pending
        goal_text = goal or f"Reproduce {', '.join(experiment_ids or ())}"
        if experiment_ids:
            resolution = self.pipeline.resolve_experiment_ids(
                catalog=session.paper_catalog,
                goal=UserReproductionGoal(goal_id=f"goal:{session.session_id}:{uuid.uuid4().hex[:10]}", text=goal_text),
                experiment_ids=experiment_ids,
            )
        else:
            resolution = self.pipeline.resolve_goal(
                catalog=session.paper_catalog,
                goal=UserReproductionGoal(goal_id=f"goal:{session.session_id}:{uuid.uuid4().hex[:10]}", text=goal_text),
            )
        resolution = self._apply_remaining_filter(session, resolution, goal_text)
        session = session.model_copy(update={
            "pending_goal": goal_text,
            "pending_goal_resolution": resolution,
            "pending_clarification_answers": (),
            "pending_resource_resolution": None,
            "pending_execution_plan": None,
            "pending_job_id": None,
            "updated_at": datetime.now(timezone.utc),
        })
        return self._continue_session_after_goal(session, enqueue=True)

    def clarify_session(self, session_id: str, *, principal: str, answers: tuple[str, ...]):
        session = self._owned_session(session_id, principal)
        if session.status is not ReproductionSessionStatus.AWAITING_CLARIFICATION or session.pending_goal is None:
            origin = self._owned_intake(session.origin_intake_id, principal)
            if origin.state is ReproductionIntakeState.AMBIGUOUS:
                intake = self.clarify(origin.intake_id, principal=principal, answers=answers)
                return self._session_for_intake(intake), None
            raise InvalidSessionStateError("session is not waiting for clarification")
        enriched = session.pending_goal + "\nUser clarification:\n" + "\n".join(answers)
        resolution = self.pipeline.resolve_goal(
            catalog=session.paper_catalog,
            goal=UserReproductionGoal(goal_id=f"goal:{session.session_id}:{uuid.uuid4().hex[:10]}", text=enriched),
        )
        resolution = self._apply_remaining_filter(session, resolution, enriched)
        session = session.model_copy(update={
            "pending_goal": enriched,
            "pending_goal_resolution": resolution,
            "pending_clarification_answers": (*session.pending_clarification_answers, *answers),
            "pending_resource_resolution": None,
            "pending_execution_plan": None,
            "updated_at": datetime.now(timezone.utc),
        })
        return self._continue_session_after_goal(session, enqueue=True)

    def submit_session_resource(self, session_id: str, *, principal: str, requirement_id: str, host_path: str):
        session = self._owned_session(session_id, principal)
        if session.status is not ReproductionSessionStatus.WAITING_FOR_RESOURCE or session.pending_resource_resolution is None:
            origin = self._owned_intake(session.origin_intake_id, principal)
            if origin.state is ReproductionIntakeState.WAITING_FOR_RESOURCE:
                intake = self.submit_resource(
                    origin.intake_id, principal=principal,
                    requirement_id=requirement_id, host_path=host_path,
                )
                return self._session_for_intake(intake), None
            raise InvalidSessionStateError("session is not waiting for an external resource")
        report = self.resource_service.register_user_path_and_resume(
            session.pending_resource_resolution, requirement_id=requirement_id,
            host_path=host_path, principal=principal,
            repository_catalog=session.repository_catalog,
        )
        session = session.model_copy(update={
            "pending_resource_resolution": report,
            "updated_at": datetime.now(timezone.utc),
        })
        intake = self._owned_intake(session.origin_intake_id, principal)
        if not report.ready_to_run:
            session = session.model_copy(update={"status": ReproductionSessionStatus.WAITING_FOR_RESOURCE})
            self.persistence.sessions.update(session)
            return session, None
        self._event(intake, ReproductionEventType.RESOURCE_RESOLVED, {"requirement_id": requirement_id}, session_id=session.session_id)
        return self._plan_session_job(session, enqueue=True)

    def start_session(self, session_id: str, *, principal: str):
        session = self._owned_session(session_id, principal)
        if session.pending_job_id is None:
            origin = self._owned_intake(session.origin_intake_id, principal)
            if origin.job_id is not None and origin.state is ReproductionIntakeState.READY_TO_RUN:
                return self.start(origin.intake_id, principal=principal)
            raise InvalidSessionStateError("session has no job ready to run")
        intake = self._owned_intake(session.origin_intake_id, principal)
        return self._enqueue_job(session.pending_job_id, intake=intake, session=session)

    def session_events(self, session_id: str, *, principal: str, after_sequence: int = 0):
        self._owned_session(session_id, principal)
        if hasattr(self.persistence.events, "list_by_session"):
            return self.persistence.events.list_by_session(session_id, after_sequence=after_sequence)
        return ()

    def _continue_after_goal(self, intake: ReproductionIntake, *, session: ReproductionSession | None = None):
        resolution = intake.goal_resolution
        if resolution is None:
            raise APIUseCaseError("analysis omitted goal resolution")
        if resolution.status is GoalResolutionStatus.NOT_FOUND:
            return self.fail_analysis(
                intake.intake_id,
                error_code=GOAL_NOT_FOUND,
                error_message=resolution.reason or "requested experiments were not found",
                failed_phase=IntakeAnalysisPhase.GOAL_RESOLVING,
                llm_call_count=intake.llm_call_count,
            )
        if resolution.status is not GoalResolutionStatus.RESOLVED:
            intake = intake.model_copy(update={
                "state": ReproductionIntakeState.AMBIGUOUS,
                "current_phase": IntakeAnalysisPhase.WAITING_FOR_CLARIFICATION,
                "waiting_reason": resolution.reason or "clarification is required",
                "updated_at": datetime.now(timezone.utc),
            })
            self.persistence.intakes.update(intake)
            if session is not None:
                self._set_session_pending(
                    session, intake,
                    status=ReproductionSessionStatus.AWAITING_CLARIFICATION,
                )
            self._event(intake, ReproductionEventType.CLARIFICATION_REQUIRED, {
                "candidate_experiment_ids": list(resolution.candidate_experiment_ids),
                "questions": list(resolution.clarification_questions),
            })
            return intake

        self._event(intake, ReproductionEventType.EXPERIMENT_SELECTION_RESOLVED, {
            "selected_experiment_ids": list(resolution.selection.selected_experiment_ids),
        })
        if intake.repository_catalog is None or intake.alignment_catalog is None:
            raise APIUseCaseError("resolved analysis omitted repository or alignment catalogs")
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
            if session is not None:
                self._set_session_pending(
                    session, intake,
                    status=ReproductionSessionStatus.WAITING_FOR_RESOURCE,
                )
            for item in report.resolutions:
                if item.binding is None:
                    self._event(intake, ReproductionEventType.RESOURCE_REQUIRED, {
                        "requirement_id": item.requirement.requirement_id,
                        "resource_name": item.requirement.canonical_name,
                        "resource_type": item.requirement.resource_type.value,
                    })
            return intake
        return self._plan_and_prepare_job(intake, session=session)

    def _plan_and_prepare_job(self, intake: ReproductionIntake, *, session: ReproductionSession | None = None, enqueue: bool = False):
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
        intake = intake.model_copy(update={
            "current_phase": IntakeAnalysisPhase.PREPARING,
            "updated_at": datetime.now(timezone.utc),
        })
        self.persistence.intakes.update(intake)
        plan = self.pipeline.plan(
            intake=intake,
            specification=intake.goal_resolution.specification,
            paper_catalog=intake.paper_catalog,
            repository_catalog=intake.repository_catalog,
            alignment_catalog=intake.alignment_catalog,
        )
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
            if session is not None:
                self._set_session_pending(
                    session, intake,
                    status=ReproductionSessionStatus.AWAITING_CLARIFICATION,
                )
            return intake

        job_id = intake.job_id or f"job:{uuid.uuid4().hex}"
        selection = intake.goal_resolution.selection
        created = intake.job_id is None
        job = ReproductionJob(
            job_id=job_id, owner_principal=intake.owner_principal,
            session_id=intake.session_id, paper=intake.paper,
            user_goal=intake.user_goal, selection=selection,
            status=ReproductionJobStatus.READY,
        )
        if created:
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
            "current_phase": IntakeAnalysisPhase.READY_TO_RUN,
            "updated_at": datetime.now(timezone.utc),
        })
        self.persistence.intakes.update(intake)
        if session is not None:
            session = self._set_session_pending(
                session, intake,
                status=ReproductionSessionStatus.ACTIVE, pending_job_id=job_id,
            )
        if enqueue:
            self._enqueue_job(job_id, intake=intake, session=session)
        return intake

    def _continue_session_after_goal(self, session: ReproductionSession, *, enqueue: bool):
        intake = self._owned_intake(session.origin_intake_id, session.owner_principal)
        resolution = session.pending_goal_resolution
        if resolution is None:
            raise APIUseCaseError("session omitted goal resolution")
        if resolution.status is not GoalResolutionStatus.RESOLVED:
            session = session.model_copy(update={
                "status": ReproductionSessionStatus.AWAITING_CLARIFICATION,
                "updated_at": datetime.now(timezone.utc),
            })
            self.persistence.sessions.update(session)
            self._event(intake, ReproductionEventType.CLARIFICATION_REQUIRED, {
                "candidate_experiment_ids": list(resolution.candidate_experiment_ids),
                "questions": list(resolution.clarification_questions),
            }, session_id=session.session_id)
            return session, None

        self._event(intake, ReproductionEventType.EXPERIMENT_SELECTION_RESOLVED, {
            "selected_experiment_ids": list(resolution.selection.selected_experiment_ids),
        }, session_id=session.session_id)
        report = self.resource_service.resolve(
            intake_id=intake.intake_id, principal=session.owner_principal,
            selection=resolution.selection, specification=resolution.specification,
            paper_catalog=session.paper_catalog, repository_catalog=session.repository_catalog,
        )
        session = session.model_copy(update={"pending_resource_resolution": report})
        if not report.ready_to_run:
            session = session.model_copy(update={
                "status": ReproductionSessionStatus.WAITING_FOR_RESOURCE,
                "updated_at": datetime.now(timezone.utc),
            })
            self.persistence.sessions.update(session)
            for item in report.resolutions:
                if item.binding is None:
                    self._event(intake, ReproductionEventType.RESOURCE_REQUIRED, {
                        "requirement_id": item.requirement.requirement_id,
                        "resource_name": item.requirement.canonical_name,
                        "resource_type": item.requirement.resource_type.value,
                    }, session_id=session.session_id)
            return session, None
        return self._plan_session_job(session, enqueue=enqueue)

    def _plan_session_job(self, session: ReproductionSession, *, enqueue: bool):
        resolution = session.pending_goal_resolution
        if (
            resolution is None
            or resolution.status is not GoalResolutionStatus.RESOLVED
            or resolution.selection is None
            or not resolution.selection.selected_experiment_ids
        ):
            raise InvalidSessionStateError("planning requires a resolved, locked experiment selection")
        if session.pending_resource_resolution is None or not session.pending_resource_resolution.ready_to_run:
            raise InvalidSessionStateError("planning requires all external resources to be available")
        intake = self._owned_intake(session.origin_intake_id, session.owner_principal)
        self._event(intake, ReproductionEventType.PLANNING_STARTED, {}, session_id=session.session_id)
        plan = self.pipeline.plan(
            specification=resolution.specification,
            paper_catalog=session.paper_catalog,
            repository_catalog=session.repository_catalog,
            alignment_catalog=session.alignment_catalog,
        )
        self._event(intake, ReproductionEventType.PLANNING_COMPLETED, {
            "plan_id": plan.plan_id, "status": plan.status.value,
            "blocker_codes": [item.code for item in plan.blockers],
        }, session_id=session.session_id)
        if plan.status is not PlanStatus.READY or plan.blockers:
            session = session.model_copy(update={
                "pending_execution_plan": plan,
                "status": ReproductionSessionStatus.AWAITING_CLARIFICATION,
                "updated_at": datetime.now(timezone.utc),
            })
            self.persistence.sessions.update(session)
            return session, None

        job_id = f"job:{uuid.uuid4().hex}"
        job = ReproductionJob(
            job_id=job_id, owner_principal=session.owner_principal,
            session_id=session.session_id, paper=session.paper,
            user_goal=resolution.selection.original_user_goal,
            selection=resolution.selection,
            status=ReproductionJobStatus.READY,
        )
        self.persistence.jobs.create(job)
        self.persistence.planning_snapshots.create(AuthoritativePlanningSnapshot(
            snapshot_id=f"planning-snapshot:{uuid.uuid4().hex}", job_id=job_id,
            specification=resolution.specification, execution_plan=plan,
        ))
        session = session.model_copy(update={
            "pending_execution_plan": plan,
            "pending_job_id": job_id,
            "status": ReproductionSessionStatus.ACTIVE,
            "updated_at": datetime.now(timezone.utc),
        })
        self.persistence.sessions.update(session)
        if enqueue:
            job = self._enqueue_job(job_id, intake=intake, session=session)
        return session, job

    def _enqueue_job(self, job_id: str, *, intake: ReproductionIntake, session: ReproductionSession | None):
        job = self.persistence.queue.enqueue(job_id)
        if intake.job_id == job_id:
            intake = intake.model_copy(update={
                "state": ReproductionIntakeState.QUEUED,
                "updated_at": datetime.now(timezone.utc),
            })
            self.persistence.intakes.update(intake)
        if session is not None:
            updates = {"status": ReproductionSessionStatus.ACTIVE, "updated_at": datetime.now(timezone.utc)}
            if session.pending_job_id == job_id:
                updates["pending_job_id"] = None
                updates["pending_goal"] = None
                updates["pending_goal_resolution"] = None
                updates["pending_resource_resolution"] = None
                updates["pending_execution_plan"] = None
            session = session.model_copy(update=updates)
            self.persistence.sessions.update(session)
        self._event(intake, ReproductionEventType.JOB_QUEUED, {"status": job.status.value}, job_id=job.job_id, session_id=None if session is None else session.session_id)
        return job

    def _upsert_session(self, intake: ReproductionIntake, analysis: IntakeAnalysis) -> ReproductionSession:
        existing = self._session_for_intake(intake)
        snapshot = analysis.repository_snapshot
        document = analysis.paper_document
        paper_hash = document.content_hash if document is not None else analysis.paper.id
        if existing is None:
            session = ReproductionSession(
                session_id=f"session:{uuid.uuid4().hex}",
                owner_principal=intake.owner_principal,
                origin_intake_id=intake.intake_id,
                source_filename=intake.source_filename,
                repository_url=intake.repository_url,
                paper=analysis.paper,
                paper_content_hash=paper_hash,
                paper_document=document,
                paper_catalog=analysis.paper_catalog,
                repository_catalog=analysis.repository_catalog,
                alignment_catalog=analysis.alignment_catalog,
                repository_snapshot_id=None if snapshot is None else snapshot.snapshot_id,
                repository_commit_sha=None if snapshot is None else snapshot.resolved_commit_sha,
                pending_goal=intake.user_goal,
                pending_goal_resolution=analysis.goal_resolution,
                status=(
                    ReproductionSessionStatus.AWAITING_CLARIFICATION
                    if analysis.goal_resolution.status is not GoalResolutionStatus.RESOLVED
                    else ReproductionSessionStatus.ACTIVE
                ),
            )
            if not hasattr(self.persistence, "sessions"):
                raise APIUseCaseError("production persistence omitted the reproduction session repository")
            self.persistence.sessions.create(session)
            return session
        updates = {
            "paper": analysis.paper,
            "paper_content_hash": paper_hash,
            "paper_document": document,
            "paper_catalog": analysis.paper_catalog,
            "pending_goal": intake.user_goal,
            "pending_goal_resolution": analysis.goal_resolution,
            "updated_at": datetime.now(timezone.utc),
        }
        if analysis.repository_catalog is not None:
            updates["repository_catalog"] = analysis.repository_catalog
        if analysis.alignment_catalog is not None:
            updates["alignment_catalog"] = analysis.alignment_catalog
        if snapshot is not None:
            updates["repository_snapshot_id"] = snapshot.snapshot_id
            updates["repository_commit_sha"] = snapshot.resolved_commit_sha
        session = existing.model_copy(update=updates)
        self.persistence.sessions.update(session)
        return session

    def _create_session(self, intake: ReproductionIntake, analysis: IntakeAnalysis) -> ReproductionSession:
        return self._upsert_session(intake, analysis)

    def _set_phase(self, intake_id: str, phase: IntakeAnalysisPhase) -> None:
        intake = self.persistence.intakes.get(intake_id)
        intake = intake.model_copy(update={
            "current_phase": phase, "updated_at": datetime.now(timezone.utc),
        })
        self.persistence.intakes.update(intake)

    def _checkpoint(self, intake_id: str, fields: dict) -> None:
        allowed = set(ReproductionIntake.model_fields)
        intake = self.persistence.intakes.get(intake_id)
        payload = {
            key: value for key, value in fields.items()
            if value is not None and key in allowed
        }
        payload["updated_at"] = datetime.now(timezone.utc)
        self.persistence.intakes.update(intake.model_copy(update=payload))

    def _register_snapshot(self, snapshot: RepositorySnapshot) -> None:
        registry = getattr(self.persistence, "repository_snapshots", None)
        if registry is None:
            raise APIUseCaseError("production persistence omitted the repository snapshot registry")
        registry.register(snapshot)

    def _load_repository_snapshot(self, snapshot_id: str) -> RepositorySnapshot | None:
        registry = getattr(self.persistence, "repository_snapshots", None)
        if registry is None or not hasattr(registry, "get"):
            return None
        try:
            return registry.get(snapshot_id)
        except PersistenceEntityNotFoundError:
            return None

    def _persist_llm_http_attempt(self, job: IntakeAnalysisJob) -> int:
        try:
            updated = self.analysis_queue.record_llm_http_attempt(
                job.job_id, job.worker_id, job.lease_token,
                max_phase_calls=self.analysis_settings.max_llm_calls,
            )
        except AnalysisJobLeaseLostError as exc:
            raise AnalysisLeaseLostError(str(exc)) from exc
        intake = self.persistence.intakes.get(job.intake_id)
        self.persistence.intakes.update(intake.model_copy(update={
            "llm_call_count": max(intake.llm_call_count, updated.lifetime_llm_call_count),
            "updated_at": datetime.now(timezone.utc),
        }))
        return updated.llm_call_count

    def _current_phase(self, intake_id: str) -> IntakeAnalysisPhase:
        return self.persistence.intakes.get(intake_id).current_phase

    def _set_session_pending(
        self, session: ReproductionSession, intake: ReproductionIntake, *,
        status: ReproductionSessionStatus, pending_job_id: str | None = None,
    ) -> ReproductionSession:
        session = session.model_copy(update={
            "status": status,
            "pending_goal": intake.user_goal,
            "pending_goal_resolution": intake.goal_resolution,
            "pending_resource_resolution": intake.resource_resolution,
            "pending_execution_plan": intake.execution_plan,
            "pending_clarification_answers": intake.clarification_answers,
            "pending_job_id": pending_job_id if pending_job_id is not None else session.pending_job_id,
            "updated_at": datetime.now(timezone.utc),
        })
        self.persistence.sessions.update(session)
        return session

    def _sync_session_from_intake(self, intake: ReproductionIntake) -> None:
        session = self._session_for_intake(intake)
        if session is None:
            return
        status = {
            ReproductionIntakeState.AMBIGUOUS: ReproductionSessionStatus.AWAITING_CLARIFICATION,
            ReproductionIntakeState.WAITING_FOR_RESOURCE: ReproductionSessionStatus.WAITING_FOR_RESOURCE,
        }.get(intake.state, ReproductionSessionStatus.ACTIVE)
        self._set_session_pending(session, intake, status=status)

    def _apply_remaining_filter(self, session: ReproductionSession, resolution: GoalResolutionResult, goal_text: str):
        if resolution.status is not GoalResolutionStatus.RESOLVED or resolution.selection is None:
            return resolution
        if not self._requests_remaining(goal_text):
            return resolution
        jobs = self._jobs_for_session(session.session_id)
        completed = completed_experiment_ids(jobs)
        remaining = tuple(
            item for item in resolution.selection.selected_experiment_ids if item not in completed
        )
        if remaining == resolution.selection.selected_experiment_ids:
            return resolution
        if not remaining:
            return GoalResolutionResult(
                status=GoalResolutionStatus.NOT_FOUND,
                selection=resolution.selection.model_copy(update={
                    "selected_experiment_ids": (),
                    "per_experiment_reasons": {},
                    "resolution_status": GoalResolutionStatus.NOT_FOUND,
                    "selection_reason": "会话中没有尚未完成的剩余实验",
                    "unresolved_mentions": ("剩余实验",),
                }),
                reason="会话中没有尚未完成的剩余实验",
            )
        return self.pipeline.resolve_experiment_ids(
            catalog=session.paper_catalog,
            goal=UserReproductionGoal(goal_id=f"goal:{session.session_id}:remaining", text=goal_text),
            experiment_ids=remaining,
        )

    @staticmethod
    def _requests_remaining(goal_text: str) -> bool:
        compact = "".join(goal_text.casefold().split())
        return any("".join(phrase.casefold().split()) in compact for phrase in _REMAINING_PHRASES)

    @staticmethod
    def _same_append_request(job: ReproductionJob, goal: str | None, experiment_ids: tuple[str, ...] | None) -> bool:
        if experiment_ids:
            return tuple(job.selection.selected_experiment_ids) == tuple(experiment_ids)
        return goal is not None and job.user_goal == goal

    def _owned_intake(self, intake_id: str, principal: str):
        try:
            intake = self.persistence.intakes.get(intake_id)
        except PersistenceEntityNotFoundError as exc:
            raise EntityNotFoundError("intake not found") from exc
        if intake.owner_principal != principal:
            raise EntityNotFoundError("intake not found")
        return intake

    def _owned_session(self, session_id: str, principal: str) -> ReproductionSession:
        if not hasattr(self.persistence, "sessions"):
            raise EntityNotFoundError("session not found")
        try:
            session = self.persistence.sessions.get(session_id)
        except PersistenceEntityNotFoundError as exc:
            raise EntityNotFoundError("session not found") from exc
        if session.owner_principal != principal:
            raise EntityNotFoundError("session not found")
        return session

    def _session_for_intake(self, intake: ReproductionIntake) -> ReproductionSession | None:
        if not hasattr(self.persistence, "sessions"):
            return None
        if intake.session_id:
            try:
                return self.persistence.sessions.get(intake.session_id)
            except PersistenceEntityNotFoundError:
                return None
        getter = getattr(self.persistence.sessions, "get_by_intake", None)
        if getter is None:
            return None
        return getter(intake.intake_id)

    def _jobs_for_session(self, session_id: str) -> tuple[ReproductionJob, ...]:
        if hasattr(self.persistence.jobs, "list_by_session"):
            return self.persistence.jobs.list_by_session(session_id)
        return tuple(job for job in self.persistence.jobs.list() if job.session_id == session_id)

    def _intake_for_job(self, job_id: str, principal: str):
        intake = next((item for item in self.persistence.intakes.list_by_owner(principal) if item.job_id == job_id), None)
        if intake is not None:
            return intake
        try:
            job = self.persistence.jobs.get(job_id)
        except PersistenceEntityNotFoundError as exc:
            raise EntityNotFoundError("reproduction not found") from exc
        if job.session_id:
            session = self._owned_session(job.session_id, principal)
            return self._owned_intake(session.origin_intake_id, principal)
        raise EntityNotFoundError("reproduction not found")

    def _event(self, intake, event_type, payload, *, job_id=None, session_id=None):
        kwargs = {
            "intake_id": intake.intake_id, "owner_principal": intake.owner_principal,
            "job_id": job_id or intake.job_id, "event_type": event_type, "payload": payload,
        }
        bound_session = session_id if session_id is not None else intake.session_id
        try:
            return self.persistence.events.append(session_id=bound_session, **kwargs)
        except TypeError:
            return self.persistence.events.append(**kwargs)
