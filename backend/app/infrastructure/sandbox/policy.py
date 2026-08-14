"""Non-overridable host mutation and container security policy."""

from __future__ import annotations

import os
import platform
from pathlib import Path, PurePosixPath

from .models import (
    MountCategory,
    ResolvedMount,
    ResourceKind,
    SandboxNetworkPolicy,
    SandboxSpec,
)
from .registry import TrustedResourceRegistry


class SandboxPolicyViolation(ValueError):
    pass


# Self and descendants are always forbidden, even if configured as an allowed root.
# "/" is exact-only: it is a parent of every absolute POSIX path.
_DANGEROUS_SYSTEM_TREES = {
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/opt",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
    "/var/run",
    "/var/lib/docker",
}
# Broad user/home mounts are exact-only. Descendants are decided by allowed roots.
_BROAD_EXACT_MOUNTS = {
    "/home",
}
_FORBIDDEN_SOCKET_SUFFIXES = (
    "/docker.sock",
    "/containerd.sock",
    "/crio.sock",
    "/cri-dockerd.sock",
)
_READ_ONLY_CATEGORIES = {
    MountCategory.REGISTERED_ENV_READ_ONLY,
    MountCategory.REGISTERED_PACKAGE_CACHE_READ_ONLY,
    MountCategory.REPOSITORY_SNAPSHOT_READ_ONLY,
    MountCategory.DATASET_READ_ONLY,
    MountCategory.CHECKPOINT_READ_ONLY,
    MountCategory.PRETRAINED_MODEL_READ_ONLY,
    MountCategory.APPROVED_CONFIG_READ_ONLY,
}
_ALLOWED_WRITE_ROOTS = {
    "/workspace",
    "/sandbox-env",
    "/cache",
    "/output",
    "/tmp",
    "/home/sandbox",
}
_CATEGORY_TARGET_ROOTS = {
    MountCategory.REGISTERED_ENV_READ_ONLY: ("/opt/reused-env", "/sandbox-env"),
    MountCategory.REGISTERED_PACKAGE_CACHE_READ_ONLY: ("/seed-cache",),
    MountCategory.REPOSITORY_SNAPSHOT_READ_ONLY: ("/source/repository",),
    MountCategory.DATASET_READ_ONLY: ("/datasets",),
    MountCategory.CHECKPOINT_READ_ONLY: ("/checkpoints",),
    MountCategory.PRETRAINED_MODEL_READ_ONLY: ("/checkpoints",),
    MountCategory.APPROVED_CONFIG_READ_ONLY: ("/config",),
}


def _container_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise SandboxPolicyViolation("container path must be absolute and normalized")
    return path


def is_within(path: str, roots: tuple[str, ...]) -> bool:
    candidate = _container_path(path)
    return any(candidate == _container_path(root) or _container_path(root) in candidate.parents for root in roots)


def _posix_host_path(path: Path) -> PurePosixPath:
    text = path.as_posix().rstrip("/") or "/"
    return PurePosixPath(text)


def is_forbidden_host_path(path: Path) -> bool:
    """Return True if a resolved host path is a forbidden system or broad mount."""
    candidate = _posix_host_path(path)
    if candidate == PurePosixPath("/"):
        return True
    if candidate in {PurePosixPath(item) for item in _BROAD_EXACT_MOUNTS}:
        return True
    return any(
        candidate == PurePosixPath(root) or PurePosixPath(root) in candidate.parents
        for root in _DANGEROUS_SYSTEM_TREES
    )


