"""Docker Engine SDK based Linux sandbox lifecycle management."""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timezone

from .models import (
    DockerCapabilityReport,
    DockerDeploymentMode,
    EnvironmentReuseStrategy,
    ResolvedMount,
    ResourceKind,
    RunResources,
    SandboxAuditRecord,
    SandboxExecResult,
    SandboxHandle,
    SandboxSpec,
)
from .policy import HostMutationGuard, SandboxPathGuard
from .registry import RunResourceRegistry


class SandboxRuntimeUnavailableError(RuntimeError):
    pass


class DockerEngineBackend:
    """Thin structured Docker SDK boundary; no CLI or shell interpolation."""

    def __init__(
        self,
        client=None,
        *,
        quota_volume_driver: str | None = None,
    ) -> None:
        if client is None:
            try:
                import docker
            except ImportError as exc:
                raise SandboxRuntimeUnavailableError(
                    "Docker SDK is not installed; install the production sandbox extra"
                ) from exc
            client = docker.from_env()
        self.client = client
        self.quota_volume_driver = quota_volume_driver

    def create_container(self, spec, mounts, network_name):
        volumes = {
            mount.source: {
                "bind": mount.target,
                "mode": "ro" if mount.read_only else "rw",
            }
            for mount in mounts
        }
        device_requests = []
        if spec.devices.gpu_device_ids:
            from docker.types import DeviceRequest

            device_requests.append(
                DeviceRequest(
                    device_ids=list(spec.devices.gpu_device_ids),
                    capabilities=[["gpu"]],
                )
            )
        security_options = list(spec.security_options)
        if spec.seccomp_profile != "default":
            security_options.append(f"seccomp={spec.seccomp_profile}")
        container = self.client.containers.create(
            image=spec.image_digest,
            command=["sleep", "infinity"],
            detach=True,
            name=_resource_name("sandbox", spec.run_id),
            labels={
                "paperrepro.managed": "true",
                "paperrepro.run_id": spec.run_id,
                "paperrepro.experiment_id": spec.experiment_id,
            },
            user=spec.user,
            read_only=True,
            privileged=False,
            cap_drop=["ALL"],
            security_opt=security_options,
            network_mode="none" if network_name is None else network_name,
            pid_mode=None,
            ipc_mode="private",
            pids_limit=spec.limits.pids_limit,
            nano_cpus=int(spec.limits.cpu_cores * 1_000_000_000),
            mem_limit=f"{spec.limits.memory_mb}m",
            memswap_limit=f"{spec.limits.memory_swap_mb}m",
            shm_size=f"{spec.limits.shm_size_mb}m",
            volumes=volumes,
            tmpfs={
                "/tmp": "rw,noexec,nosuid,nodev,size=512m",
                "/home/sandbox": "rw,nosuid,nodev,size=256m",
            },
            device_requests=device_requests,
            environment={
                "HOME": "/home/sandbox",
                "PATH": (
                    "/sandbox-env/venv/bin:/sandbox-env/sysroot/bin:"
                    "/opt/reused-env/bin:"
                    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                ),
                "LD_LIBRARY_PATH": (
                    "/sandbox-env/sysroot/lib:/sandbox-env/sysroot/lib64:"
                    "/opt/reused-env/lib:/opt/reused-env/lib64"
                ),
                "XDG_CACHE_HOME": "/cache/xdg",
                "PYTHONPYCACHEPREFIX": "/cache/pycache",
                "MPLCONFIGDIR": "/cache/matplotlib",
                "HF_HOME": "/cache/huggingface",
                "TORCH_HOME": "/cache/torch",
                "PIP_CACHE_DIR": "/cache/pip",
                "CONDA_PKGS_DIRS": "/cache/conda",
            },
        )
        return container.id

    def capability_report(self):
        info = self.client.info()
        security = tuple(str(item).casefold() for item in info.get("SecurityOptions", ()))
        if any("rootless" in item for item in security):
            mode = DockerDeploymentMode.ROOTLESS
        elif any("userns" in item for item in security):
            mode = DockerDeploymentMode.USERNS_REMAP
        else:
            mode = DockerDeploymentMode.STANDARD_DOCKER
        runtimes = info.get("Runtimes", {})
        warnings = () if mode is not DockerDeploymentMode.STANDARD_DOCKER else (
            "rootless Docker or userns-remap is recommended for production",
        )
        return DockerCapabilityReport(
            deployment_mode=mode,
            seccomp_available=any("seccomp" in item for item in security),
            cgroup_version=str(info.get("CgroupVersion")) if info.get("CgroupVersion") else None,
            nvidia_runtime_available="nvidia" in runtimes,
            warnings=warnings,
        )

    def start(self, container_id):
        self.client.containers.get(container_id).start()

    def stop(self, container_id, timeout):
        self.client.containers.get(container_id).stop(timeout=timeout)

    def kill(self, container_id):
        self.client.containers.get(container_id).kill()

    def remove_container(self, container_id):
        self.client.containers.get(container_id).remove(force=True, v=False)

    def inspect(self, container_id):
        container = self.client.containers.get(container_id)
        container.reload()
        return dict(container.attrs)

    def exec(self, container_id, argv, cwd, environment):
        container = self.client.containers.get(container_id)
        result = container.exec_run(
            cmd=list(argv),
            workdir=cwd,
            environment=environment,
            demux=True,
            stdout=True,
            stderr=True,
            privileged=False,
            user="65532:65532",
        )
        stdout, stderr = result.output or (b"", b"")
        return result.exit_code, stdout or b"", stderr or b""

    def create_volume(self, run_id, purpose, size_bytes):
        if not self.quota_volume_driver:
            raise SandboxRuntimeUnavailableError(
                "a quota-enforcing Docker volume driver must be configured"
            )
        volume = self.client.volumes.create(
            name=_resource_name(purpose, run_id),
            driver=self.quota_volume_driver,
            driver_opts={"size": str(size_bytes), "uid": "65532", "gid": "65532"},
            labels={
                "paperrepro.managed": "true",
                "paperrepro.run_id": run_id,
                "paperrepro.purpose": purpose,
            },
        )
        return volume.name

    def remove_volume(self, volume_id):
        self.client.volumes.get(volume_id).remove(force=False)

    def remove_network(self, network_id):
        self.client.networks.get(network_id).remove()

    def remove_image(self, image_id):
        self.client.images.remove(image=image_id, force=False, noprune=True)


