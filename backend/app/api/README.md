# ReproPilot production API

This package is the HTTP composition boundary. Routes depend on
`ReproductionAPIService` and a replaceable `PrincipalAuthenticator`; they do
not import SQLAlchemy models, Docker, sandbox, GPU scheduling, the planner, or
the comparator.

`ExistingServicesAnalysisPipeline` sequences the existing paper ingestion,
paper extraction, repository analysis, alignment, goal intake, and planning
services. `build_production_app` wires only PostgreSQL product repositories,
Task 15B resource resolution, and Task 14's durable queue. HTTP requests never
claim or execute jobs.

The temporary auth contract requires `X-ReproPilot-Principal`. It is designed
to be replaced by JWT/account authentication without changing ownership checks
in the application service.

Intake creation uses multipart fields `paper_pdf`, `repository_url`, and
`goal`. The default PDF request limit is 50 MiB. External resource host paths
are accepted only on the resource submission request and never appear in an
API projection or product event.

SSE at `/api/v1/reproductions/{job_id}/events` uses PostgreSQL event sequence
numbers as SSE ids. `Last-Event-ID` replays all later records before live
polling continues. Disconnecting a client only closes its database tail; it
does not interact with the worker or durable queue.
