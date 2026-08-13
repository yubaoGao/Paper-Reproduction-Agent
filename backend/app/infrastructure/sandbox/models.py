"""Typed contracts for trusted Linux sandbox infrastructure."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import Field, JsonValue, model_validator

from backend.app.domain.experiment import DomainModel, NonEmptyStr, _require_aware


class EnvironmentRegistrationMode(str, Enum):
    STATIC_REGISTRY = "static_registry"
    ADMIN_DISCOVERY = "admin_discovery"


class EnvironmentArtifactType(str, Enum):
    OCI_IMAGE = "oci_image"
    CONDA_ARCHIVE = "conda_archive"
    READ_ONLY_PREFIX = "read_only_prefix"
    PACKAGE_CACHE_SOURCE = "package_cache_source"


class EnvironmentReuseStrategy(str, Enum):
    REUSED_IMAGE = "reused_image"
    REUSED_READ_ONLY_ENV = "reused_read_only_env"
    SEEDED_FROM_PACKAGE_CACHE = "seeded_from_package_cache"
    BUILT_IN_SANDBOX = "built_in_sandbox"


class CompatibilityStatus(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    PROBE_REQUIRED = "probe_required"


class SandboxNetworkPolicy(str, Enum):
    OFFLINE = "offline"
    PROVISIONING_EGRESS = "provisioning_egress"
    RESTRICTED_EGRESS = "restricted_egress"


class DockerDeploymentMode(str, Enum):
    ROOTLESS = "rootless"
    USERNS_REMAP = "userns_remap"
    STANDARD_DOCKER = "standard_docker"


class DockerCapabilityReport(DomainModel):
    deployment_mode: DockerDeploymentMode
    seccomp_available: bool
    cgroup_version: NonEmptyStr | None = None
    nvidia_runtime_available: bool = False
    warnings: tuple[NonEmptyStr, ...] = ()


class MountCategory(str, Enum):
    REGISTERED_ENV_READ_ONLY = "registered_env_read_only"
    REGISTERED_PACKAGE_CACHE_READ_ONLY = "registered_package_cache_read_only"
    REPOSITORY_SNAPSHOT_READ_ONLY = "repository_snapshot_read_only"
    DATASET_READ_ONLY = "dataset_read_only"
    CHECKPOINT_READ_ONLY = "checkpoint_read_only"
    PRETRAINED_MODEL_READ_ONLY = "pretrained_model_read_only"
    APPROVED_CONFIG_READ_ONLY = "approved_config_read_only"
    RUN_PRIVATE_VOLUME_READ_WRITE = "run_private_volume_read_write"


class ResourceKind(str, Enum):
    HOST_PATH = "host_path"
    DOCKER_VOLUME = "docker_volume"
    DOCKER_NETWORK = "docker_network"


class EnvironmentFingerprint(DomainModel):
    platform: NonEmptyStr
    architecture: NonEmptyStr
    python_version: NonEmptyStr
    python_implementation: NonEmptyStr = "cpython"
    packages: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)
    frameworks: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)
    system_packages: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)
    cuda_runtime: NonEmptyStr | None = None
    abi: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)
    content_digest: NonEmptyStr


class EnvironmentDescriptor(DomainModel):
    """Path-free environment metadata safe to expose outside infrastructure."""

    environment_id: NonEmptyStr
    artifact_type: EnvironmentArtifactType
    fingerprint: EnvironmentFingerprint
    image_digest: NonEmptyStr | None = None
    prefix_sensitive: bool = True
    probe_required: bool = False
    registration_mode: EnvironmentRegistrationMode
    metadata: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def immutable_image_has_digest(self):
        if self.artifact_type is EnvironmentArtifactType.OCI_IMAGE:
            if not self.image_digest or "@sha256:" not in self.image_digest:
                raise ValueError("OCI environment must use an immutable image digest")
        return self


class ReusableEnvironmentArtifact(DomainModel):
    artifact_id: NonEmptyStr
    artifact_type: EnvironmentArtifactType
    fingerprint: EnvironmentFingerprint
    image_digest: NonEmptyStr | None = None
    resource_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def immutable_reference(self):
        if self.artifact_type is EnvironmentArtifactType.OCI_IMAGE:
            if not self.image_digest or "@sha256:" not in self.image_digest:
                raise ValueError("reusable OCI artifact requires an image digest")
        elif self.resource_id is None:
            raise ValueError("non-image reusable artifact requires a registry ID")
        return self


class PackageCacheSource(DomainModel):
    cache_id: NonEmptyStr
    package_manager: NonEmptyStr
    fingerprint: NonEmptyStr


class CompatibilityResult(DomainModel):
    status: CompatibilityStatus
    reasons: tuple[NonEmptyStr, ...] = ()
    missing_packages: tuple[NonEmptyStr, ...] = ()


class SandboxEnvironmentPlan(DomainModel):
    strategy: EnvironmentReuseStrategy
    base_image_digest: NonEmptyStr
    reused_environment_id: NonEmptyStr | None = None
    environment_fingerprint: EnvironmentFingerprint
    package_cache_source_ids: tuple[NonEmptyStr, ...] = ()
    sandbox_private_env_path: NonEmptyStr = "/sandbox-env"
    required_downloads: tuple[NonEmptyStr, ...] = ()
    resolved_system_packages: tuple[NonEmptyStr, ...] = ()
    compatibility: CompatibilityResult
    warnings: tuple[NonEmptyStr, ...] = ()
    provenance: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)


class AssignedDeviceSet(DomainModel):
    gpu_device_ids: tuple[NonEmptyStr, ...] = ()
    gpu_lease_token: NonEmptyStr | None = None
    gpu_lease_expires_at: datetime | None = None

    @model_validator(mode="after")
    def explicitly_assigned_only(self):
        forbidden = {"all", "*", "-1"}
        if any(item.casefold() in forbidden for item in self.gpu_device_ids):
            raise ValueError("all-GPU device requests are forbidden")
        if len(set(self.gpu_device_ids)) != len(self.gpu_device_ids):
            raise ValueError("GPU device IDs must be unique")
        if self.gpu_device_ids and (self.gpu_lease_token is None or self.gpu_lease_expires_at is None):
            raise ValueError("GPU device IDs must come from an explicit GPU lease")
        if not self.gpu_device_ids and (self.gpu_lease_token is not None or self.gpu_lease_expires_at is not None):
            raise ValueError("GPU lease metadata requires allocated device IDs")
        if self.gpu_lease_expires_at is not None:
            _require_aware(self.gpu_lease_expires_at, "gpu_lease_expires_at")
        return self

    @classmethod
    def from_lease(cls, lease, *, run_id: str, step_id: str):
        if lease.run_id != run_id or lease.step_id != step_id:
            raise ValueError("GPU lease owner does not match the sandbox execution step")
        if lease.expires_at <= datetime.now(timezone.utc):
            raise ValueError("expired GPU lease cannot be passed to the sandbox runtime")
        return cls(
            gpu_device_ids=lease.allocated_gpu_ids,
            gpu_lease_token=lease.lease_token,
            gpu_lease_expires_at=lease.expires_at,
        )


class SandboxResourceLimits(DomainModel):
    cpu_cores: float = Field(default=1.0, gt=0, le=256)
    memory_mb: int = Field(default=4096, ge=128)
    memory_swap_mb: int = Field(default=4096, ge=128)
    pids_limit: int = Field(default=256, ge=16, le=32768)
    shm_size_mb: int = Field(default=64, ge=16)
    workspace_bytes: int = Field(default=20 * 1024**3, ge=1024**3)
    execution_timeout_seconds: int = Field(default=3600, gt=0)

    @model_validator(mode="after")
    def swap_not_below_memory(self):
        if self.memory_swap_mb < self.memory_mb:
            raise ValueError("memory/swap limit cannot be below memory limit")
        return self


class SandboxMount(DomainModel):
    resource_id: NonEmptyStr
    target: NonEmptyStr
    category: MountCategory
    read_only: bool


class SandboxSpec(DomainModel):
    run_id: NonEmptyStr
    experiment_id: NonEmptyStr
    image_digest: NonEmptyStr
    mounts: tuple[SandboxMount, ...]
    allowed_write_roots: tuple[NonEmptyStr, ...] = (
        "/workspace",
        "/sandbox-env",
        "/cache",
        "/output",
        "/tmp",
        "/home/sandbox",
    )
    network_policy: SandboxNetworkPolicy = SandboxNetworkPolicy.OFFLINE
    egress_network_resource_id: NonEmptyStr | None = None
    user: NonEmptyStr = "65532:65532"
    read_only_rootfs: bool = True
    drop_capabilities: tuple[NonEmptyStr, ...] = ("ALL",)
    security_options: tuple[NonEmptyStr, ...] = ("no-new-privileges:true",)
    seccomp_profile: NonEmptyStr = "default"
    privileged: bool = False
    host_pid: bool = False
    host_ipc: bool = False
    host_network: bool = False
    devices: AssignedDeviceSet = Field(default_factory=AssignedDeviceSet)
    limits: SandboxResourceLimits = Field(default_factory=SandboxResourceLimits)


class RegisteredResource(DomainModel):
    resource_id: NonEmptyStr
    kind: ResourceKind
    category: MountCategory
    host_path: NonEmptyStr | None = None
    volume_name: NonEmptyStr | None = None
    network_name: NonEmptyStr | None = None
    owner_run_id: NonEmptyStr | None = None
    metadata: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def one_location(self):
        if self.kind is ResourceKind.HOST_PATH and not self.host_path:
            raise ValueError("host-path resource requires a trusted path")
        if self.kind is ResourceKind.DOCKER_VOLUME and not self.volume_name:
            raise ValueError("volume resource requires a Docker volume name")
        if self.kind is ResourceKind.DOCKER_NETWORK and not self.network_name:
            raise ValueError("network resource requires a Docker network name")
        return self


class ResolvedMount(DomainModel):
    source: NonEmptyStr
    target: NonEmptyStr
    category: MountCategory
    read_only: bool
    kind: ResourceKind


class RunResources(DomainModel):
    run_id: NonEmptyStr
    container_ids: tuple[NonEmptyStr, ...] = ()
    volume_ids: tuple[NonEmptyStr, ...] = ()
    network_ids: tuple[NonEmptyStr, ...] = ()
    temporary_image_ids: tuple[NonEmptyStr, ...] = ()
    workspace_resource_id: NonEmptyStr | None = None
    environment_id: NonEmptyStr | None = None
    artifact_references: tuple[NonEmptyStr, ...] = ()


class SandboxAuditRecord(DomainModel):
    run_id: NonEmptyStr
    container_id: NonEmptyStr
    image_digest: NonEmptyStr
    environment_strategy: EnvironmentReuseStrategy
    environment_id: NonEmptyStr | None = None
    mount_categories: tuple[MountCategory, ...]
    resource_limits: SandboxResourceLimits
    network_policy: SandboxNetworkPolicy
    gpu_device_ids: tuple[NonEmptyStr, ...] = ()
    gpu_lease_token: NonEmptyStr | None = None
    security_options: tuple[NonEmptyStr, ...]
    started_at: datetime
    finished_at: datetime | None = None
    cleanup_result: NonEmptyStr | None = None


class SandboxExecResult(DomainModel):
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = Field(ge=0)
    timed_out: bool = False


class SandboxHandle(DomainModel):
    run_id: NonEmptyStr
    container_id: NonEmptyStr
    environment_plan: SandboxEnvironmentPlan