def is_strict_descendant(path: Path, root: Path) -> bool:
    """Return True if path is inside root after pathlib containment, not equal to it."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return bool(relative.parts)


class HostMutationGuard:
    """Resolve only registered resources and reject dangerous host exposure."""

    def __init__(
        self,
        resources: TrustedResourceRegistry,
        *,
        allowed_host_roots: tuple[str | Path, ...] = (),
        host_mount_points: tuple[str, ...] | None = None,
    ) -> None:
        self.resources = resources
        self.allowed_host_roots = self._normalize_allowed_host_roots(allowed_host_roots)
        self.host_mount_points = (
            host_mount_points
            if host_mount_points is not None
            else self._linux_mount_points()
        )

    def validate_and_resolve(self, spec: SandboxSpec) -> tuple[ResolvedMount, ...]:
        self._validate_container_security(spec)
        resolved = []
        targets = set()
        for mount in spec.mounts:
            target = str(_container_path(mount.target))
            if target in targets:
                raise SandboxPolicyViolation("duplicate sandbox mount target")
            targets.add(target)
            resource = self.resources.resolve(mount.resource_id)
            if resource.category is not mount.category:
                raise SandboxPolicyViolation("mount category does not match registry")
            if mount.category in _READ_ONLY_CATEGORIES and not mount.read_only:
                raise SandboxPolicyViolation("shared resources must be read-only")
            target_roots = _CATEGORY_TARGET_ROOTS.get(mount.category)
            if target_roots and not is_within(target, target_roots):
                raise SandboxPolicyViolation(
                    "read-only resource target is outside its approved container area"
                )
            if mount.category is MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE:
                if mount.read_only:
                    raise SandboxPolicyViolation("run-private volume must be writable")
                if resource.kind is not ResourceKind.DOCKER_VOLUME:
                    raise SandboxPolicyViolation("writable host bind mounts are forbidden")
                if resource.owner_run_id != spec.run_id:
                    raise SandboxPolicyViolation("run-private resource ownership mismatch")
                if not is_within(target, spec.allowed_write_roots):
                    raise SandboxPolicyViolation("writable target is outside allowed roots")
            source = resource.host_path or resource.volume_name
            if resource.kind is ResourceKind.HOST_PATH:
                self._validate_host_path(source)
                self._validate_no_recursive_submounts(source)
            if resource.kind is ResourceKind.DOCKER_NETWORK:
                raise SandboxPolicyViolation("network cannot be used as a mount")
            resolved.append(
                ResolvedMount(
                    source=source,
                    target=target,
                    category=mount.category,
                    read_only=mount.read_only,
                    kind=resource.kind,
                )
            )
        return tuple(resolved)

    def resolve_network(self, spec: SandboxSpec) -> str | None:
        if spec.network_policy is SandboxNetworkPolicy.OFFLINE:
            if spec.egress_network_resource_id is not None:
                raise SandboxPolicyViolation("offline sandbox cannot attach a network")
            return None
        if not spec.egress_network_resource_id:
            raise SandboxPolicyViolation(
                "egress requires an administrator-registered filtered network"
            )
        resource = self.resources.resolve(spec.egress_network_resource_id)
        if resource.kind is not ResourceKind.DOCKER_NETWORK:
            raise SandboxPolicyViolation("egress resource is not a Docker network")
        required = {
            "filtered_egress": True,
            "block_private_cidrs": True,
            "block_link_local": True,
            "block_cloud_metadata": True,
            "inter_container_communication": False,
        }
        if any(resource.metadata.get(key) is not value for key, value in required.items()):
            raise SandboxPolicyViolation(
                "egress network lacks required isolation and destination filters"
            )
        return resource.network_name

    @staticmethod
    def _normalize_allowed_host_roots(roots: tuple[str | Path, ...]) -> tuple[Path, ...]:
        normalized = []
        for root in roots:
            path = Path(root).resolve(strict=True)
            if not path.is_dir():
                raise SandboxPolicyViolation("allowed host root must be a directory")
            if is_forbidden_host_path(path):
                raise SandboxPolicyViolation("allowed host root is a forbidden system path")
            normalized.append(path)
        return tuple(normalized)

    def _validate_host_path(self, source: str) -> None:
        resolved = Path(source).resolve(strict=True)
        folded = resolved.as_posix().casefold()
        if is_forbidden_host_path(resolved):
            raise SandboxPolicyViolation("dangerous broad host mount is forbidden")
        if not self.allowed_host_roots:
            raise SandboxPolicyViolation(
                "host bind mounts require a configured allowed host root"
            )
        if not any(is_strict_descendant(resolved, root) for root in self.allowed_host_roots):
            raise SandboxPolicyViolation(
                "host path is outside the configured allowed host roots"
            )
        if any(folded.endswith(item) for item in _FORBIDDEN_SOCKET_SUFFIXES):
            raise SandboxPolicyViolation("container runtime sockets are forbidden")
        if any(part.casefold() in {".ssh", ".aws", ".config"} for part in resolved.parts):
            raise SandboxPolicyViolation("host credential/config directories are forbidden")
        if resolved.name.casefold() in {".env", "credentials", "credentials.json"}:
            raise SandboxPolicyViolation("host credential files are forbidden")
        if not resolved.is_dir() and not resolved.is_file():
            raise SandboxPolicyViolation("registered host resource is not a file or directory")

    @staticmethod
    def _validate_container_security(spec: SandboxSpec) -> None:
        if "@sha256:" not in spec.image_digest:
            raise SandboxPolicyViolation("sandbox image must be pinned by digest")
        if spec.privileged:
            raise SandboxPolicyViolation("privileged sandbox is forbidden")
        if spec.host_network or spec.host_pid or spec.host_ipc:
            raise SandboxPolicyViolation("host namespaces are forbidden")
        if spec.user.split(":", 1)[0] == "0" or spec.user.casefold() == "root":
            raise SandboxPolicyViolation("experiment sandbox must run as non-root")
        if not spec.read_only_rootfs:
            raise SandboxPolicyViolation("execution root filesystem must be read-only")
        if tuple(item.upper() for item in spec.drop_capabilities) != ("ALL",):
            raise SandboxPolicyViolation("all Linux capabilities must be dropped")
        if "no-new-privileges:true" not in spec.security_options:
            raise SandboxPolicyViolation("no-new-privileges is required")
        if spec.seccomp_profile.casefold() in {"unconfined", "none"}:
            raise SandboxPolicyViolation("seccomp cannot be disabled")
        if any("unconfined" in item.casefold() for item in spec.security_options):
            raise SandboxPolicyViolation("unconfined security options are forbidden")
        if set(spec.security_options) != {"no-new-privileges:true"}:
            raise SandboxPolicyViolation("only no-new-privileges security option is allowed")
        if spec.network_policy is SandboxNetworkPolicy.OFFLINE and spec.host_network:
            raise SandboxPolicyViolation("offline sandbox cannot use host network")
        normalized_roots = {str(_container_path(root)) for root in spec.allowed_write_roots}
        if not normalized_roots or not normalized_roots <= _ALLOWED_WRITE_ROOTS:
            raise SandboxPolicyViolation("allowed write roots exceed system policy")

    def _validate_no_recursive_submounts(self, source: str) -> None:
        normalized = Path(source).resolve(strict=True).as_posix().rstrip("/")
        for mount_point in self.host_mount_points:
            candidate = Path(mount_point).resolve(strict=False).as_posix().rstrip("/")
            if candidate != normalized and candidate.startswith(normalized + "/"):
                raise SandboxPolicyViolation(
                    "registered host resource contains a recursive submount"
                )

    @staticmethod
    def _linux_mount_points() -> tuple[str, ...]:
        if platform.system() != "Linux":
            return ()
        try:
            lines = Path("/proc/self/mountinfo").read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
        except OSError as exc:
            raise SandboxPolicyViolation(
                "cannot verify host recursive mount topology"
            ) from exc
        points = []
        for line in lines:
            fields = line.split()
            if len(fields) >= 5:
                points.append(fields[4].replace("\\040", " "))
        return tuple(points)


class SandboxPathGuard:
    """Validate artifact and coding paths without following escape symlinks."""

    @staticmethod
    def require_allowed(path: str, allowed_roots: tuple[str, ...]) -> str:
        normalized = os.path.normpath(path).replace("\\", "/")
        if not normalized.startswith("/"):
            raise SandboxPolicyViolation("path must be container-absolute")
        if not is_within(normalized, allowed_roots):
            raise SandboxPolicyViolation("path escapes approved sandbox roots")
        return normalized
