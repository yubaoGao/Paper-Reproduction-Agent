"""Versioned HTTP routes. No persistence, Docker, or GPU adapters are imported here."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import StreamingResponse

from .auth import Principal
from .dependencies import get_api_service, get_principal
from .presenters import present_intake, present_job, present_session
from .schemas import (
    AppendExperimentsRequest, ClarificationRequest, IntakeResponse,
    JobSummaryResponse, ResourceSubmissionRequest, SessionResponse,
)
from .github_repository_url import GitHubRepositoryUrlError, normalize_github_repository_url
from .sse import stream_job_events


router = APIRouter(prefix="/api/v1/reproductions", tags=["reproductions"])


async def _read_bounded_upload(upload: UploadFile, maximum_bytes: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = await upload.read(min(1024 * 1024, maximum_bytes + 1 - total))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > maximum_bytes:
            from fastapi import HTTPException
            raise HTTPException(status_code=413, detail="paper PDF exceeds the configured upload limit")
        chunks.append(chunk)


def _github_repository_url(value: str) -> str:
    try:
        return normalize_github_repository_url(value)
    except GitHubRepositoryUrlError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/intakes", response_model=IntakeResponse, status_code=201)
async def create_intake(
    request: Request,
    paper_pdf: UploadFile = File(...),
    repository_url: str = Form(..., min_length=1, max_length=2048),
    goal: str = Form(..., min_length=1, max_length=10_000),
    principal: Principal = Depends(get_principal),
    service=Depends(get_api_service),
):
    # Bounded upload ingestion remains delegated to Task 05 through the pipeline.
    repository_url = _github_repository_url(repository_url)
    content = await _read_bounded_upload(paper_pdf, request.app.state.max_paper_upload_bytes)
    return present_intake(service.create_intake(
        principal=principal.principal_id,
        source_filename=paper_pdf.filename or "paper.pdf",
        paper_pdf=content, repository_url=repository_url, goal=goal,
    ))


@router.get("/intakes/{intake_id}", response_model=IntakeResponse)
def get_intake(intake_id: str, principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    return present_intake(service.get_intake(intake_id, principal=principal.principal_id))


@router.post("/intakes/{intake_id}/clarifications", response_model=IntakeResponse)
def clarify_intake(intake_id: str, body: ClarificationRequest, principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    return present_intake(service.clarify(intake_id, principal=principal.principal_id, answers=body.answers))


@router.post("/intakes/{intake_id}/resources", response_model=IntakeResponse)
def submit_resource(intake_id: str, body: ResourceSubmissionRequest, principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    return present_intake(service.submit_resource(
        intake_id, principal=principal.principal_id,
        requirement_id=body.requirement_id, host_path=body.host_path,
    ))


@router.post("/intakes/{intake_id}/start", response_model=JobSummaryResponse, status_code=202)
def start_intake(intake_id: str, principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    job = service.start(intake_id, principal=principal.principal_id)
    return present_job(job)


@router.get("/sessions", response_model=tuple[SessionResponse, ...])
def list_sessions(principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    return tuple(
        present_session(item) for item in service.list_sessions(principal=principal.principal_id)
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    session, jobs, experiments, _events = service.get_session(session_id, principal=principal.principal_id)
    return present_session(session, jobs=jobs, experiments=experiments)


@router.post("/sessions/{session_id}/experiments", response_model=SessionResponse)
def append_session_experiments(
    session_id: str, body: AppendExperimentsRequest,
    principal: Principal = Depends(get_principal), service=Depends(get_api_service),
):
    session, _job = service.append_experiments(
        session_id, principal=principal.principal_id,
        goal=body.goal, experiment_ids=body.experiment_ids,
    )
    loaded, jobs, experiments, _events = service.get_session(session.session_id, principal=principal.principal_id)
    return present_session(loaded, jobs=jobs, experiments=experiments)


@router.post("/sessions/{session_id}/clarifications", response_model=SessionResponse)
def clarify_session(
    session_id: str, body: ClarificationRequest,
    principal: Principal = Depends(get_principal), service=Depends(get_api_service),
):
    session, _job = service.clarify_session(session_id, principal=principal.principal_id, answers=body.answers)
    loaded, jobs, experiments, _events = service.get_session(session.session_id, principal=principal.principal_id)
    return present_session(loaded, jobs=jobs, experiments=experiments)


@router.post("/sessions/{session_id}/resources", response_model=SessionResponse)
def submit_session_resource(
    session_id: str, body: ResourceSubmissionRequest,
    principal: Principal = Depends(get_principal), service=Depends(get_api_service),
):
    session, _job = service.submit_session_resource(
        session_id, principal=principal.principal_id,
        requirement_id=body.requirement_id, host_path=body.host_path,
    )
    loaded, jobs, experiments, _events = service.get_session(session.session_id, principal=principal.principal_id)
    return present_session(loaded, jobs=jobs, experiments=experiments)


@router.post("/sessions/{session_id}/start", response_model=JobSummaryResponse, status_code=202)
def start_session(session_id: str, principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    job = service.start_session(session_id, principal=principal.principal_id)
    return present_job(job)


@router.get("", response_model=tuple[JobSummaryResponse, ...])
def list_reproductions(principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    return tuple(present_job(job) for job in service.list_jobs(principal=principal.principal_id))


@router.get("/{job_id}", response_model=JobSummaryResponse)
def get_reproduction(job_id: str, principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    job, runs, intake, events = service.job_detail(job_id, principal=principal.principal_id)
    return present_job(job, runs=runs, intake=intake, events=events)


@router.post("/{job_id}/cancel", response_model=JobSummaryResponse, status_code=202)
def cancel_reproduction(job_id: str, principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    return present_job(service.cancel(job_id, principal=principal.principal_id))


@router.get("/{job_id}/results")
def get_results(job_id: str, principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    # Canonical FinalResult is returned without a metric-name whitelist.
    return [item.model_dump(mode="json") for item in service.results(job_id, principal=principal.principal_id)]


@router.get("/{job_id}/comparison")
def get_comparison(job_id: str, principal: Principal = Depends(get_principal), service=Depends(get_api_service)):
    return service.comparison(job_id, principal=principal.principal_id).model_dump(mode="json")


@router.get("/{job_id}/events")
def get_events(
    request: Request, job_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    principal: Principal = Depends(get_principal), service=Depends(get_api_service),
):
    try:
        after = max(0, int(last_event_id or "0"))
    except ValueError:
        after = 0
    # Authorize before beginning a streaming response.
    service.get_job(job_id, principal=principal.principal_id)
    return StreamingResponse(
        stream_job_events(request, service, job_id=job_id, principal=principal.principal_id, after_sequence=after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
