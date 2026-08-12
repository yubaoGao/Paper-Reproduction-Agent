"""Static environment inventory, fingerprinting, and deterministic reuse planning."""

from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
from typing import Callable

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from backend.app.domain import EnvironmentRequirement

from .models import (
    CompatibilityResult,
    CompatibilityStatus,
    EnvironmentArtifactType,
    EnvironmentDescriptor,
    EnvironmentFingerprint,
    EnvironmentRegistrationMode,
    EnvironmentReuseStrategy,
    PackageCacheSource,
    SandboxEnvironmentPlan,
)


class EnvironmentCatalogError(ValueError):
    pass


class RegisteredEnvironment:
    """Trusted record; path-bearing resource IDs never enter agent context."""

    def __init__(self, descriptor: EnvironmentDescriptor, resource_id: str | None):
        self.descriptor = descriptor
        self.resource_id = resource_id


def environment_fingerprint(
    *,
    platform_name: str,
    architecture: str,
    python_version: str,
    python_implementation: str = "cpython",
    packages: dict[str, str] | None = None,
    frameworks: dict[str, str] | None = None,
    system_packages: dict[str, str] | None = None,
    cuda_runtime: str | None = None,
    abi: dict[str, str] | None = None,
) -> EnvironmentFingerprint:
    content = {
        "platform": platform_name.casefold(),
        "architecture": architecture.casefold(),
        "python_version": python_version,
        "python_implementation": python_implementation.casefold(),
        "packages": dict(sorted((packages or {}).items())),
        "frameworks": dict(sorted((frameworks or {}).items())),
        "system_packages": dict(sorted((system_packages or {}).items())),
        "cuda_runtime": cuda_runtime,
        "abi": dict(sorted((abi or {}).items())),
    }
    digest = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return EnvironmentFingerprint(**content, content_digest=f"sha256:{digest}")


