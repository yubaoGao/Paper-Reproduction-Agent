"""Opt-in NVIDIA device visibility test; this is not a GPU scheduler."""

from __future__ import annotations

import os
import platform
import unittest
import uuid

from backend.app.infrastructure.sandbox import (
    AssignedDeviceSet,
    DockerEngineBackend,
    EnvironmentReuseStrategy,
    HostMutationGuard,
    LinuxSandboxManager,
    RunResourceRegistry,
    SandboxEnvironmentPlan,
    SandboxSpec,
    TrustedResourceRegistry,
    environment_fingerprint,
)


ENABLED = (
    os.getenv("NVIDIA_SANDBOX_INTEGRATION") == "1"
    and platform.system() == "Linux"
    and bool(os.getenv("PAPERREPRO_ASSIGNED_GPU_IDS"))
    and bool(os.getenv("PAPERREPRO_NVIDIA_IMAGE_DIGEST"))
)


@unittest.skipUnless(
    ENABLED,
    "requires opt-in Linux NVIDIA runtime, digest image, and assigned device IDs",
)
class NvidiaSandboxIntegrationTests(unittest.TestCase):
    def test_only_explicitly_assigned_devices_are_requested_and_visible(self):
        assigned = tuple(
            item.strip()
            for item in os.environ["PAPERREPRO_ASSIGNED_GPU_IDS"].split(",")
            if item.strip()
        )
        devices = AssignedDeviceSet(gpu_device_ids=assigned)
        image = os.environ["PAPERREPRO_NVIDIA_IMAGE_DIGEST"]
        run_id = f"nvidia-{uuid.uuid4().hex}"
        manager = LinuxSandboxManager(
            DockerEngineBackend(),
            HostMutationGuard(TrustedResourceRegistry()),
            RunResourceRegistry(),
        )
        plan = SandboxEnvironmentPlan(
            strategy=EnvironmentReuseStrategy.REUSED_IMAGE,
            base_image_digest=image,
            reused_environment_id="nvidia-integration-image",
            environment_fingerprint=environment_fingerprint(
                platform_name="linux",
                architecture=platform.machine(),
                python_version="integration",
            ),
            compatibility={"status": "compatible", "reasons": ["fixture"]},
        )
        spec = SandboxSpec(
            run_id=run_id,
            experiment_id="nvidia-integration",
            image_digest=image,
            mounts=(),
            devices=devices,
        )
        handle = None
        try:
            handle = manager.create(spec, plan)
            manager.start(handle)
            request = manager.inspect(handle)["HostConfig"]["DeviceRequests"][0]
            self.assertEqual(tuple(request["DeviceIDs"]), assigned)
            visible = manager.exec(
                handle,
                program="nvidia-smi",
                argv=("--query-gpu=uuid", "--format=csv,noheader"),
                cwd="/tmp",
                timeout_seconds=30,
            )
            self.assertEqual(visible.exit_code, 0)
            self.assertEqual(len(visible.stdout.splitlines()), len(assigned))
        finally:
            if handle is not None:
                try:
                    manager.stop(handle)
                except Exception:
                    pass
            manager.cleanup(run_id)


if __name__ == "__main__":
    unittest.main()
