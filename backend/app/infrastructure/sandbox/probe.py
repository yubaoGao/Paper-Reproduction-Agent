"""Restricted read-only environment compatibility probe."""

from __future__ import annotations

import json
import uuid

from .models import (
    MountCategory,
    RegisteredResource,
    ResourceKind,
    SandboxEnvironmentPlan,
    SandboxMount,
    SandboxNetworkPolicy,
    SandboxSpec,
)


class SandboxEnvironmentProbe:
    """Imports requested frameworks only inside an offline hardened sandbox."""

    def __init__(self, manager, resource_registry, *, base_image_digest: str) -> None:
        self.manager = manager
        self.resource_registry = resource_registry
        self.base_image_digest = base_image_digest

    def __call__(self, descriptor) -> bool:
        run_id = f"env-probe-{uuid.uuid4().hex}"
        mounts = []
        for purpose, target in (("cache", "/cache"), ("workspace", "/workspace")):
            volume = self.manager.create_run_volume(run_id, purpose, 1024**3)
            resource_id = f"run:{run_id}:{purpose}"
            self.resource_registry.register(
                RegisteredResource(
                    resource_id=resource_id,
                    kind=ResourceKind.DOCKER_VOLUME,
                    category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,
                    volume_name=volume,
                    owner_run_id=run_id,
                )
            )
            mounts.append(
                SandboxMount(
                    resource_id=resource_id,
                    target=target,
                    category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,
                    read_only=False,
                )
            )
        mounts.append(
            SandboxMount(
                resource_id=f"environment:{descriptor.environment_id}",
                target=str(descriptor.metadata.get("mount_target") or "/opt/reused-env"),
                category=MountCategory.REGISTERED_ENV_READ_ONLY,
                read_only=True,
            )
        )
        spec = SandboxSpec(
            run_id=run_id,
            experiment_id="environment-probe",
            image_digest=self.base_image_digest,
            mounts=tuple(mounts),
            network_policy=SandboxNetworkPolicy.OFFLINE,
        )
        probe_plan = SandboxEnvironmentPlan(
            strategy="reused_read_only_env",
            base_image_digest=self.base_image_digest,
            reused_environment_id=descriptor.environment_id,
            environment_fingerprint=descriptor.fingerprint,
            compatibility={"status": "probe_required", "reasons": ["probe"]},
        )
        handle = self.manager.create(spec, probe_plan)
        self.manager.start(handle)
        packages = tuple(descriptor.fingerprint.frameworks)
        script = (
            "import importlib,json,platform,sys;"
            f"mods={packages!r};"
            "[importlib.import_module(x) for x in mods];"
            "print(json.dumps({'python':platform.python_version(),"
            "'machine':platform.machine(),'ok':True}))"
        )
        try:
            result = self.manager.exec(
                handle,
                program=str(
                    descriptor.metadata.get("python_program") or "/opt/reused-env/bin/python"
                ),
                argv=("-I", "-c", script),
                cwd="/workspace",
                timeout_seconds=120,
            )
            if result.timed_out or result.exit_code != 0:
                return False
            value = json.loads(result.stdout)
            return bool(value.get("ok"))
        except Exception:
            return False
        finally:
            try:
                self.manager.stop(handle)
            finally:
                try:
                    self.manager.cleanup(run_id)
                finally:
                    self.resource_registry.remove_run_resources(run_id)