class StaticEnvironmentInspector:
    """Reads metadata files only; never activates or imports the environment."""

    FRAMEWORKS = {"torch", "tensorflow", "jax", "transformers"}

    def inspect(self, prefix: Path) -> EnvironmentFingerprint:
        prefix = prefix.resolve(strict=True)
        packages: dict[str, str] = {}
        python_version = self._pyvenv_python(prefix)
        conda_meta = prefix / "conda-meta"
        if conda_meta.is_dir():
            for record in sorted(conda_meta.glob("*.json")):
                try:
                    value = json.loads(record.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                name = str(value.get("name") or "").casefold()
                version = str(value.get("version") or "")
                if name and version:
                    packages[name] = version
                    if name == "python":
                        python_version = version
        if not packages:
            for site in prefix.glob("lib/python*/site-packages"):
                for record in sorted(site.glob("*.dist-info/METADATA")):
                    name, version = self._dist_info(record)
                    if name and version:
                        packages[name.casefold()] = version
        if not python_version:
            raise EnvironmentCatalogError("environment has no static Python metadata")
        frameworks = {
            name: packages[name]
            for name in self.FRAMEWORKS
            if name in packages
        }
        cuda = packages.get("cudatoolkit") or packages.get("cuda-version")
        return environment_fingerprint(
            platform_name="linux",
            architecture=platform.machine() or "unknown",
            python_version=python_version,
            packages=packages,
            frameworks=frameworks,
            cuda_runtime=cuda,
        )

    @staticmethod
    def _pyvenv_python(prefix: Path) -> str | None:
        config = prefix / "pyvenv.cfg"
        if not config.is_file():
            return None
        for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip().casefold() == "version":
                return value.strip()
        return None

    @staticmethod
    def _dist_info(path: Path) -> tuple[str | None, str | None]:
        name = version = None
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None, None
        for line in lines:
            if line.startswith("Name:"):
                name = line.partition(":")[2].strip()
            elif line.startswith("Version:"):
                version = line.partition(":")[2].strip()
            if name and version:
                break
        return name, version


class HostEnvironmentCatalog:
    """Admin-owned catalog limited to explicit allowlisted roots."""

    def __init__(
        self,
        allowlisted_roots=(),
        inspector=None,
        resource_registry=None,
    ) -> None:
        self.allowlisted_roots = tuple(
            Path(item).resolve(strict=True) for item in allowlisted_roots
        )
        self.inspector = inspector or StaticEnvironmentInspector()
        self.resource_registry = resource_registry
        self._records: dict[str, RegisteredEnvironment] = {}

    def register_static(
        self,
        descriptor: EnvironmentDescriptor,
        resource_id: str | None = None,
    ) -> None:
        if descriptor.registration_mode is not EnvironmentRegistrationMode.STATIC_REGISTRY:
            raise EnvironmentCatalogError("static registration mode is required")
        if descriptor.environment_id in self._records:
            raise EnvironmentCatalogError("environment ID is already registered")
        self._records[descriptor.environment_id] = RegisteredEnvironment(
            descriptor,
            resource_id,
        )

    def discover(self) -> tuple[EnvironmentDescriptor, ...]:
        discovered = []
        for root in self.allowlisted_roots:
            for prefix in sorted(item for item in root.iterdir() if item.is_dir()):
                fingerprint = self.inspector.inspect(prefix)
                identifier = "discovered:" + hashlib.sha256(
                    str(prefix).encode()
                ).hexdigest()[:16]
                descriptor = EnvironmentDescriptor(
                    environment_id=identifier,
                    artifact_type=EnvironmentArtifactType.READ_ONLY_PREFIX,
                    fingerprint=fingerprint,
                    registration_mode=EnvironmentRegistrationMode.ADMIN_DISCOVERY,
                    prefix_sensitive=True,
                    probe_required=True,
                )
                self._records[identifier] = RegisteredEnvironment(
                    descriptor,
                    f"environment:{identifier}",
                )
                if self.resource_registry is not None:
                    from .models import MountCategory, RegisteredResource, ResourceKind

                    self.resource_registry.register(
                        RegisteredResource(
                            resource_id=f"environment:{identifier}",
                            kind=ResourceKind.HOST_PATH,
                            category=MountCategory.REGISTERED_ENV_READ_ONLY,
                            host_path=str(prefix),
                        )
                    )
                discovered.append(descriptor)
        return tuple(discovered)

    def records(self) -> tuple[RegisteredEnvironment, ...]:
        return tuple(self._records[key] for key in sorted(self._records))


class SandboxImageCache:
    def __init__(self, descriptors=()) -> None:
        self._descriptors = tuple(descriptors)
        self._by_fingerprint = {
            item.fingerprint.content_digest: item for item in self._descriptors
        }

    def exact(self, fingerprint: EnvironmentFingerprint):
        return self._by_fingerprint.get(fingerprint.content_digest)

    def descriptors(self):
        return self._descriptors


class TrustedSystemPackageResolver:
    """Admin mapping from semantic system requirement to reviewed artifact name."""

    def __init__(self, mappings: dict[str, str]) -> None:
        self.mappings = dict(mappings)

    def resolve(self, requirements: tuple[str, ...]) -> tuple[str, ...]:
        missing = tuple(item for item in requirements if item not in self.mappings)
        if missing:
            raise EnvironmentCatalogError(
                "unapproved system dependencies: " + ", ".join(missing)
            )
        return tuple(self.mappings[item] for item in requirements)


class EnvironmentBroker:
    """Deterministic four-level resolution; agents cannot supply paths."""

    def __init__(
        self,
        catalog: HostEnvironmentCatalog,
        *,
        base_image_digest: str,
        image_cache: SandboxImageCache | None = None,
        package_caches: tuple[PackageCacheSource, ...] = (),
        platform_name: str = "linux",
        architecture: str = "x86_64",
        probe: Callable[[EnvironmentDescriptor], bool] | None = None,
        system_package_resolver: TrustedSystemPackageResolver | None = None,
    ) -> None:
        if "@sha256:" not in base_image_digest:
            raise ValueError("base image must be immutable and digest-pinned")
        self.catalog = catalog
        self.base_image_digest = base_image_digest
        self.image_cache = image_cache or SandboxImageCache()
        self.package_caches = package_caches
        self.platform_name = platform_name
        self.architecture = architecture
        self.probe = probe
        self.system_package_resolver = system_package_resolver

    def resolve(self, requirement: EnvironmentRequirement) -> SandboxEnvironmentPlan:
        desired = self._desired_fingerprint(requirement)
        image = next(
            (
                item
                for item in self.image_cache.descriptors()
                if self.compatibility(requirement, item.fingerprint).status
                is CompatibilityStatus.COMPATIBLE
            ),
            None,
        )
        if image is not None:
            return self._plan(
                EnvironmentReuseStrategy.REUSED_IMAGE,
                image.fingerprint,
                image.image_digest,
                image.environment_id,
                CompatibilityStatus.COMPATIBLE,
                "exact immutable image fingerprint match",
            )

        for record in self.catalog.records():
            result = self.compatibility(requirement, record.descriptor.fingerprint)
            if result.status is not CompatibilityStatus.COMPATIBLE:
                continue
            descriptor = record.descriptor
            if descriptor.artifact_type is not EnvironmentArtifactType.READ_ONLY_PREFIX:
                continue
            result = CompatibilityResult(
                status=CompatibilityStatus.PROBE_REQUIRED,
                reasons=("read-only environment requires restricted sandbox probe",),
            )
            if self.probe is None or not self.probe(descriptor):
                continue
            return SandboxEnvironmentPlan(
                strategy=EnvironmentReuseStrategy.REUSED_READ_ONLY_ENV,
                base_image_digest=self.base_image_digest,
                reused_environment_id=descriptor.environment_id,
                environment_fingerprint=descriptor.fingerprint,
                compatibility=CompatibilityResult(
                    status=CompatibilityStatus.COMPATIBLE,
                    reasons=("static match and restricted sandbox probe passed",),
                ),
                provenance={"reason": "exact registered read-only environment"},
            )

        downloads = tuple(requirement.dependencies)
        system_packages = ()
        if requirement.system_dependencies:
            if self.system_package_resolver is None:
                raise EnvironmentCatalogError(
                    "system dependencies require an administrator package resolver"
                )
            system_packages = self.system_package_resolver.resolve(
                requirement.system_dependencies
            )
        if self.package_caches:
            return SandboxEnvironmentPlan(
                strategy=EnvironmentReuseStrategy.SEEDED_FROM_PACKAGE_CACHE,
                base_image_digest=self.base_image_digest,
                environment_fingerprint=desired,
                package_cache_source_ids=tuple(item.cache_id for item in self.package_caches),
                required_downloads=downloads,
                resolved_system_packages=system_packages,
                compatibility=CompatibilityResult(
                    status=CompatibilityStatus.COMPATIBLE,
                    reasons=("build sandbox-private environment from read-only cache",),
                ),
                provenance={"reason": "no exact environment; approved cache available"},
            )
        return SandboxEnvironmentPlan(
            strategy=EnvironmentReuseStrategy.BUILT_IN_SANDBOX,
            base_image_digest=self.base_image_digest,
            environment_fingerprint=desired,
            required_downloads=downloads,
            resolved_system_packages=system_packages,
            compatibility=CompatibilityResult(
                status=CompatibilityStatus.COMPATIBLE,
                reasons=("sandbox-local provisioning required",),
            ),
            provenance={"reason": "cache miss; provision in sandbox-private storage"},
        )

    def compatibility(
        self,
        requirement: EnvironmentRequirement,
        candidate: EnvironmentFingerprint,
    ) -> CompatibilityResult:
        reasons = []
        missing = []
        if candidate.platform.casefold() != self.platform_name.casefold():
            reasons.append("platform mismatch")
        if candidate.architecture.casefold() != self.architecture.casefold():
            reasons.append("architecture mismatch")
        if requirement.python_constraint:
            try:
                if Version(candidate.python_version) not in SpecifierSet(
                    requirement.python_constraint
                ):
                    reasons.append("Python version mismatch")
            except Exception:
                reasons.append("invalid or unsupported Python compatibility metadata")
        for item in requirement.dependencies:
            try:
                parsed = Requirement(item)
            except Exception:
                missing.append(item)
                continue
            actual = candidate.packages.get(parsed.name.casefold())
            if actual is None or (parsed.specifier and Version(actual) not in parsed.specifier):
                missing.append(item)
        for framework in requirement.frameworks:
            if framework.casefold() not in candidate.frameworks:
                missing.append(framework)
        for system_package in requirement.system_dependencies:
            if system_package.casefold() not in candidate.system_packages:
                missing.append(system_package)
        if requirement.cuda_hints:
            requested = requirement.cuda_hints[0].casefold().replace("cuda", "").strip(" =")
            actual = (candidate.cuda_runtime or "").casefold().replace("cuda", "").strip(" =")
            if not actual or not actual.startswith(requested):
                reasons.append("CUDA runtime mismatch")
        if missing:
            reasons.append("required packages are missing or incompatible")
        return CompatibilityResult(
            status=(
                CompatibilityStatus.INCOMPATIBLE
                if reasons
                else CompatibilityStatus.COMPATIBLE
            ),
            reasons=tuple(reasons),
            missing_packages=tuple(missing),
        )

    def _desired_fingerprint(self, requirement: EnvironmentRequirement):
        packages = {}
        for item in requirement.dependencies:
            try:
                parsed = Requirement(item)
            except Exception:
                continue
            exact = next(
                (spec.version for spec in parsed.specifier if spec.operator == "=="),
                str(parsed.specifier) or "required",
            )
            packages[parsed.name.casefold()] = exact
        frameworks = {
            item.casefold(): packages.get(item.casefold(), "required")
            for item in requirement.frameworks
        }
        python_version = requirement.python_constraint or ">=3.11"
        return environment_fingerprint(
            platform_name=self.platform_name,
            architecture=self.architecture,
            python_version=python_version,
            packages=packages,
            frameworks=frameworks,
            system_packages={item.casefold(): "required" for item in requirement.system_dependencies},
            cuda_runtime=requirement.cuda_hints[0] if requirement.cuda_hints else None,
        )

    @staticmethod
    def _plan(strategy, fingerprint, image, environment_id, status, reason):
        return SandboxEnvironmentPlan(
            strategy=strategy,
            base_image_digest=image,
            reused_environment_id=environment_id,
            environment_fingerprint=fingerprint,
            compatibility=CompatibilityResult(status=status, reasons=(reason,)),
            provenance={"reason": reason},
        )
