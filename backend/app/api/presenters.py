"""Path-safe public projections for product aggregates."""

from __future__ import annotations

from backend.app.domain import ReproductionJobStatus

from .schemas import IntakeResponse, JobSummaryResponse, ResourceRequirementResponse


def present_intake(intake):
    goal = intake.goal_resolution
    selection = None if goal is None else goal.selection
    resources = ()
    if intake.resource_resolution is not None:
        resources = tuple(
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
            for item in intake.resource_resolution.resolutions
        )
    return IntakeResponse(
        intake_id=intake.intake_id, state=intake.state.value, goal=intake.user_goal,
        repository_url=intake.repository_url,
        candidate_experiment_ids=() if goal is None else goal.candidate_experiment_ids,
        selected_experiment_ids=() if selection is None else selection.selected_experiment_ids,
        clarification_questions=() if goal is None else goal.clarification_questions,
        required_resources=resources,
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
    resource_summaries = ()
    if intake is not None and intake.resource_resolution is not None:
        resource_summaries = tuple(
            ResourceRequirementResponse(
                requirement_id=item.requirement.requirement_id,
                resource_name=item.requirement.canonical_name,
                resource_type=item.requirement.resource_type.value,
                required=item.requirement.required, status=item.status.value,
                expected_structure=item.requirement.expected_structure,
                messages=item.messages,
            ) for item in intake.resource_resolution.resolutions
        )
    epoch = next((item for item in reversed(events) if item.event_type.value == "EPOCH_PROGRESS"), None)
    progress = {"completed_steps": done, "total_steps": len(steps)}
    if epoch is not None:
        progress.update(epoch.payload)
    return JobSummaryResponse(
        job_id=job.job_id, goal=job.user_goal,
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
