# PaperReproAgent contributor instructions

## Boundaries

- `backend/app/curie_core` owns scientific experiment reasoning and experiment-internal orchestration only.
- `backend/app/runtime` owns runtime contracts. New platform code must not import `backend.app.runtime.legacy`.
- `InternalExperimentScheduler` is not the future platform GPU scheduler.
- Domain and service code must stay independent of Docker, LangGraph provider globals, and HTTP frameworks.
- Do not introduce HTTP handlers that execute experiments synchronously.

## Safety

- Treat repositories, datasets, generated scripts, model output, and artifacts as untrusted.
- Never add Docker socket, host-root, host-network, privileged, all-GPU, world-writable workspace, or plaintext-secret behavior to a production runtime.
- Do not restore third-party telemetry that uploads questions, configs, logs, code, datasets, or runtime metadata by default.

## Validation

- Run `python -m compileall backend` for backend structural changes.
- Run `pnpm run build` from `frontend/` for frontend changes.
- Linux Docker/GPU integration validation belongs to the deployment environment and must be run explicitly.
