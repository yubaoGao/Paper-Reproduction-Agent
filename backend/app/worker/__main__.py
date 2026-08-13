from __future__ import annotations

import os

from .application import build_production_worker, run_worker_forever


def main() -> None:
    worker_id = os.environ.get("REPROPILOT_WORKER_ID")
    if not worker_id:
        raise RuntimeError("REPROPILOT_WORKER_ID is required")
    run_worker_forever(build_production_worker(worker_id=worker_id))


if __name__ == "__main__":
    main()
