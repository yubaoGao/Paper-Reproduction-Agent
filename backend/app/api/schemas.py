"""Public API schemas deliberately excluding private host/runtime fields."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClarificationRequest(APIModel):
    answers: tuple[str, ...] = Field(min_length=1, max_length=32)


class ResourceSubmissionRequest(APIModel):
    requirement_id: str = Field(min_length=1, max_length=255)
    host_path: str = Field(min_length=1, max_length=4096)


class ResourceRequirementResponse(APIModel):
    requirement_id: str
    resource_name: str
    resource_type: str
    required: bool
    status: str
    preparation_hints: tuple[str, ...] = ()
    source_urls: tuple[str, ...] = ()
    expected_structure: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()


class IntakeResponse(APIModel):
    intake_id: str
    state: str
    goal: str
    repository_url: str
    candidate_experiment_ids: tuple[str, ...] = ()
    selected_experiment_ids: tuple[str, ...] = ()
    clarification_questions: tuple[str, ...] = ()
    required_resources: tuple[ResourceRequirementResponse, ...] = ()
    planning_status: str | None = None
    planning_blockers: tuple[dict[str, Any], ...] = ()
    waiting_reason: str | None = None
    job_id: str | None = None
    created_at: datetime
    updated_at: datetime


class JobSummaryResponse(APIModel):
    job_id: str
    goal: str
    selected_experiment_ids: tuple[str, ...]
    state: str
    current_action: str | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    waiting_reason: str | None = None
    required_resources: tuple[ResourceRequirementResponse, ...] = ()
    gpu_requirement: dict[str, Any] | None = None
    gpu_allocation: dict[str, Any] | None = None
    resource_adaptations: tuple[dict[str, Any], ...] = ()
    attempts: int = 0
    retries: int = 0
    terminal_failure: str | None = None
    created_at: datetime
    updated_at: datetime
    enqueued_at: datetime | None = None
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ErrorResponse(APIModel):
    code: str
    message: str