def _resource_name(prefix: str, run_id: str) -> str:
    import hashlib

    suffix = hashlib.sha256(run_id.encode()).hexdigest()[:20]
    return f"paperrepro-{prefix}-{suffix}"


class LinuxSandboxManager:
    """Trusted lifecycle owner; cleanup is registry-bounded and never global."""

    def __init__(
        self,
        backend: DockerEngineBackend,
        mutation_guard: HostMutationGuard,
        run_registry: RunResourceRegistry,
        *,
        output_limit_bytes: int = 2 * 1024 * 1024,
        default_volume_limit_bytes: int = 20 * 1024**3,
    ) -> None:
        self.backend = backend
        self.mutation_guard = mutation_guard
        self.run_registry = run_registry
        self.output_limit_bytes = output_limit_bytes
        self.default_volume_limit_bytes = default_volume_limit_bytes
        self._audits: dict[str, SandboxAuditRecord] = {}

    def create(
        self,
        spec: SandboxSpec,
        environment_plan,
    ) -> SandboxHandle:
        mounts = self.mutation_guard.validate_and_resolve(spec)
        network_name = self.mutation_guard.resolve_network(spec)
        try:
            current = self.run_registry.get(spec.run_id)
        except KeyError:
            current = self.run_registry.create(spec.run_id)
        container_id = self.backend.create_container(spec, mounts, network_name)
        self.run_registry.update(
            spec.run_id,
            container_ids=(*current.container_ids, container_id),
            environment_id=environment_plan.reused_environment_id,
        )
        self._audits[spec.run_id] = SandboxAuditRecord(
            run_id=spec.run_id,
            container_id=container_id,
            image_digest=spec.image_digest,
            environment_strategy=environment_plan.strategy,
            environment_id=environment_plan.reused_environment_id,
            mount_categories=tuple(item.category for item in mounts),
            resource_limits=spec.limits,
            network_policy=spec.network_policy,
            gpu_device_ids=spec.devices.gpu_device_ids,
            gpu_lease_token=spec.devices.gpu_lease_token,
            security_options=spec.security_options,
            started_at=datetime.now(timezone.utc),
        )
        return SandboxHandle(
            run_id=spec.run_id,
            container_id=container_id,
            environment_plan=environment_plan,
        )

    def capabilities(self) -> DockerCapabilityReport:
        return self.backend.capability_report()

    def start(self, handle: SandboxHandle) -> None:
        self.backend.start(handle.container_id)

    def exec(
        self,
        handle: SandboxHandle,
        *,
        program: str,
        argv: tuple[str, ...] = (),
        cwd: str = "/workspace/repository",
        environment: dict[str, str] | None = None,
        timeout_seconds: int,
    ) -> SandboxExecResult:
        SandboxPathGuard.require_allowed(
            cwd,
            ("/workspace", "/sandbox-env", "/cache", "/output", "/tmp"),
        )
        started = time.monotonic()
        result_queue = queue.Queue(maxsize=1)

        def invoke():
            try:
                result_queue.put(
                    self.backend.exec(
                        handle.container_id,
                        (program, *argv),
                        cwd,
                        dict(environment or {}),
                    )
                )
            except BaseException as exc:
                result_queue.put(exc)

        worker = threading.Thread(target=invoke, daemon=True)
        worker.start()
        worker.join(timeout_seconds)
        duration = time.monotonic() - started
        if worker.is_alive():
            self.backend.kill(handle.container_id)
            return SandboxExecResult(duration_seconds=duration, timed_out=True)
        value = result_queue.get_nowait()
        if isinstance(value, BaseException):
            raise value
        exit_code, stdout, stderr = value
        return SandboxExecResult(
            exit_code=exit_code,
            stdout=self._decode(stdout),
            stderr=self._decode(stderr),
            duration_seconds=duration,
        )

    def stop(self, handle: SandboxHandle, timeout_seconds: int = 10) -> None:
        self.backend.stop(handle.container_id, timeout_seconds)

    def kill(self, handle: SandboxHandle) -> None:
        self.backend.kill(handle.container_id)

    def inspect(self, handle: SandboxHandle) -> dict:
        return self.backend.inspect(handle.container_id)

    def create_run_volume(
        self,
        run_id: str,
        purpose: str,
        size_bytes: int | None = None,
    ) -> str:
        try:
            current = self.run_registry.get(run_id)
        except KeyError:
            current = self.run_registry.create(run_id)
        volume_id = self.backend.create_volume(
            run_id,
            purpose,
            size_bytes or self.default_volume_limit_bytes,
        )
        self.run_registry.update(
            run_id,
            volume_ids=(*current.volume_ids, volume_id),
        )
        return volume_id

    def cleanup(self, run_id: str) -> None:
        resources = self.run_registry.get(run_id)
        errors = []
        for container_id in reversed(resources.container_ids):
            try:
                self.backend.remove_container(container_id)
            except Exception as exc:
                errors.append(f"container {container_id}: {exc}")
        for network_id in reversed(resources.network_ids):
            try:
                self.backend.remove_network(network_id)
            except Exception as exc:
                errors.append(f"network {network_id}: {exc}")
        for volume_id in reversed(resources.volume_ids):
            try:
                self.backend.remove_volume(volume_id)
            except Exception as exc:
                errors.append(f"volume {volume_id}: {exc}")
        for image_id in reversed(resources.temporary_image_ids):
            try:
                self.backend.remove_image(image_id)
            except Exception as exc:
                errors.append(f"image {image_id}: {exc}")
        self.run_registry.remove(run_id)
        audit = self._audits.get(run_id)
        if audit is not None:
            self._audits[run_id] = audit.model_copy(
                update={
                    "finished_at": datetime.now(timezone.utc),
                    "cleanup_result": "success" if not errors else "; ".join(errors),
                }
            )
        if errors:
            raise RuntimeError("sandbox cleanup incomplete: " + "; ".join(errors))

    def release_container(self, handle: SandboxHandle) -> None:
        """Remove one exact phase container while retaining run-owned volumes."""
        self.backend.remove_container(handle.container_id)
        resources = self.run_registry.get(handle.run_id)
        self.run_registry.update(
            handle.run_id,
            container_ids=tuple(
                item for item in resources.container_ids if item != handle.container_id
            ),
        )

    def audit(self, run_id: str) -> SandboxAuditRecord:
        return self._audits[run_id]

    def _decode(self, value: bytes) -> str:
        return value[: self.output_limit_bytes].decode("utf-8", errors="replace")
