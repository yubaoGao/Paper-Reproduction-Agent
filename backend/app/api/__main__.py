"""Run the production FastAPI process independently from workers."""

from __future__ import annotations

import os

import uvicorn

from .application import build_production_app


def main() -> None:
    host = os.environ.get("REPROPILOT_API_HOST", "127.0.0.1")
    port = int(os.environ.get("REPROPILOT_API_PORT", "8000"))
    uvicorn.run(build_production_app(), host=host, port=port)


if __name__ == "__main__":
    main()
