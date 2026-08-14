# PaperReproAgent contributor instructions

## Boundaries

- `backend/app/curie_core` owns only the retained dependency-light scientific reasoning facets.
- `backend/app/runtime` owns provider-neutral runtime contracts and models; the legacy runtime has been removed.
- Domain and service code must stay independent of Docker, LangGraph provider globals, and HTTP frameworks.
- Do not introduce HTTP handlers that execute experiments synchronously.

## Safety

- Treat repositories, datasets, generated scripts, model output, and artifacts as untrusted.
- Never add Docker socket, host-root, host-network, privileged, all-GPU, world-writable workspace, or plaintext-secret behavior to a production runtime.
- Do not restore third-party telemetry that uploads questions, configs, logs, code, datasets, or runtime metadata by default.

## Validation

- Run `python -m compileall backend` for local structural changes.
- Run `pnpm run typecheck` and `pnpm run build` from `frontend/` for frontend changes.
