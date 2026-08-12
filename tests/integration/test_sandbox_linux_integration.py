"""Opt-in destructive-attempt tests on an administrator-owned Linux fixture.

The fixture directory must contain ``repository/``, ``dataset/``, and
``environment/`` leaves. Tests only mount them read-only and verify their
content hashes before and after the sandbox run.
"""

from __future__ import annotations

import hashlib
import os
import platform
from pathlib import Path
import unittest
import uuid

from backend.app.infrastructure.sandbox import (
    DockerEngineBackend,
    EnvironmentReuseStrategy,
    HostMutationGuard,
    LinuxSandboxManager,
    MountCategory,
    RegisteredResource,
    ResourceKind,
    RunResourceRegistry,
    SandboxEnvironmentPlan,
    SandboxMount,
    SandboxSpec,
    TrustedResourceRegistry,
    environment_fingerprint,
)


ENABLED = (
    os.getenv("SANDBOX_LINUX_INTEGRATION") == "1"
    and platform.system() == "Linux"
    and bool(os.getenv("PAPERREPRO_SANDBOX_IMAGE_DIGEST"))
    and bool(os.getenv("PAPERREPRO_QUOTA_VOLUME_DRIVER"))
    and bool(os.getenv("PAPERREPRO_LINUX_SECURITY_FIXTURE"))
)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@unittest.skipUnless(
    ENABLED,
    "requires an opt-in Linux Docker host, digest image, quota driver, and registered fixture",
)
class SandboxLinuxIntegrationTests(unittest.TestCase):
    def test_host_mutation_attempts_and_container_hardening(self):
        fixture = Path(os.environ["PAPERREPRO_LINUX_SECURITY_FIXTURE"]).resolve(
            strict=True
        )
        leaves = {
            name: (fixture / name).resolve(strict=True)
            for name in ("repository", "dataset", "environment")
        }
        before = {name: _tree_digest(path) for name, path in leaves.items()}
        run_id = f"linux-security-{uuid.uuid4().hex}"
        trusted = TrustedResourceRegistry(
            (
                RegisteredResource(
                    resource_id="integration:repository",
                    kind=ResourceKind.HOST_PATH,
                    category=MountCategory.REPOSITORY_SNAPSHOT_READ_ONLY,
                    host_path=str(leaves["repository"]),
                ),
                RegisteredResource(
                    resource_id="integration:dataset",
                    kind=ResourceKind.HOST_PATH,
                    category=MountCategory.DATASET_READ_ONLY,
                    host_path=str(leaves["dataset"]),
                ),
                RegisteredResource(
                    resource_id="integration:environment",
                    kind=ResourceKind.HOST_PATH,
                    category=MountCategory.REGISTERED_ENV_READ_ONLY,
                    host_path=str(leaves["environment"]),
                ),
            )
        )
        runs = RunResourceRegistry()
        backend = DockerEngineBackend(
            quota_volume_driver=os.environ["PAPERREPRO_QUOTA_VOLUME_DRIVER"]
        )
        manager = LinuxSandboxManager(backend, HostMutationGuard(trusted), runs)
        workspace_volume = manager.create_run_volume(run_id, "workspace", 1024**3)
        workspace_resource = f"run:{run_id}:workspace"
        trusted.register(
            RegisteredResource(
                resource_id=workspace_resource,
                kind=ResourceKind.DOCKER_VOLUME,
                category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,
                volume_name=workspace_volume,
                owner_run_id=run_id,
            )
        )
        mounts = (
            SandboxMount(
                resource_id="integration:repository",
                target="/source/repository",
                category=MountCategory.REPOSITORY_SNAPSHOT_READ_ONLY,
                read_only=True,
            ),
            SandboxMount(
                resource_id="integration:dataset",
                target="/datasets/input",
                category=MountCategory.DATASET_READ_ONLY,
                read_only=True,
            ),
            SandboxMount(
                resource_id="integration:environment",
                target="/opt/reused-env",
                category=MountCategory.REGISTERED_ENV_READ_ONLY,
                read_only=True,
            ),
            SandboxMount(
                resource_id=workspace_resource,
                target="/workspace",
                category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,
                read_only=False,
            ),
        )
        fingerprint = environment_fingerprint(
            platform_name="linux",
            architecture=platform.machine(),
            python_version="integration",
        )
        plan = SandboxEnvironmentPlan(
            strategy=EnvironmentReuseStrategy.REUSED_READ_ONLY_ENV,
            base_image_digest=os.environ["PAPERREPRO_SANDBOX_IMAGE_DIGEST"],
            reused_environment_id="integration-environment",
            environment_fingerprint=fingerprint,
            compatibility={"status": "compatible", "reasons": ["fixture"]},
        )
        spec = SandboxSpec(
            run_id=run_id,
            experiment_id="linux-security-integration",
            image_digest=os.environ["PAPERREPRO_SANDBOX_IMAGE_DIGEST"],
            mounts=mounts,
        )
        handle = None
        try:
            handle = manager.create(spec, plan)
            manager.start(handle)

            def shell(script: str):
                return manager.exec(
                    handle,
                    program="sh",
                    argv=("-c", script),
                    cwd="/workspace",
                    timeout_seconds=30,
                )

            self.assertNotEqual(shell("touch /opt/reused-env/blocked").exit_code, 0)
            self.assertNotEqual(
                shell("python -m pip install --no-index --target /opt/reused-env pip").exit_code,
                0,
            )
            self.assertNotEqual(shell("touch /datasets/input/blocked").exit_code, 0)
            self.assertNotEqual(shell("touch /source/repository/blocked").exit_code, 0)
            self.assertEqual(shell("mkdir -p repository && touch repository/allowed").exit_code, 0)
            self.assertEqual(shell("test ! -e /var/run/docker.sock").exit_code, 0)
            self.assertEqual(shell("test ! -e /host").exit_code, 0)
            self.assertNotEqual(shell("touch /etc/paperrepro-blocked").exit_code, 0)
            self.assertEqual(shell("test ! -e /runs/sibling").exit_code, 0)
            self.assertEqual(shell("test \"$(id -u)\" != 0").exit_code, 0)

            attrs = manager.inspect(handle)
            host = attrs["HostConfig"]
            self.assertFalse(host["Privileged"])
            self.assertTrue(host["ReadonlyRootfs"])
            self.assertEqual(host["NetworkMode"], "none")
            self.assertFalse(host.get("PidMode"))
            self.assertEqual(host["IpcMode"], "private")
            self.assertIn("ALL", host["CapDrop"])
            self.assertIn("no-new-privileges:true", host["SecurityOpt"])
            self.assertGreater(host["PidsLimit"], 0)
            self.assertGreater(host["Memory"], 0)
        finally:
            if handle is not None:
                try:
                    manager.stop(handle)
                except Exception:
                    pass
            try:
                manager.cleanup(run_id)
            finally:
                trusted.remove_run_resources(run_id)

        self.assertEqual(
            before,
            {name: _tree_digest(path) for name, path in leaves.items()},
        )


if __name__ == "__main__":
    unittest.main()
