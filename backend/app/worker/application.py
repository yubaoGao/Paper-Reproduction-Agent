"""Independent PostgreSQL -> GPU -> sandbox -> result worker composition root."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.domain import (
    AdaptedExecutionConfig,
    GPUDeviceState,
    GPURequirement,
    MultiGPUSemantics,
    RepositoryResourceCapabilities,
)
from backend.app.infrastructure.persistence import PostgresPersistence
from backend.app.infrastructure.sandbox import (
    DockerEngineBackend,
    DockerSandboxCommandExecutionAdapter,
    DockerSandboxWorkspaceAdapter,
    EnvironmentProvisioner,
    HostMutationGuard,
    LinuxSandboxManager,
    MountCategory,
    RegisteredResource,
    RunLogStore,
    RunResourceRegistry,
    SandboxArtifactCollectionAdapter,
    SandboxEnvironmentProbe,
    SandboxPolicyViolation,
    SandboxRuntimeService,
    SandboxSessionRegistry,
    TrustedResourceRegistry,
    ResourceKind,
)
from backend.app.infrastructure.sandbox.assets import wire_production_environment_reuse
from backend.app.orchestration import OOMAdaptationCoordinator, ReproductionOrchestrator
from backend.app.orchestration.resource_adaptation import ResourceExecutionProfile
from backend.app.orchestration.worker import ReproductionWorker
from backend.app.services import (
    CanonicalResultResolver,
    ExternalResourcePathValidator,
    ExternalResourceResolutionService,
    JobResultFinalizer,
    ProductEventPublisher,
    RepositoryResultAdapterRegistry,
    ResolvedExternalResourceProvider,
)


class PlanResourceExecutionProfileProvider:
    """Derive scheduler/adaptation truth from one authoritative persisted plan."""

    def __init__(self, plan, scheduler) -> None:
        self.plan = plan
        self.scheduler = scheduler

    def profile(self, run_id: str, step_id: str) -> ResourceExecutionProfile:
        experiment = self._experiment(step_id)
        resource = experiment.resource_requirement
        reference_count = resource.gpu_count or (1 if resource.gpu_required else 0)
        if reference_count < 1:
            raise ValueError("GPU adaptation profile requested for a CPU-only step")
        metadata = experiment.metadata
        explicit_requirement = metadata.get("gpu_requirement")
        requirement = (
            GPURequirement.model_validate(explicit_requirement)
            if isinstance(explicit_requirement, dict)
            else GPURequirement(
                reference_gpu_count=reference_count,
                preferred_gpu_count=reference_count,
                minimum_gpu_count=reference_count,
                reference_batch_size=self._batch_size(experiment),
                multi_gpu_semantics=(
                    MultiGPUSemantics.SEMANTICALLY_REQUIRED
                    if reference_count > 1
                    else MultiGPUSemantics.PERFORMANCE_ONLY
                ),
                evidence=resource.notes or ("execution_plan:resource_requirement",),
            )
        )
        raw_capabilities = metadata.get("resource_capabilities", {})
        capabilities = RepositoryResourceCapabilities.model_validate(
            raw_capabilities if isinstance(raw_capabilities, dict) else {}
        )
        lease = self.scheduler.resolve(run_id, step_id)
        allocated = 0 if lease is None else len(lease.allocated_gpu_ids)
        inventory = self.scheduler.inventory()
        inventory_count = sum(
            item.state is not GPUDeviceState.OFFLINE for item in inventory
        )
        batch_size = requirement.reference_batch_size or self._batch_size(experiment)
        return ResourceExecutionProfile(
            requirement=requirement,
            initial_config=AdaptedExecutionConfig(
                gpu_count=max(1, requirement.reference_gpu_count),
                per_gpu_batch_size=max(1, batch_size),
                gradient_accumulation_steps=requirement.gradient_accumulation_steps,
            ),
            allocated_gpu_count=allocated,
            inventory_gpu_count=inventory_count,
            capabilities=capabilities,
        )

    def _experiment(self, step_id):
        for experiment in self.plan.experiments:
            if experiment.id == step_id:
                return experiment
            if experiment.action_plan is not None and any(
                action.action_id == step_id for action in experiment.action_plan.actions
            ):
                return experiment
        raise ValueError(f"step {step_id!r} is absent from the authoritative plan")

    @staticmethod
    def _batch_size(experiment) -> int:
        for key, value in experiment.hyperparameters.items():
            normalized = key.casefold().replace("-", "_")
            if normalized in {"batch_size", "per_gpu_batch_size", "train_batch_size"}:
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    return value
        return 1


def build_production_worker(
    *,
    worker_id: str,
    database_url: str | None = None,
    result_adapters: dict | None = None,
    principal_resource_roots=None,
    base_image_digest: str | None = None,
    run_log_root: str | Path | None = None,
    docker_volume_driver: str | None = None,
    allowed_host_roots=None,
):
    """Build a real worker. No Fake, in-memory persistence, or legacy runtime."""
    url = database_url or os.environ.get("REPROPILOT_DATABASE_URL")
    image = base_image_digest or os.environ.get("REPROPILOT_BASE_IMAGE_DIGEST")
    log_root = run_log_root or os.environ.get("REPROPILOT_RUN_LOG_ROOT")
    volume_driver = docker_volume_driver or os.environ.get("REPROPILOT_DOCKER_VOLUME_DRIVER")
    data_root = None if allowed_host_roots is not None else (
        (os.environ.get("REPROPILOT_DATA_ROOT") or "").strip() or None
    )
    missing = [
        name for name, value in (
            ("REPROPILOT_DATABASE_URL", url),
            ("REPROPILOT_BASE_IMAGE_DIGEST", image),
            ("REPROPILOT_RUN_LOG_ROOT", log_root),
            ("REPROPILOT_DOCKER_VOLUME_DRIVER", volume_driver),
            ("REPROPILOT_DATA_ROOT", data_root if allowed_host_roots is None else "configured"),
        ) if not value
    ]
    if missing:
        raise RuntimeError("production worker configuration is missing: " + ", ".join(missing))

    engine = create_engine(url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    roots = (
        principal_resource_roots
        if principal_resource_roots is not None
        else _principal_resource_roots_from_env()
    )
    path_validator = ExternalResourcePathValidator(principal_roots=roots)
    # PostgresPersistence defaults to the existing NvidiaSMIInventoryProvider.
    persistence = PostgresPersistence(
        sessions, external_resource_path_validator=path_validator,
    )
    publisher = ProductEventPublisher(persistence)
    trusted = TrustedResourceRegistry()
    network_id = os.environ.get("REPROPILOT_PROVISIONING_NETWORK_ID")
    network_name = os.environ.get("REPROPILOT_PROVISIONING_NETWORK_NAME")
    if bool(network_id) != bool(network_name):
        raise RuntimeError("both provisioning network ID and name must be configured")
    if network_id and network_name:
        trusted.register(RegisteredResource(
            resource_id=network_id,
            kind=ResourceKind.DOCKER_NETWORK,
            category=MountCategory.APPROVED_CONFIG_READ_ONLY,
            network_name=network_name,
            metadata={
                "filtered_egress": True,
                "block_private_cidrs": True,
                "block_link_local": True,
                "block_cloud_metadata": True,
                "inter_container_communication": False,
            },
        ))
    host_roots = (
        allowed_host_roots
        if allowed_host_roots is not None
        else (data_root,)
    )
    try:
        mutation_guard = HostMutationGuard(trusted, allowed_host_roots=host_roots)
    except (OSError, SandboxPolicyViolation) as exc:
        raise RuntimeError(
            "REPROPILOT_DATA_ROOT must be an existing directory that is not a forbidden system path"
        ) from exc
    backend = DockerEngineBackend(quota_volume_driver=volume_driver)
    manager = LinuxSandboxManager(
        backend,
        mutation_guard,
        RunResourceRegistry(),
    )
    sandbox_sessions = SandboxSessionRegistry()
    asset_root = Path(data_root if data_root is not None else host_roots[0])
    probe = SandboxEnvironmentProbe(manager, trusted, base_image_digest=image)
    broker, promoter, store, _catalog = wire_production_environment_reuse(
        data_root=asset_root,
        resource_registry=trusted,
        base_image_digest=image,
        manager=manager,
        probe=probe,
    )
    runtime_service = SandboxRuntimeService(
        manager=manager,
        environment_broker=broker,
        resource_registry=trusted,
        session_registry=sandbox_sessions,
        provisioner=EnvironmentProvisioner(manager),
        provisioning_network_resource_id=network_id,
        gpu_lease_provider=persistence.gpu_scheduler,
        external_resource_binding_provider=persistence.resources,
        repository_snapshot_provider=persistence.repository_snapshots,
        artifact_promoter=promoter,
    )
    workspace = DockerSandboxWorkspaceAdapter(runtime_service)
    command = DockerSandboxCommandExecutionAdapter(
        manager,
        sandbox_sessions,
        log_store=RunLogStore(log_root),
    )
    artifacts = SandboxArtifactCollectionAdapter(manager, sandbox_sessions)
    adapter_registry = RepositoryResultAdapterRegistry(result_adapters)
    resolver = CanonicalResultResolver(adapter_registry)
    resource_service = ExternalResourceResolutionService(
        persistence.resources, path_validator,
    )

    def executor_factory(cancellation):
        snapshot = persistence.planning_snapshots.get_by_job(cancellation.job_id)
        job = persistence.jobs.get(cancellation.job_id)
        intake = next(
            item for item in persistence.intakes.list_by_owner(job.owner_principal)
            if item.job_id == job.job_id
        )
        external = (
            None
            if intake.resource_resolution is None
            else ResolvedExternalResourceProvider(resource_service, intake.resource_resolution)
        )
        resource_adaptation = OOMAdaptationCoordinator(
            PlanResourceExecutionProfileProvider(
                snapshot.execution_plan, persistence.gpu_scheduler,
            )
        )
        return ReproductionOrchestrator(
            repository=persistence.runs,
            command_port=command,
            workspace_port=workspace,
            artifact_port=artifacts,
            cancellation_port=cancellation,
            result_resolver=resolver,
            resource_adaptation_port=resource_adaptation,
            external_resource_reference_provider=external,
            product_event_publisher=publisher,
            job_id=job.job_id,
            owner_principal=job.owner_principal,
        )

    return ReproductionWorker(
        worker_id=worker_id,
        queue=persistence.gpu_queue,
        planning_snapshots=persistence.planning_snapshots,
        runs=persistence.runs,
        executor_factory=executor_factory,
        cleanup_port=workspace,
        gpu_resource_port=persistence.gpu_resources,
        gpu_scheduler=persistence.gpu_scheduler,
        result_finalizer=JobResultFinalizer(
            persistence, event_publisher=publisher,
        ),
        product_event_publisher=publisher,
    )


def run_worker_forever(worker, *, idle_seconds: float = 1.0) -> None:
    if idle_seconds <= 0:
        raise ValueError("idle_seconds must be positive")
    while True:
        if worker.run_once() is None:
            time.sleep(idle_seconds)


def _principal_resource_roots_from_env():
    raw = os.environ.get("REPROPILOT_PRINCIPAL_RESOURCE_ROOTS_JSON", "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("REPROPILOT_PRINCIPAL_RESOURCE_ROOTS_JSON must be valid JSON") from exc
    if not isinstance(value, dict) or any(
        not isinstance(principal, str)
        or not isinstance(roots, list)
        or any(not isinstance(root, str) for root in roots)
        for principal, roots in value.items()
    ):
        raise RuntimeError(
            "REPROPILOT_PRINCIPAL_RESOURCE_ROOTS_JSON must map principals to path lists"
        )
    return value
