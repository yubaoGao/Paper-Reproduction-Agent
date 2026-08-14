"""Persistent package cache and prepared-environment artifacts.

Host persistence lives under the configured data root. Sandbox cleanup never
deletes these trees; sandboxes only receive read-only mounts of published
artifacts. Writable sandbox-private venvs are never registered for reuse.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .environment import canonical_sha256
from .models import (
    EnvironmentArtifactType,
    EnvironmentDescriptor,
    EnvironmentFingerprint,
    EnvironmentRegistrationMode,
    MountCategory,
    PackageCacheSource,
    PreparedEnvironmentValidationState,
    RegisteredResource,
    ResourceKind,
    ReusableEnvironmentArtifact,
)


class EnvironmentAssetError(RuntimeError):
    pass


def principal_key(principal: str) -> str:
    return canonical_sha256({"principal": principal}).removeprefix("sha256:")[:16]


def fingerprint_key(fingerprint: EnvironmentFingerprint) -> str:
    return fingerprint.content_digest.removeprefix("sha256:")


class FilesystemEnvironmentAssetStore:
    """Principal- and fingerprint-isolated reusable environment assets."""

    def __init__(self, data_root, *, stale_lock_seconds: float = 7200) -> None:
        self.data_root = Path(data_root).resolve()
        self.stale_lock_seconds = stale_lock_seconds
        self.cache_root = self.data_root / "cache" / "packages"
        self.cache_staging = self.data_root / "cache" / "staging"
        self.prepared_root = self.data_root / "environments" / "prepared"
        self.env_staging = self.data_root / "environments" / "staging"
        self.lock_root = self.data_root / "environments" / "locks"

    def ensure_layout(self) -> None:
        for path in (
            self.cache_root,
            self.cache_staging,
            self.prepared_root,
            self.env_staging,
            self.lock_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def new_env_staging(self) -> Path:
        self.ensure_layout()
        path = self.env_staging / uuid.uuid4().hex
        path.mkdir(parents=True, exist_ok=False)
        (path / "prefix").mkdir()
        return path

    def new_cache_staging(self) -> Path:
        self.ensure_layout()
        path = self.cache_staging / uuid.uuid4().hex
        path.mkdir(parents=True, exist_ok=False)
        return path

    def prepared_dir(self, principal: str, fingerprint: EnvironmentFingerprint) -> Path:
        return self.prepared_root / principal_key(principal) / fingerprint_key(fingerprint)

    def cache_dir(
        self,
        principal: str,
        fingerprint: EnvironmentFingerprint,
        package_manager: str,
    ) -> Path:
        return (
            self.cache_root
            / principal_key(principal)
            / fingerprint_key(fingerprint)
            / package_manager
        )

    def lock_path(self, principal: str, fingerprint: EnvironmentFingerprint) -> Path:
        name = f"{principal_key(principal)}--{fingerprint_key(fingerprint)}.lock"
        return self.lock_root / name

    def get_published(
        self, principal: str, fingerprint: EnvironmentFingerprint,
    ) -> ReusableEnvironmentArtifact | None:
        manifest = self.prepared_dir(principal, fingerprint) / "ARTIFACT.json"
        if not manifest.is_file():
            return None
        try:
            artifact = ReusableEnvironmentArtifact.model_validate_json(
                manifest.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None
        if artifact.validation_state is PreparedEnvironmentValidationState.INVALIDATED:
            return None
        if artifact.validation_state is not PreparedEnvironmentValidationState.PASSED:
            return None
        if artifact.fingerprint.content_digest != fingerprint.content_digest:
            return None
        if artifact.owner_principal not in {None, principal}:
            return None
        return artifact

    def prefix_path(self, artifact: ReusableEnvironmentArtifact) -> Path:
        if artifact.owner_principal is None:
            raise EnvironmentAssetError("prepared artifact is missing ownership")
        return self.prepared_dir(artifact.owner_principal, artifact.fingerprint) / "prefix"

    def package_caches(
        self, principal: str, fingerprint: EnvironmentFingerprint,
    ) -> tuple[PackageCacheSource, ...]:
        root = self.cache_root / principal_key(principal) / fingerprint_key(fingerprint)
        if not root.is_dir():
            return ()
        sources = []
        for manager_dir in sorted(item for item in root.iterdir() if item.is_dir()):
            manifest = manager_dir / "CACHE.json"
            if not manifest.is_file():
                continue
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                source = PackageCacheSource.model_validate(payload)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if source.fingerprint != fingerprint.content_digest:
                continue
            if source.owner_principal not in {None, principal}:
                continue
            sources.append(source)
        return tuple(sources)

    def try_begin_build(
        self, principal: str, fingerprint: EnvironmentFingerprint, run_id: str,
    ):
        self.ensure_layout()
        if self.get_published(principal, fingerprint) is not None:
            return None
        path = self.lock_path(principal, fingerprint)
        self._clear_stale_lock(path)
        payload = json.dumps(
            {
                "run_id": run_id,
                "principal": principal,
                "fingerprint": fingerprint.content_digest,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None
        try:
            os.write(fd, payload.encode())
        finally:
            os.close(fd)
        return path

    def build_in_progress(self, principal: str, fingerprint: EnvironmentFingerprint) -> bool:
        path = self.lock_path(principal, fingerprint)
        self._clear_stale_lock(path)
        return path.is_file()

    def wait_for_published(
        self,
        principal: str,
        fingerprint: EnvironmentFingerprint,
        *,
        timeout_seconds: float,
    ) -> ReusableEnvironmentArtifact | None:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while time.monotonic() < deadline:
            artifact = self.get_published(principal, fingerprint)
            if artifact is not None:
                return artifact
            if not self.build_in_progress(principal, fingerprint):
                return self.get_published(principal, fingerprint)
            time.sleep(0.05)
        return self.get_published(principal, fingerprint)

    def abort_build(
        self, principal: str, fingerprint: EnvironmentFingerprint, run_id: str,
    ) -> None:
        path = self.lock_path(principal, fingerprint)
        if not path.is_file():
            return
        holder = self._lock_run_id(path)
        if holder in {None, run_id}:
            path.unlink(missing_ok=True)

    def complete_build(
        self, principal: str, fingerprint: EnvironmentFingerprint, run_id: str,
    ) -> None:
        self.abort_build(principal, fingerprint, run_id)

    def publish_prepared(
        self,
        *,
        principal: str,
        fingerprint: EnvironmentFingerprint,
        staging_dir: Path,
        python_version: str,
        base_image_digest: str,
        dependency_specification_hash: str,
        cuda_runtime: str | None,
    ) -> ReusableEnvironmentArtifact | None:
        prefix = self._normalized_prefix(Path(staging_dir) / "prefix")
        if prefix is None:
            shutil.rmtree(staging_dir, ignore_errors=True)
            return None
        final_dir = self.prepared_dir(principal, fingerprint)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if (final_dir / "ARTIFACT.json").is_file():
            existing = self.get_published(principal, fingerprint)
            if existing is not None:
                shutil.rmtree(staging_dir, ignore_errors=True)
                return existing
            shutil.rmtree(final_dir, ignore_errors=True)
        artifact_id = "envart:" + canonical_sha256(
            {
                "principal": principal,
                "fingerprint": fingerprint.content_digest,
                "kind": "prepared_environment",
            }
        ).removeprefix("sha256:")[:24]
        artifact = ReusableEnvironmentArtifact(
            artifact_id=artifact_id,
            artifact_type=EnvironmentArtifactType.PREPARED_ENVIRONMENT,
            fingerprint=fingerprint,
            resource_id=f"environment:{artifact_id}",
            python_version=python_version,
            base_image_digest=base_image_digest,
            dependency_specification_hash=dependency_specification_hash,
            cuda_runtime=cuda_runtime,
            created_at=datetime.now(timezone.utc),
            owner_principal=principal,
            validation_state=PreparedEnvironmentValidationState.PASSED,
            mount_target="/sandbox-env",
        )
        (Path(staging_dir) / "ARTIFACT.json").write_text(
            artifact.model_dump_json(), encoding="utf-8",
        )
        normalized_staging = Path(staging_dir) / "prefix"
        if prefix.resolve() != normalized_staging.resolve():
            replacement = Path(staging_dir) / f"prefix-{uuid.uuid4().hex}"
            shutil.copytree(prefix, replacement)
            shutil.rmtree(normalized_staging, ignore_errors=True)
            replacement.rename(normalized_staging)
        try:
            os.replace(staging_dir, final_dir)
        except OSError:
            shutil.rmtree(staging_dir, ignore_errors=True)
            return self.get_published(principal, fingerprint)
        published = self.get_published(principal, fingerprint)
        if published is None:
            shutil.rmtree(final_dir, ignore_errors=True)
            return None
        return published

    def publish_package_cache(
        self,
        *,
        principal: str,
        fingerprint: EnvironmentFingerprint,
        package_manager: str,
        staging_dir: Path,
    ) -> PackageCacheSource | None:
        source_root = Path(staging_dir)
        if not any(source_root.iterdir()):
            shutil.rmtree(staging_dir, ignore_errors=True)
            return None
        final_dir = self.cache_dir(principal, fingerprint, package_manager)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if (final_dir / "CACHE.json").is_file():
            shutil.rmtree(staging_dir, ignore_errors=True)
            caches = {
                item.package_manager: item
                for item in self.package_caches(principal, fingerprint)
            }
            return caches.get(package_manager)
        cache_id = "pkgcache:" + canonical_sha256(
            {
                "principal": principal,
                "fingerprint": fingerprint.content_digest,
                "manager": package_manager,
            }
        ).removeprefix("sha256:")[:24]
        source = PackageCacheSource(
            cache_id=cache_id,
            package_manager=package_manager,
            fingerprint=fingerprint.content_digest,
            owner_principal=principal,
        )
        (source_root / "CACHE.json").write_text(
            source.model_dump_json(), encoding="utf-8",
        )
        try:
            os.replace(staging_dir, final_dir)
        except OSError:
            shutil.rmtree(staging_dir, ignore_errors=True)
            caches = {
                item.package_manager: item
                for item in self.package_caches(principal, fingerprint)
            }
            return caches.get(package_manager)
        return source

    def invalidate(self, artifact_id: str) -> None:
        for manifest in self.prepared_root.glob("*/*/ARTIFACT.json"):
            try:
                artifact = ReusableEnvironmentArtifact.model_validate_json(
                    manifest.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            if artifact.artifact_id != artifact_id:
                continue
            updated = artifact.model_copy(
                update={"validation_state": PreparedEnvironmentValidationState.INVALIDATED}
            )
            manifest.write_text(updated.model_dump_json(), encoding="utf-8")
            return

    def iter_published(self) -> tuple[ReusableEnvironmentArtifact, ...]:
        artifacts = []
        if not self.prepared_root.is_dir():
            return ()
        for manifest in sorted(self.prepared_root.glob("*/*/ARTIFACT.json")):
            try:
                artifact = ReusableEnvironmentArtifact.model_validate_json(
                    manifest.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            if artifact.validation_state is PreparedEnvironmentValidationState.PASSED:
                artifacts.append(artifact)
        return tuple(artifacts)

    def iter_package_caches(self) -> tuple[tuple[PackageCacheSource, Path], ...]:
        items = []
        if not self.cache_root.is_dir():
            return ()
        for manifest in sorted(self.cache_root.glob("*/*/*/CACHE.json")):
            try:
                source = PackageCacheSource.model_validate_json(
                    manifest.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            items.append((source, manifest.parent))
        return tuple(items)

    def register_published(self, resource_registry, catalog) -> None:
        from .environment import RegisteredEnvironment

        for artifact in self.iter_published():
            host_path = str(self.prefix_path(artifact))
            resource_registry.register_or_validate(
                RegisteredResource(
                    resource_id=artifact.resource_id,
                    kind=ResourceKind.HOST_PATH,
                    category=MountCategory.REGISTERED_ENV_READ_ONLY,
                    host_path=host_path,
                    metadata={
                        "prepared": True,
                        "owner_principal": artifact.owner_principal,
                        "immutable": True,
                    },
                )
            )
            descriptor = EnvironmentDescriptor(
                environment_id=artifact.artifact_id,
                artifact_type=EnvironmentArtifactType.PREPARED_ENVIRONMENT,
                fingerprint=artifact.fingerprint,
                registration_mode=EnvironmentRegistrationMode.STATIC_REGISTRY,
                prefix_sensitive=True,
                probe_required=True,
                metadata={
                    "python_program": "/sandbox-env/venv/bin/python",
                    "mount_target": artifact.mount_target or "/sandbox-env",
                    "prepared": True,
                },
            )
            if artifact.artifact_id not in catalog._records:
                catalog._records[artifact.artifact_id] = RegisteredEnvironment(
                    descriptor, artifact.resource_id,
                )
        for source, path in self.iter_package_caches():
            resource_registry.register_or_validate(
                RegisteredResource(
                    resource_id=f"cache:{source.cache_id}",
                    kind=ResourceKind.HOST_PATH,
                    category=MountCategory.REGISTERED_PACKAGE_CACHE_READ_ONLY,
                    host_path=str(path),
                    metadata={
                        "package_manager": source.package_manager,
                        "owner_principal": source.owner_principal,
                        "immutable": True,
                    },
                )
            )

    def _normalized_prefix(self, exported: Path) -> Path | None:
        if not exported.is_dir():
            return None
        candidates = [
            exported,
            exported / "venv",
            exported / "sandbox-env",
            exported / "sandbox-env" / "venv",
        ]
        for candidate in candidates:
            if (candidate / "pyvenv.cfg").is_file():
                return candidate.parent if candidate.name == "venv" else candidate
            if (candidate / "venv" / "pyvenv.cfg").is_file():
                return candidate
        for match in exported.rglob("pyvenv.cfg"):
            parent = match.parent
            return parent.parent if parent.name == "venv" else parent
        return None

    def _clear_stale_lock(self, path: Path) -> None:
        if not path.is_file():
            return
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return
        if age > self.stale_lock_seconds:
            path.unlink(missing_ok=True)

    @staticmethod
    def _lock_run_id(path: Path) -> str | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        run_id = payload.get("run_id")
        return run_id if isinstance(run_id, str) else None


class EnvironmentArtifactPromoter:
    """Export a validated sandbox-private env to an immutable host artifact."""

    def __init__(
        self,
        *,
        manager,
        store: FilesystemEnvironmentAssetStore,
        resource_registry,
        catalog,
    ) -> None:
        self.manager = manager
        self.store = store
        self.resource_registry = resource_registry
        self.catalog = catalog

    def promote(self, handle, plan, *, principal: str) -> ReusableEnvironmentArtifact | None:
        if not plan.provenance.get("build_claimed"):
            return self.store.get_published(principal, plan.environment_fingerprint)
        if not self._probe_private_env(handle, plan):
            return None
        env_staging = self.store.new_env_staging()
        cache_staging = self.store.new_cache_staging()
        try:
            self.manager.copy_from_container(handle, "/sandbox-env", env_staging / "prefix")
            try:
                self.manager.copy_from_container(handle, "/cache/pip", cache_staging)
            except Exception:
                shutil.rmtree(cache_staging, ignore_errors=True)
                cache_staging = None
        except Exception as exc:
            shutil.rmtree(env_staging, ignore_errors=True)
            if cache_staging is not None:
                shutil.rmtree(cache_staging, ignore_errors=True)
            raise EnvironmentAssetError("artifact export failed") from exc
        python_version = plan.environment_fingerprint.python_version
        artifact = self.store.publish_prepared(
            principal=principal,
            fingerprint=plan.environment_fingerprint,
            staging_dir=env_staging,
            python_version=python_version,
            base_image_digest=plan.base_image_digest,
            dependency_specification_hash=(
                plan.environment_fingerprint.dependency_specification_hash
                or canonical_sha256({"dependencies": list(plan.required_downloads)})
            ),
            cuda_runtime=plan.environment_fingerprint.cuda_runtime,
        )
        if cache_staging is not None:
            source = self.store.publish_package_cache(
                principal=principal,
                fingerprint=plan.environment_fingerprint,
                package_manager="pip",
                staging_dir=cache_staging,
            )
            if source is not None:
                self.store.register_published(self.resource_registry, self.catalog)
        if artifact is None:
            return None
        self.store.register_published(self.resource_registry, self.catalog)
        return artifact

    def _probe_private_env(self, handle, plan) -> bool:
        result = self.manager.exec(
            handle,
            program="/sandbox-env/venv/bin/python",
            argv=("-I", "-c", "import sys; print(sys.version)"),
            cwd="/sandbox-env",
            timeout_seconds=60,
        )
        if result.timed_out or result.exit_code != 0:
            return False
        return True


def wire_production_environment_reuse(
    *,
    data_root: Path | str,
    resource_registry,
    base_image_digest: str,
    manager,
    probe=None,
    platform_name: str = "linux",
    architecture: str = "x86_64",
    build_wait_seconds: float = 1800,
    system_package_resolver=None,
    image_cache=None,
):
    from .environment import EnvironmentBroker, HostEnvironmentCatalog

    store = FilesystemEnvironmentAssetStore(data_root)
    store.ensure_layout()
    catalog = HostEnvironmentCatalog(resource_registry=resource_registry)
    store.register_published(resource_registry, catalog)
    broker = EnvironmentBroker(
        catalog,
        base_image_digest=base_image_digest,
        image_cache=image_cache,
        platform_name=platform_name,
        architecture=architecture,
        probe=probe,
        system_package_resolver=system_package_resolver,
        asset_store=store,
        build_wait_seconds=build_wait_seconds,
    )
    promoter = EnvironmentArtifactPromoter(
        manager=manager,
        store=store,
        resource_registry=resource_registry,
        catalog=catalog,
    )
    return broker, promoter, store, catalog
