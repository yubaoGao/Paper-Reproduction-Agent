"""Sandbox-private Python environment provisioning from structured requirements."""

from __future__ import annotations

from .models import EnvironmentReuseStrategy


class EnvironmentProvisioningError(RuntimeError):
    pass


class EnvironmentProvisioner:
    """Never executes repository installers, Dockerfiles, or host package tools."""

    def __init__(self, manager, *, python_program: str = "python") -> None:
        self.manager = manager
        self.python_program = python_program

    def provision(self, handle, plan, timeout_seconds: int = 1800) -> None:
        if plan.strategy is EnvironmentReuseStrategy.REUSED_IMAGE:
            return
        if plan.strategy is EnvironmentReuseStrategy.REUSED_READ_ONLY_ENV:
            return
        seed_directories = []
        for index, _cache_id in enumerate(plan.package_cache_source_ids):
            target = f"/cache/seed/{index}"
            hydrated = self.manager.exec(
                handle,
                program="cp",
                argv=("-a", f"/seed-cache/{index}/.", target),
                cwd="/cache",
                timeout_seconds=timeout_seconds,
            )
            if hydrated.timed_out or hydrated.exit_code != 0:
                raise EnvironmentProvisioningError(
                    "read-only package cache hydration failed"
                )
            seed_directories.append(target)
        if plan.resolved_system_packages:
            system = self.manager.exec(
                handle,
                program="/opt/paperrepro/bin/materialize-system-packages",
                argv=(
                    "--target",
                    "/sandbox-env/sysroot",
                    *(
                        value
                        for item in plan.resolved_system_packages
                        for value in ("--package", item)
                    ),
                ),
                cwd="/sandbox-env",
                timeout_seconds=timeout_seconds,
            )
            if system.timed_out or system.exit_code != 0:
                raise EnvironmentProvisioningError(
                    "trusted system package materialization failed"
                )
        created = self.manager.exec(
            handle,
            program=self.python_program,
            argv=("-m", "venv", "/sandbox-env/venv"),
            cwd="/sandbox-env",
            timeout_seconds=timeout_seconds,
        )
        if created.timed_out or created.exit_code != 0:
            raise EnvironmentProvisioningError("sandbox-private venv creation failed")
        if not plan.required_downloads:
            return
        offline = bool(seed_directories) and (
            plan.strategy is EnvironmentReuseStrategy.SEEDED_FROM_PACKAGE_CACHE
        )
        argv = (
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--cache-dir",
            "/cache/pip",
            *(("--no-index",) if offline else ()),
            *(value for target in seed_directories for value in ("--find-links", target)),
            *plan.required_downloads,
        )
        installed = self.manager.exec(
            handle,
            program="/sandbox-env/venv/bin/python",
            argv=argv,
            cwd="/sandbox-env",
            timeout_seconds=timeout_seconds,
        )
        if installed.timed_out or installed.exit_code != 0:
            raise EnvironmentProvisioningError(
                "dependency installation failed inside provisioning sandbox"
            )
