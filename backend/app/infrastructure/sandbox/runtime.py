"""Composition root for environment planning and per-run sandbox sessions."""

from __future__ import annotations

from threading import RLock

from .models import (
    MountCategory,
    RegisteredResource,
    ResourceKind,
    SandboxMount,
    SandboxNetworkPolicy,
    SandboxResourceLimits,
    SandboxSpec,
)


class SandboxSession:
    def __init__(self, handle, spec) -> None:
        self.handle = handle
        self.spec = spec


class SandboxSessionRegistry:
    def __init__(self) -> None:
        self._sessions = {}
        self._lock = RLock()

    def put(self, session: SandboxSession) -> None:
        with self._lock:
            run_id = session.handle.run_id
            if run_id in self._sessions:
                raise ValueError("sandbox session already exists")
            self._sessions[run_id] = session

    def get(self, run_id: str) -> SandboxSession:
        with self._lock:
            try:
                return self._sessions[run_id]
            except KeyError as exc:
                raise KeyError(f"sandbox session for run {run_id!r} is unavailable") from exc

    def remove(self, run_id: str) -> SandboxSession:
        with self._lock:
            return self._sessions.pop(run_id)


class SandboxRuntimeService:
    """Creates provisioning and offline execution phases over run-private volumes."""

    def __init__(
        self,
        *,
        manager,
        environment_broker,
        resource_registry,
        session_registry,
        provisioner,
        provisioning_network_resource_id: str | None = None,
    ) -> None:
        self.manager = manager
        self.environment_broker = environment_broker
        self.resource_registry = resource_registry
        self.session_registry = session_registry
        self.provisioner = provisioner
        self.provisioning_network_resource_id = provisioning_network_resource_id

    def prepare(self, context):
        if not context.repository_snapshot_id:
            raise ValueError("a registered repository snapshot is required")
        plan = self.environment_broker.resolve(context.environment_requirement)
        mounts = list(self._private_mounts(context.run_id))
        mounts.append(
            SandboxMount(
                resource_id=f"repository:{context.repository_snapshot_id}",
                target="/source/repository",
                category=MountCategory.REPOSITORY_SNAPSHOT_READ_ONLY,
                read_only=True,
            )
        )
        dataset_id = None
        if isinstance(context.dataset_requirement, dict):
            dataset_id = context.dataset_requirement.get("repository_dataset_id")
        if dataset_id:
            mounts.append(
                SandboxMount(
                    resource_id=f"dataset:{dataset_id}",
                    target="/datasets/input",
                    category=MountCategory.DATASET_READ_ONLY,
                    read_only=True,
                )
            )
        if plan.reused_environment_id:
            mounts.append(
                SandboxMount(
                    resource_id=f"environment:{plan.reused_environment_id}",
                    target="/opt/reused-env",
                    category=MountCategory.REGISTERED_ENV_READ_ONLY,
                    read_only=True,
                )
            )
        for index, cache_id in enumerate(plan.package_cache_source_ids):
            mounts.append(
                SandboxMount(
                    resource_id=f"cache:{cache_id}",
                    target=f"/seed-cache/{index}",
                    category=MountCategory.REGISTERED_PACKAGE_CACHE_READ_ONLY,
                    read_only=True,
                )
            )

        if plan.required_downloads or plan.resolved_system_packages:
            provisioning_spec = self._spec(
                context,
                plan.base_image_digest,
                tuple(mounts),
                SandboxNetworkPolicy.PROVISIONING_EGRESS,
                self.provisioning_network_resource_id,
            )
            provisioning = self.manager.create(provisioning_spec, plan)
            self.manager.start(provisioning)
            try:
                self.provisioner.provision(provisioning, plan)
                self.manager.stop(provisioning)
                self.manager.release_container(provisioning)
            except Exception:
                try:
                    self.manager.kill(provisioning)
                except Exception:
                    pass
                self.manager.cleanup(context.run_id)
                self.resource_registry.remove_run_resources(context.run_id)
                raise

        # Copy the immutable source through a short-lived offline container.
        # The final execution/OpenHands container must not retain visibility of
        # the original repository snapshot or provisioning cache mounts.
        materialization_spec = self._spec(
            context,
            plan.base_image_digest,
            tuple(mounts),
            SandboxNetworkPolicy.OFFLINE,
            None,
        )
        materializer = self.manager.create(materialization_spec, plan)
        self.manager.start(materializer)
        try:
            copied = self.manager.exec(
                materializer,
                program="cp",
                argv=("-a", "/source/repository/.", "/workspace/repository"),
                cwd="/workspace",
                timeout_seconds=300,
            )
            if copied.timed_out or copied.exit_code != 0:
                raise RuntimeError("repository snapshot materialization failed")
            self.manager.stop(materializer)
            self.manager.release_container(materializer)
        except Exception:
            try:
                self.manager.kill(materializer)
            except Exception:
                pass
            self.manager.cleanup(context.run_id)
            self.resource_registry.remove_run_resources(context.run_id)
            raise

        execution_mounts = tuple(
            mount
            for mount in mounts
            if mount.category
            not in {
                MountCategory.REPOSITORY_SNAPSHOT_READ_ONLY,
                MountCategory.REGISTERED_PACKAGE_CACHE_READ_ONLY,
            }
        )
        execution_spec = self._spec(
            context,
            plan.base_image_digest,
            execution_mounts,
            SandboxNetworkPolicy.OFFLINE,
            None,
        )
        try:
            handle = self.manager.create(execution_spec, plan)
            self.manager.start(handle)
            session = SandboxSession(handle, execution_spec)
            self.session_registry.put(session)
            return session
        except Exception:
            try:
                self.manager.cleanup(context.run_id)
            finally:
                self.resource_registry.remove_run_resources(context.run_id)
            raise

    def cleanup(self, run_id: str) -> None:
        self.session_registry.remove(run_id)
        try:
            self.manager.cleanup(run_id)
        finally:
            self.resource_registry.remove_run_resources(run_id)

    def _private_mounts(self, run_id: str):
        definitions = (
            ("workspace", "/workspace"),
            ("environment", "/sandbox-env"),
            ("cache", "/cache"),
            ("output", "/output"),
        )
        mounts = []
        try:
            for purpose, target in definitions:
                volume_id = self.manager.create_run_volume(run_id, purpose)
                resource_id = f"run:{run_id}:{purpose}"
                self.resource_registry.register(
                    RegisteredResource(
                        resource_id=resource_id,
                        kind=ResourceKind.DOCKER_VOLUME,
                        category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,
                        volume_name=volume_id,
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
        except Exception:
            try:
                self.manager.cleanup(run_id)
            except KeyError:
                pass
            finally:
                self.resource_registry.remove_run_resources(run_id)
            raise
        return mounts

    @staticmethod
    def _spec(context, image, mounts, network_policy, network_resource_id):
        requirement = context.resource_requirement
        limits = SandboxResourceLimits(
            cpu_cores=requirement.cpu_cores or 1.0,
            memory_mb=requirement.memory_mb or 4096,
            memory_swap_mb=requirement.memory_mb or 4096,
        )
        return SandboxSpec(
            run_id=context.run_id,
            experiment_id=context.experiment_id,
            image_digest=image,
            mounts=mounts,
            network_policy=network_policy,
            egress_network_resource_id=network_resource_id,
            limits=limits,
        )
