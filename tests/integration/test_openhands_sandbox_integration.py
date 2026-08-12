"""Opt-in OpenHands controller execution inside the hardened sandbox."""

from __future__ import annotations

import os
import platform
import unittest
import uuid

from backend.app.infrastructure.sandbox import (
    DockerEngineBackend,
    EnvironmentReuseStrategy,
    HostMutationGuard,
    LinuxSandboxManager,
    MountCategory,
    OpenHandsCodingAgentAdapter,
    RegisteredResource,
    ResourceKind,
    RunResourceRegistry,
    SandboxEnvironmentPlan,
    SandboxedOpenHandsController,
    SandboxMount,
    SandboxSession,
    SandboxSessionRegistry,
    SandboxSpec,
    TrustedResourceRegistry,
    environment_fingerprint,
)
from backend.app.runtime.curie_models import CodingRequest


ENABLED = (
    os.getenv("OPENHANDS_SANDBOX_INTEGRATION") == "1"
    and platform.system() == "Linux"
    and bool(os.getenv("PAPERREPRO_OPENHANDS_IMAGE_DIGEST"))
    and bool(os.getenv("PAPERREPRO_QUOTA_VOLUME_DRIVER"))
)


@unittest.skipUnless(
    ENABLED,
    "requires opt-in Linux OpenHands digest image and quota volume driver",
)
class OpenHandsSandboxIntegrationTests(unittest.TestCase):
    def test_openhands_edits_only_current_private_workspace(self):
        run_id = f"openhands-{uuid.uuid4().hex}"
        trusted = TrustedResourceRegistry()
        runs = RunResourceRegistry()
        manager = LinuxSandboxManager(
            DockerEngineBackend(
                quota_volume_driver=os.environ["PAPERREPRO_QUOTA_VOLUME_DRIVER"]
            ),
            HostMutationGuard(trusted),
            runs,
        )
        volume = manager.create_run_volume(run_id, "workspace", 1024**3)
        resource_id = f"run:{run_id}:workspace"
        trusted.register(
            RegisteredResource(
                resource_id=resource_id,
                kind=ResourceKind.DOCKER_VOLUME,
                category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,
                volume_name=volume,
                owner_run_id=run_id,
            )
        )
        image = os.environ["PAPERREPRO_OPENHANDS_IMAGE_DIGEST"]
        plan = SandboxEnvironmentPlan(
            strategy=EnvironmentReuseStrategy.REUSED_IMAGE,
            base_image_digest=image,
            reused_environment_id="openhands-integration-image",
            environment_fingerprint=environment_fingerprint(
                platform_name="linux",
                architecture=platform.machine(),
                python_version="integration",
            ),
            compatibility={"status": "compatible", "reasons": ["fixture"]},
        )
        spec = SandboxSpec(
            run_id=run_id,
            experiment_id="openhands-integration",
            image_digest=image,
            mounts=(
                SandboxMount(
                    resource_id=resource_id,
                    target="/workspace",
                    category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,
                    read_only=False,
                ),
            ),
        )
        handle = None
        try:
            handle = manager.create(spec, plan)
            manager.start(handle)
            created = manager.exec(
                handle,
                program="mkdir",
                argv=("-p", "/workspace/repository"),
                cwd="/workspace",
                timeout_seconds=30,
            )
            self.assertEqual(created.exit_code, 0)
            sessions = SandboxSessionRegistry()
            sessions.put(SandboxSession(handle, spec))
            result = OpenHandsCodingAgentAdapter(
                SandboxedOpenHandsController(manager, sessions)
            ).apply(
                CodingRequest(
                    run_id=run_id,
                    experiment_id="openhands-integration",
                    instruction=(
                        "Create /workspace/repository/paperrepro_openhands_probe.txt "
                        "containing the single word sandboxed. Do not change other files."
                    ),
                    allowed_change_categories=("integration_probe",),
                    locked_constraint_keys=("filesystem_boundary",),
                )
            )
            self.assertTrue(result.patch_id)
            checked = manager.exec(
                handle,
                program="test",
                argv=("-f", "/workspace/repository/paperrepro_openhands_probe.txt"),
                cwd="/workspace/repository",
                timeout_seconds=30,
            )
            self.assertEqual(checked.exit_code, 0)
            mounts = manager.inspect(handle)["Mounts"]
            self.assertFalse(any("docker.sock" in item.get("Source", "") for item in mounts))
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


if __name__ == "__main__":
    unittest.main()
