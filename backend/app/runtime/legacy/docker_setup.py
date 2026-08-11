"""Non-mutating Docker prerequisite check for the legacy Curie runtime.

The original project attempted to install and start Docker automatically. A
platform worker must never mutate its host this way, so Task 01 retains only a
read-only availability check. The legacy runtime remains unsupported on the
current Windows development machine.
"""

from __future__ import annotations

import shutil
import subprocess


def require_docker_available() -> None:
    """Raise a clear error when a caller explicitly selects LEGACY_RUNTIME."""

    if shutil.which("docker") is None:
        raise RuntimeError(
            "LEGACY_RUNTIME requires a pre-installed Docker CLI and daemon. "
            "PaperReproAgent does not install Docker automatically."
        )

    result = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "LEGACY_RUNTIME found Docker but could not reach its daemon."
        )
