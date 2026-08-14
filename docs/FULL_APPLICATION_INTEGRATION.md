# Full application composition

Task 18 connects the existing ReproPilot modules without adding a second
execution path. The production topology is:

```text
FastAPI -> application services -> PostgreSQL queue
                                     |
independent Worker <- authoritative state + claim
       -> NVIDIA GPU scheduler -> exact AssignedDeviceSet
       -> Task 11 orchestrator -> Task 10 Docker sandbox
       -> explicit repository ResultAdapter -> FinalResult validation
       -> deterministic Comparison -> terminal job + PostgreSQL ProductEvents
       -> FastAPI SSE replay (Last-Event-ID) -> React
```

Run migrations before starting either process:

```powershell
alembic upgrade head
python -m backend.app.api
python -m backend.app.worker
```

The API and worker share no Python objects. Both reconstruct state from the
database, while the worker alone owns GPU and sandbox adapters. A production
worker requires a digest-pinned base image, an explicit quota-capable Docker
volume driver, live NVIDIA inventory, and `REPROPILOT_DATA_ROOT` pointing at an
existing data directory whose descendants may be registered as HOST_PATH mounts.
It never requests all GPUs.

`REPROPILOT_DATA_ROOT` is the sandbox host-bind allowlist, for example
`/home/gyb/ReproPilotData`. It is not the application checkout
(`/home/gyb/ReproPilot`) and must not be `/`, `/home`, or `/etc`.

`REPROPILOT_PRINCIPAL_RESOURCE_ROOTS_JSON` must list each principal's approved
dataset/config roots when local resources can be submitted. The API persists
only validated bindings, and the worker applies the same allowlist; it never
downloads missing datasets automatically.

Result interpretation fails closed. `build_production_worker(result_adapters=...)`
must receive adapters keyed by exact repository ID; an unknown repository is
not treated as supported. Reference adapters are intentionally unavailable to
production composition.

The deterministic integration suite uses test persistence and a test GPU
provider, as permitted for composition tests. It covers happy path, ambiguous
clarification, missing resource resume, GPU wait/allocation, OOM adaptation,
active cancellation cleanup, restart/recovery, and ordered SSE replay.
