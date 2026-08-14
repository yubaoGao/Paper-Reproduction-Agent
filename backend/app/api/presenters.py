"""Path-safe public projections for product aggregates."""

from __future__ import annotations

from .schemas import (
    ExperimentJobHistoryResponse,
    IntakeResponse,
    JobSummaryResponse,
    ResourceRequirementResponse,
    SessionExperimentResponse,
    SessionResponse,
)


def _resource_responses(resolution) -> tuple[ResourceRequirementResponse, ...]:
    if resolution is None:
        return ()
    return tuple(
        ResourceRequirementResponse(
            requirement_id=item.requirement.requirement_id,
            resource_name=item.requirement.canonical_name,
            resource_type=item.requirement.resource_type.value,
            required=item.requirement.required,
            status=item.status.value,
            preparation_hints=() if item.preparation_hint is None else item.preparation_hint.repository_instructions,
            source_urls=() if item.preparation_hint is None else item.preparation_hint.source_urls,
            expected_structure=item.requirement.expected_structure,
            messages=item.messages,
        )
        for item in resolution.resolutions
    )


def present_intake(intake):
    goal = intake.goal_resolution
    selection = None if goal is None else goal.selection
    return IntakeResponse(
        intake_id=intake.intake_id, session_id=intake.session_id,
        state=intake.state.value, goal=intake.user_goal,
        repository_url=intake.repository_url,
        candidate_experiment_ids=() if goal is None else goal.candidate_experiment_ids,
        selected_experiment_ids=() if selection is None else selection.selected_experiment_ids,
        clarification_questions=() if goal is None else goal.clarification_questions,
        required_resources=_resource_responses(intake.resource_resolution),
        planning_status=None if intake.execution_plan is None else intake.execution_plan.status.value,
        planning_blockers=() if intake.execution_plan is None else tuple({
            "code": item.code, "message": item.message,
            "paper_experiment_id": item.paper_experiment_id,
        } for item in intake.execution_plan.blockers),
        waiting_reason=intake.waiting_reason, job_id=intake.job_id,
        created_at=intake.created_at, updated_at=intake.updated_at,
    )


def present_job(job, *, runs=(), intake=None, events=()):
    latest = runs[-1] if runs else None
    steps = () if latest is None else latest.steps
    attempts = sum(len(step.attempts) for step in steps)
    retries = sum(max(0, len(step.attempts) - 1) for step in steps)
    adaptations = tuple(
        item.model_dump(mode="json")
        for step in steps for attempt in step.attempts for item in attempt.resource_adaptations
    )
    done = sum(step.status.value in {"succeeded", "failed", "blocked", "cancelled"} for step in steps)
    current = next((step.step_id for step in steps if step.status.value in {"preparing", "running", "validating", "patching", "retrying"}), None)
    latest_action = events[-1].event_type.value if events else None
    gpu_waiting = next((item for item in reversed(events) if item.event_type.value == "GPU_WAITING"), None)
    gpu_allocated = next((item for item in reversed(events) if item.event_type.value == "GPU_ALLOCATED"), None)
    event_adaptations = tuple(item.payload for item in events if item.event_type.value == "RESOURCE_ADAPTED")
    event_retries = sum(item.event_type.value == "STEP_RETRYING" for item in events)
    requirements = () if intake is None or intake.execution_plan is None else tuple(
        item.resource_requirement.model_dump(mode="json") for item in intake.execution_plan.experiments
    )
    resource_summaries = _resource_responses(None if intake is None else intake.resource_resolution)
    epoch = next((item for item in reversed(events) if item.event_type.value == "EPOCH_PROGRESS"), None)
    progress = {"completed_steps": done, "total_steps": len(steps)}
    if epoch is not None:
        progress.update(epoch.payload)
    return JobSummaryResponse(
        job_id=job.job_id, session_id=job.session_id, goal=job.user_goal,
        selected_experiment_ids=job.selection.selected_experiment_ids,
        state=job.status.value, current_action=current or latest_action,
        progress=progress,
        waiting_reason=(gpu_waiting.payload.get("reason") if gpu_waiting else None) or (None if intake is None else intake.waiting_reason),
        required_resources=resource_summaries,
        gpu_requirement={"experiments": requirements} if requirements else None,
        gpu_allocation=None if gpu_allocated is None else gpu_allocated.payload,
        resource_adaptations=(*adaptations, *event_adaptations), attempts=attempts, retries=retries + event_retries,
        terminal_failure=job.last_error,
        created_at=job.created_at, updated_at=job.updated_at,
        enqueued_at=job.enqueued_at, claimed_at=job.claimed_at,
        started_at=None if latest is None else latest.started_at,
        finished_at=None if latest is None else latest.finished_at,
    )


def present_session(session, *, jobs=(), experiments=(), events=()):
    goal = session.pending_goal_resolution
    selection = None if goal is None else goal.selection
    plan = session.pending_execution_plan
    job_summaries = tuple(present_job(job) for job in jobs)
    return SessionResponse(
        session_id=session.session_id, status=session.status.value,
        origin_intake_id=session.origin_intake_id,
        repository_url=session.repository_url,
        repository_snapshot_id=session.repository_snapshot_id,
        repository_commit_sha=session.repository_commit_sha,
        paper_content_hash=session.paper_content_hash,
        source_filename=session.source_filename,
        goal=session.pending_goal,
        candidate_experiment_ids=() if goal is None else goal.candidate_experiment_ids,
        selected_experiment_ids=() if selection is None else selection.selected_experiment_ids,
        clarification_questions=() if goal is None else goal.clarification_questions,
        required_resources=_resource_responses(session.pending_resource_resolution),
        planning_status=None if plan is None else plan.status.value,
        planning_blockers=() if plan is None else tuple({
            "code": item.code, "message": item.message,
            "paper_experiment_id": item.paper_experiment_id,
        } for item in plan.blockers),
        pending_job_id=session.pending_job_id,
        waiting_reason=None if goal is None else goal.reason,
        experiments=tuple(
            SessionExperimentResponse(
                experiment_id=item.experiment_id, name=item.name,
                experiment_type=item.experiment_type, status=item.status.value,
                current_job_id=item.current_job_id,
                job_history=tuple(
                    ExperimentJobHistoryResponse(
                        job_id=history.job_id, goal=history.goal,
                        status=history.status.value,
                        created_at=history.created_at, updated_at=history.updated_at,
                    )
                    for history in item.job_history
                ),
            )
            for item in experiments
        ),
        jobs=job_summaries,
        created_at=session.created_at, updated_at=session.updated_at,
    )
