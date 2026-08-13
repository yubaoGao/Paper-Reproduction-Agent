# PostgreSQL persistence

This package contains the production SQLAlchemy adapters for reproduction state.
Domain and service code depend only on repository protocols and immutable domain
models; SQLAlchemy and PostgreSQL types stay in this infrastructure package.

Apply schema migrations explicitly:

```powershell
$env:REPROPILOT_DATABASE_URL = 'postgresql+psycopg://user:password@host/database'
python -m alembic -c alembic.ini upgrade head
```

Production startup must not call `metadata.create_all()`. Artifact rows contain
references and metadata only; artifact file bytes remain in external storage.

Real integration tests require an isolated PostgreSQL database:

```powershell
$env:REPROPILOT_TEST_POSTGRES_DSN = 'postgresql+psycopg://user:password@host/test_database'
python -m unittest tests.integration.test_postgres_persistence -v
```

The integration test creates and removes one uniquely named schema.

## Durable queue

`PostgresDurableJobQueue` claims FIFO jobs with `FOR UPDATE SKIP LOCKED`.
Every owned mutation requires both `worker_id` and a unique `lease_token`.
Workers heartbeat before lease expiry; expired `CLAIMED`/`RUNNING` jobs are
atomically returned to `QUEUED`, while cancellation requests remain durable.

The application worker lives in `backend.app.orchestration.worker` and delegates
execution/resume to `ReproductionOrchestrator`; it does not invoke Docker or a
runtime directly.
