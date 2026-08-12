"""Opt-in immutable environment reuse probe on a Linux Docker host."""

from __future__ import annotations

import hashlib
import os
import platform
from pathlib import Path
import unittest

from backend.app.domain import EnvironmentRequirement
from backend.app.infrastructure.sandbox import (
    DockerEngineBackend,
    EnvironmentBroker,
    EnvironmentReuseStrategy,
    HostEnvironmentCatalog,
    HostMutationGuard,
    LinuxSandboxManager,
    RunResourceRegistry,
    SandboxEnvironmentProbe,
    TrustedResourceRegistry,
)


ENABLED = (
    os.getenv("ENVIRONMENT_REUSE_INTEGRATION") == "1"
    and platform.system() == "Linux"
    and bool(os.getenv("PAPERREPRO_ENVIRONMENT_REGISTRY"))
    and bool(os.getenv("PAPERREPRO_SANDBOX_IMAGE_DIGEST"))
    and bool(os.getenv("PAPERREPRO_QUOTA_VOLUME_DRIVER"))
)


def _digest(root: Path) -> str:
    value = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        value.update(path.relative_to(root).as_posix().encode())
        value.update(path.read_bytes())
    return value.hexdigest()


@unittest.skipUnless(
    ENABLED,
    "requires opt-in Linux Docker, environment registry, digest image, and quota driver",
)
class EnvironmentReuseIntegrationTests(unittest.TestCase):
    def test_registered_environment_probe_does_not_mutate_host_prefix(self):
        root = Path(os.environ["PAPERREPRO_ENVIRONMENT_REGISTRY"]).resolve(strict=True)
        before = _digest(root)
        trusted = TrustedResourceRegistry()
        catalog = HostEnvironmentCatalog((root,), resource_registry=trusted)
        descriptors = catalog.discover()
        self.assertTrue(descriptors, "registry must contain at least one environment")
        selected = descriptors[0]
        runs = RunResourceRegistry()
        manager = LinuxSandboxManager(
            DockerEngineBackend(
                quota_volume_driver=os.environ["PAPERREPRO_QUOTA_VOLUME_DRIVER"]
            ),
            HostMutationGuard(trusted),
            runs,
        )
        base_image = os.environ["PAPERREPRO_SANDBOX_IMAGE_DIGEST"]
        probe = SandboxEnvironmentProbe(manager, trusted, base_image_digest=base_image)
        packages = tuple(
            f"{name}=={version}"
            for name, version in selected.fingerprint.packages.items()
        )
        result = EnvironmentBroker(
            catalog,
            base_image_digest=base_image,
            architecture=selected.fingerprint.architecture,
            probe=probe,
        ).resolve(
            EnvironmentRequirement(
                python_constraint=f"=={selected.fingerprint.python_version}",
                dependencies=packages,
                frameworks=tuple(selected.fingerprint.frameworks),
                cuda_hints=(selected.fingerprint.cuda_runtime,)
                if selected.fingerprint.cuda_runtime
                else (),
            )
        )
        self.assertEqual(result.strategy, EnvironmentReuseStrategy.REUSED_READ_ONLY_ENV)
        self.assertEqual(before, _digest(root))


if __name__ == "__main__":
    unittest.main()
