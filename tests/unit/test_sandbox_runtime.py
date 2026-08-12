import json
import shutil
import time
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.app.domain import ArtifactKind, EnvironmentRequirement
from backend.app.infrastructure.sandbox import *
from backend.app.infrastructure.sandbox.environment import RegisteredEnvironment
from backend.app.infrastructure.sandbox.models import CompatibilityStatus
from backend.app.runtime.curie_models import (
    CodingRequest,
    CommandExecutionRequest,
    ExecutionStatus,
)


IMAGE = "paperrepro/runtime@sha256:" + "a" * 64
ROOT = Path("tests/.sandbox-runtime-fixture").resolve()


def fingerprint(**changes):
    values = dict(
        platform_name="linux",
        architecture="x86_64",
        python_version="3.11.9",
        packages={"torch": "2.4.0", "numpy": "2.0.0"},
        frameworks={"torch": "2.4.0"},
        cuda_runtime="12.1",
    )
    values.update(changes)
    return environment_fingerprint(**values)


def descriptor(kind=EnvironmentArtifactType.READ_ONLY_PREFIX, **changes):
    values = dict(
        environment_id="env-1",
        artifact_type=kind,
        fingerprint=fingerprint(),
        image_digest=IMAGE if kind is EnvironmentArtifactType.OCI_IMAGE else None,
        prefix_sensitive=False,
        probe_required=False,
        registration_mode=EnvironmentRegistrationMode.STATIC_REGISTRY,
    )
    values.update(changes)
    return EnvironmentDescriptor(**values)


def plan(strategy=EnvironmentReuseStrategy.REUSED_IMAGE):
    return SandboxEnvironmentPlan(
        strategy=strategy,
        base_image_digest=IMAGE,
        reused_environment_id="env-1" if strategy.name.startswith("REUSED") else None,
        environment_fingerprint=fingerprint(),
        compatibility=CompatibilityResult(
            status=CompatibilityStatus.COMPATIBLE,
            reasons=("test",),
        ),
    )


class Backend:
    def __init__(self):
        self.created = []
        self.started = []
        self.removed_containers = []
        self.removed_volumes = []
        self.removed_networks = []
        self.removed_images = []
        self.exec_values = [(0, b"ok", b"")]
        self.exec_calls = []
        self.sleep = 0

    def create_container(self, spec, mounts, network):
        self.created.append((spec, mounts, network))
        return f"container-{len(self.created)}"

    def start(self, value): self.started.append(value)
    def stop(self, value, timeout): pass
    def kill(self, value): self.killed = value
    def inspect(self, value): return {"Id": value}
    def exec(self, container, argv, cwd, environment):
        self.exec_calls.append((container, argv, cwd, environment))
        if self.sleep: time.sleep(self.sleep)
        return self.exec_values.pop(0)
    def create_volume(self, run_id, purpose, size_bytes): return f"volume-{run_id}-{purpose}"
    def remove_container(self, value): self.removed_containers.append(value)
    def remove_volume(self, value): self.removed_volumes.append(value)
    def remove_network(self, value): self.removed_networks.append(value)
    def remove_image(self, value): self.removed_images.append(value)


class Sessions:
    def __init__(self, session): self.session = session
    def get(self, run_id):
        assert run_id == self.session.handle.run_id
        return self.session


class SecretValues:
    def resolve(self, run_id, names): return {name: "top-secret" for name in names}


class LogRefs:
    def __init__(self): self.values=[]
    def write(self, run_id, command_id, stream, value):self.values.append(value);return f"run-log://{run_id}/{stream}"


class SandboxRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ROOT.mkdir(parents=True, exist_ok=True)
        for name in ("repo", "dataset", "env", "cache"):
            (ROOT / name).mkdir(exist_ok=True)
        (ROOT / "docker.sock").touch()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(ROOT, ignore_errors=True)

    def resources(self):
        return TrustedResourceRegistry(
            (
                RegisteredResource(resource_id="repo",kind=ResourceKind.HOST_PATH,category=MountCategory.REPOSITORY_SNAPSHOT_READ_ONLY,host_path=str(ROOT/"repo")),
                RegisteredResource(resource_id="dataset",kind=ResourceKind.HOST_PATH,category=MountCategory.DATASET_READ_ONLY,host_path=str(ROOT/"dataset")),
                RegisteredResource(resource_id="env",kind=ResourceKind.HOST_PATH,category=MountCategory.REGISTERED_ENV_READ_ONLY,host_path=str(ROOT/"env")),
                RegisteredResource(resource_id="cache",kind=ResourceKind.HOST_PATH,category=MountCategory.REGISTERED_PACKAGE_CACHE_READ_ONLY,host_path=str(ROOT/"cache")),
                RegisteredResource(resource_id="work",kind=ResourceKind.DOCKER_VOLUME,category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,volume_name="volume-r1",owner_run_id="r1"),
                RegisteredResource(resource_id="egress",kind=ResourceKind.DOCKER_NETWORK,category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,network_name="filtered-egress",metadata={"filtered_egress":True,"block_private_cidrs":True,"block_link_local":True,"block_cloud_metadata":True,"inter_container_communication":False}),
            )
        )

    def spec(self, mounts=(), **changes):
        values = dict(run_id="r1",experiment_id="e1",image_digest=IMAGE,mounts=tuple(mounts))
        values.update(changes)
        return SandboxSpec(**values)

    def test_01_fingerprint_stable(self):
        self.assertEqual(fingerprint().content_digest, fingerprint().content_digest)

    def test_02_fingerprint_changes_with_packages(self):
        self.assertNotEqual(fingerprint().content_digest,fingerprint(packages={"torch":"2.5"}).content_digest)

    def test_03_image_requires_digest(self):
        with self.assertRaises(ValidationError):descriptor(EnvironmentArtifactType.OCI_IMAGE,image_digest="mutable:latest")

    def test_04_all_gpu_forbidden(self):
        with self.assertRaises(ValidationError):AssignedDeviceSet(gpu_device_ids=("all",))

    def test_05_no_gpu_is_explicit_empty(self):self.assertEqual(AssignedDeviceSet().gpu_device_ids,())
    def test_06_swap_limit(self):
        with self.assertRaises(ValidationError):SandboxResourceLimits(memory_mb=1024,memory_swap_mb=512)

    def test_07_registered_repository_read_only(self):
        guard=HostMutationGuard(self.resources());mount=SandboxMount(resource_id="repo",target="/source/repository",category=MountCategory.REPOSITORY_SNAPSHOT_READ_ONLY,read_only=True)
        self.assertTrue(guard.validate_and_resolve(self.spec((mount,)))[0].read_only)

    def test_08_shared_repository_rw_denied(self):
        guard=HostMutationGuard(self.resources());mount=SandboxMount(resource_id="repo",target="/source/repository",category=MountCategory.REPOSITORY_SNAPSHOT_READ_ONLY,read_only=False)
        with self.assertRaises(SandboxPolicyViolation):guard.validate_and_resolve(self.spec((mount,)))

    def test_09_run_volume_owner_enforced(self):
        mount=SandboxMount(resource_id="work",target="/workspace",category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,read_only=False)
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec((mount,),run_id="other"))

    def test_10_arbitrary_resource_denied(self):
        mount=SandboxMount(resource_id="unknown",target="/workspace",category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,read_only=False)
        with self.assertRaises(ValueError):HostMutationGuard(self.resources()).validate_and_resolve(self.spec((mount,)))

    def test_11_privileged_denied(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(privileged=True))
    def test_12_host_network_denied(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(host_network=True))
    def test_13_host_pid_denied(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(host_pid=True))
    def test_14_host_ipc_denied(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(host_ipc=True))
    def test_15_root_denied(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(user="0:0"))
    def test_16_writable_rootfs_denied(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(read_only_rootfs=False))
    def test_17_capability_addition_denied(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(drop_capabilities=("NET_ADMIN",)))
    def test_18_no_new_privileges_required(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(security_options=("label=disable",)))
    def test_19_seccomp_unconfined_denied(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(seccomp_profile="unconfined"))
    def test_20_mutable_image_denied(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(image_digest="latest"))
    def test_21_offline_has_no_network(self):self.assertIsNone(HostMutationGuard(self.resources()).resolve_network(self.spec()))
    def test_22_egress_requires_registered_network(self):
        spec=self.spec(network_policy=SandboxNetworkPolicy.PROVISIONING_EGRESS)
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).resolve_network(spec)
    def test_23_filtered_network_resolved(self):
        spec=self.spec(network_policy=SandboxNetworkPolicy.PROVISIONING_EGRESS,egress_network_resource_id="egress")
        self.assertEqual(HostMutationGuard(self.resources()).resolve_network(spec),"filtered-egress")
    def test_24_path_traversal_denied(self):
        with self.assertRaises(SandboxPolicyViolation):SandboxPathGuard.require_allowed("/output/../etc/passwd",("/output",))
    def test_25_sibling_run_path_denied(self):
        with self.assertRaises(SandboxPolicyViolation):SandboxPathGuard.require_allowed("/runs/b/work",("/runs/a",))

    def test_26_environment_compatible(self):
        broker=EnvironmentBroker(HostEnvironmentCatalog(),base_image_digest=IMAGE)
        req=EnvironmentRequirement(python_constraint=">=3.11,<3.12",dependencies=("torch==2.4.0",),frameworks=("torch",),cuda_hints=("12.1",))
        self.assertEqual(broker.compatibility(req,fingerprint()).status,CompatibilityStatus.COMPATIBLE)

    def test_27_python_mismatch(self):
        broker=EnvironmentBroker(HostEnvironmentCatalog(),base_image_digest=IMAGE)
        self.assertEqual(broker.compatibility(EnvironmentRequirement(python_constraint=">=3.12"),fingerprint()).status,CompatibilityStatus.INCOMPATIBLE)
    def test_28_package_mismatch(self):
        broker=EnvironmentBroker(HostEnvironmentCatalog(),base_image_digest=IMAGE)
        result=broker.compatibility(EnvironmentRequirement(dependencies=("torch==9",)),fingerprint());self.assertIn("torch==9",result.missing_packages)
    def test_29_cuda_mismatch(self):
        broker=EnvironmentBroker(HostEnvironmentCatalog(),base_image_digest=IMAGE)
        self.assertEqual(broker.compatibility(EnvironmentRequirement(cuda_hints=("11.8",)),fingerprint()).status,CompatibilityStatus.INCOMPATIBLE)

    def test_30_image_reuse_first(self):
        image=descriptor(EnvironmentArtifactType.OCI_IMAGE)
        broker=EnvironmentBroker(HostEnvironmentCatalog(),base_image_digest=IMAGE,image_cache=SandboxImageCache((image,)))
        self.assertEqual(broker.resolve(EnvironmentRequirement(dependencies=("torch==2.4.0",))).strategy,EnvironmentReuseStrategy.REUSED_IMAGE)

    def test_31_readonly_env_probe_pass(self):
        catalog=HostEnvironmentCatalog();item=descriptor(probe_required=True);catalog.register_static(item,"env")
        broker=EnvironmentBroker(catalog,base_image_digest=IMAGE,probe=lambda value:True)
        self.assertEqual(broker.resolve(EnvironmentRequirement(dependencies=("torch==2.4.0",))).strategy,EnvironmentReuseStrategy.REUSED_READ_ONLY_ENV)

    def test_32_probe_failure_falls_back(self):
        catalog=HostEnvironmentCatalog();catalog.register_static(descriptor(probe_required=True),"env")
        broker=EnvironmentBroker(catalog,base_image_digest=IMAGE,probe=lambda value:False)
        self.assertEqual(broker.resolve(EnvironmentRequirement()).strategy,EnvironmentReuseStrategy.BUILT_IN_SANDBOX)

    def test_33_cache_seed(self):
        cache=PackageCacheSource(cache_id="pip",package_manager="pip",fingerprint="sha256:x")
        broker=EnvironmentBroker(HostEnvironmentCatalog(),base_image_digest=IMAGE,package_caches=(cache,))
        self.assertEqual(broker.resolve(EnvironmentRequirement(dependencies=("numpy",))).strategy,EnvironmentReuseStrategy.SEEDED_FROM_PACKAGE_CACHE)

    def test_34_cache_miss_download(self):
        broker=EnvironmentBroker(HostEnvironmentCatalog(),base_image_digest=IMAGE)
        result=broker.resolve(EnvironmentRequirement(dependencies=("numpy",)));self.assertEqual(result.strategy,EnvironmentReuseStrategy.BUILT_IN_SANDBOX);self.assertEqual(result.required_downloads,("numpy",))

    def test_35_static_inspector_no_activation(self):
        prefix=ROOT/"static-env";meta=prefix/"conda-meta";meta.mkdir(parents=True,exist_ok=True);(meta/"python.json").write_text(json.dumps({"name":"python","version":"3.11.8"}));(meta/"torch.json").write_text(json.dumps({"name":"torch","version":"2.4.0"}))
        value=StaticEnvironmentInspector().inspect(prefix);self.assertEqual(value.python_version,"3.11.8");self.assertEqual(value.frameworks["torch"],"2.4.0")

    def manager(self, backend=None):
        backend=backend or Backend();registry=RunResourceRegistry();manager=LinuxSandboxManager(backend,HostMutationGuard(self.resources()),registry);return manager,backend,registry

    def test_36_manager_creates_hardened_spec(self):
        manager,backend,_=self.manager();handle=manager.create(self.spec(),plan());manager.start(handle);self.assertEqual(backend.created[0][0].drop_capabilities,("ALL",));self.assertEqual(backend.started,["container-1"])
    def test_37_exec_uses_argv(self):
        manager,backend,_=self.manager();handle=manager.create(self.spec(),plan());result=manager.exec(handle,program="python",argv=("train.py",),timeout_seconds=1);self.assertEqual(result.stdout,"ok")
    def test_38_timeout_kills_only_container(self):
        backend=Backend();backend.sleep=.05;manager,_,_=self.manager(backend);handle=manager.create(self.spec(),plan());result=manager.exec(handle,program="python",timeout_seconds=.001);self.assertTrue(result.timed_out);self.assertEqual(backend.killed,"container-1")
    def test_39_cleanup_exact_resources(self):
        manager,backend,registry=self.manager();handle=manager.create(self.spec(),plan());registry.update("r1",volume_ids=("v1",),network_ids=("n1",),temporary_image_ids=("i1",));manager.cleanup("r1");self.assertEqual(backend.removed_containers,[handle.container_id]);self.assertEqual(backend.removed_volumes,["v1"]);self.assertEqual(backend.removed_networks,["n1"]);self.assertEqual(backend.removed_images,["i1"])
    def test_40_no_global_cleanup_method(self):
        manager,_,_=self.manager();self.assertFalse(hasattr(manager,"prune"))

    def session(self, backend=None):
        manager,backend,_=self.manager(backend);handle=manager.create(self.spec(),plan());return manager,backend,Sessions(SandboxSession(handle,self.spec()))

    def test_41_command_success_mapping(self):
        manager,_,sessions=self.session();adapter=DockerSandboxCommandExecutionAdapter(manager,sessions,log_store=self.log_store());request=CommandExecutionRequest(run_id="r1",experiment_id="e1",command_id="c",program="python",argv=("x.py",),working_directory_reference="/workspace/repository",timeout_seconds=5);result=adapter.execute(request);self.assertEqual(result.status,ExecutionStatus.SUCCEEDED);self.assertTrue(result.stdout_reference.startswith("run-log://"))
    def test_42_secret_redaction(self):
        backend=Backend();backend.exec_values=[(0,b"top-secret",b"")];manager,_,sessions=self.session(backend);adapter=DockerSandboxCommandExecutionAdapter(manager,sessions,log_store=self.log_store(),secret_provider=SecretValues());request=CommandExecutionRequest(run_id="r1",experiment_id="e1",command_id="c",program="python",working_directory_reference="/workspace/repository",environment_references=("TOKEN",),timeout_seconds=5);self.assertEqual(adapter.execute(request).stdout,"[REDACTED]")
    def test_43_missing_secret_provider_fails(self):
        manager,_,sessions=self.session();adapter=DockerSandboxCommandExecutionAdapter(manager,sessions,log_store=self.log_store());request=CommandExecutionRequest(run_id="r1",experiment_id="e1",command_id="c",program="python",working_directory_reference="/workspace/repository",environment_references=("TOKEN",),timeout_seconds=5)
        with self.assertRaises(RuntimeError):adapter.execute(request)
    def test_44_artifact_collection_approved_root(self):
        backend=Backend();backend.exec_values=[(0,b"/output/result.json\x0012\x00",b""),(1,b"",b"")];manager,_,sessions=self.session(backend);adapter=SandboxArtifactCollectionAdapter(manager,sessions);context=type("C",(),{"run_id":"r1"})();items=adapter.collect(context,None);self.assertEqual(items[0].kind,ArtifactKind.RESULT);self.assertTrue(items[0].uri.startswith("sandbox://r1/output"))

    def test_45_openhands_workspace_edit_mapping(self):
        value={"patch_id":"p1","summary":"fixed import","changed_categories":["import"],"changed_paths":["/workspace/repository/a.py"],"proposed_values":{},"patch_path":"/workspace/repository/.paperrepro/p.patch"}
        backend=Backend();backend.exec_values=[(0,json.dumps(value).encode(),b"")];manager,_,sessions=self.session(backend);adapter=OpenHandsCodingAgentAdapter(SandboxedOpenHandsController(manager,sessions));request=CodingRequest(run_id="r1",experiment_id="e1",instruction="fix",allowed_change_categories=("import",),locked_constraint_keys=("dataset",));result=adapter.apply(request);self.assertEqual(result.patch_id,"p1")
    def test_46_openhands_escape_denied(self):
        value={"patch_id":"p","summary":"x","changed_paths":["/dataset/x"]};backend=Backend();backend.exec_values=[(0,json.dumps(value).encode(),b"")];manager,_,sessions=self.session(backend);controller=SandboxedOpenHandsController(manager,sessions)
        with self.assertRaises(SandboxPolicyViolation):controller.run(CodingRequest(run_id="r1",experiment_id="e1",instruction="x",allowed_change_categories=("path",),locked_constraint_keys=("dataset",)))
    def test_47_openhands_cannot_request_mounts(self):
        value={"patch_id":"p","summary":"x","changed_paths":[],"mounts":["/"]};backend=Backend();backend.exec_values=[(0,json.dumps(value).encode(),b"")];manager,_,sessions=self.session(backend);controller=SandboxedOpenHandsController(manager,sessions)
        with self.assertRaises(OpenHandsExecutionError):controller.run(CodingRequest(run_id="r1",experiment_id="e1",instruction="x",allowed_change_categories=("path",),locked_constraint_keys=("dataset",)))
    def test_48_openhands_timeout(self):
        backend=Backend();backend.sleep=.05;manager,_,sessions=self.session(backend);controller=SandboxedOpenHandsController(manager,sessions,timeout_seconds=.001)
        with self.assertRaises(OpenHandsExecutionError):controller.run(CodingRequest(run_id="r1",experiment_id="e1",instruction="x",allowed_change_categories=("path",),locked_constraint_keys=("dataset",)))
    def test_49_requirements_manifest(self):
        self.assertEqual(DependencyManifestParser().requirements_txt("torch==2.4\n# x\nnumpy>=2"),("torch==2.4","numpy>=2"))
    def test_50_requirements_options_denied(self):
        with self.assertRaises(DependencyManifestError):DependencyManifestParser().requirements_txt("-r remote.txt")
    def test_51_pyproject_manifest(self):
        content=b'[project]\nname="x"\ndependencies=["torch==2.4"]\n';self.assertEqual(DependencyManifestParser().pyproject(content),("torch==2.4",))
    def test_52_environment_yml_manifest(self):
        content="dependencies:\n  - python=3.11\n  - pip:\n      - torch==2.4\n";self.assertEqual(DependencyManifestParser().environment_yml(content),("python==3.11","torch==2.4"))
    def test_53_reusable_prefix_requires_registry_id(self):
        with self.assertRaises(ValidationError):ReusableEnvironmentArtifact(artifact_id="a",artifact_type=EnvironmentArtifactType.READ_ONLY_PREFIX,fingerprint=fingerprint())
    def test_54_reusable_image_is_digest_pinned(self):
        item=ReusableEnvironmentArtifact(artifact_id="a",artifact_type=EnvironmentArtifactType.OCI_IMAGE,fingerprint=fingerprint(),image_digest=IMAGE);self.assertEqual(item.image_digest,IMAGE)
    def test_55_duplicate_mount_target_denied(self):
        a=SandboxMount(resource_id="repo",target="/source",category=MountCategory.REPOSITORY_SNAPSHOT_READ_ONLY,read_only=True);b=SandboxMount(resource_id="dataset",target="/source",category=MountCategory.DATASET_READ_ONLY,read_only=True)
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec((a,b)))
    def test_56_write_outside_allowed_roots_denied(self):
        mount=SandboxMount(resource_id="work",target="/unapproved",category=MountCategory.RUN_PRIVATE_VOLUME_READ_WRITE,read_only=False)
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec((mount,)))
    def test_57_unapproved_system_package_denied(self):
        broker=EnvironmentBroker(HostEnvironmentCatalog(),base_image_digest=IMAGE)
        with self.assertRaises(ValueError):broker.resolve(EnvironmentRequirement(system_dependencies=("ffmpeg",)))
    def test_58_system_package_is_admin_resolved(self):
        resolver=TrustedSystemPackageResolver({"ffmpeg":"conda:ffmpeg=7.0"});broker=EnvironmentBroker(HostEnvironmentCatalog(),base_image_digest=IMAGE,system_package_resolver=resolver);result=broker.resolve(EnvironmentRequirement(system_dependencies=("ffmpeg",)));self.assertEqual(result.resolved_system_packages,("conda:ffmpeg=7.0",))
    def test_59_custom_write_root_denied(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(allowed_write_roots=("/host",)))
    def test_60_readonly_mount_target_is_category_scoped(self):
        mount=SandboxMount(resource_id="repo",target="/etc/repository",category=MountCategory.REPOSITORY_SNAPSHOT_READ_ONLY,read_only=True)
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec((mount,)))
    def test_61_recursive_submount_denied(self):
        mount=SandboxMount(resource_id="env",target="/opt/reused-env",category=MountCategory.REGISTERED_ENV_READ_ONLY,read_only=True);guard=HostMutationGuard(self.resources(),host_mount_points=(str(ROOT/"env"/"nested"),))
        with self.assertRaises(SandboxPolicyViolation):guard.validate_and_resolve(self.spec((mount,)))
    def test_62_extra_security_option_denied(self):
        with self.assertRaises(SandboxPolicyViolation):HostMutationGuard(self.resources()).validate_and_resolve(self.spec(security_options=("no-new-privileges:true","seccomp=unconfined")))

    def test_63_runtime_requires_registered_repository_snapshot(self):
        service=SandboxRuntimeService(manager=None,environment_broker=None,resource_registry=None,session_registry=None,provisioner=None)
        context=type("Context",(),{"repository_snapshot_id":None})()
        with self.assertRaises(ValueError):service.prepare(context)

    def test_64_package_cache_is_hydrated_before_private_install(self):
        backend=Backend();backend.exec_values=[(0,b"",b""),(0,b"",b""),(0,b"",b"")]
        manager,_,_=self.manager(backend);handle=manager.create(self.spec(),plan())
        value=SandboxEnvironmentPlan(strategy=EnvironmentReuseStrategy.SEEDED_FROM_PACKAGE_CACHE,base_image_digest=IMAGE,environment_fingerprint=fingerprint(),package_cache_source_ids=("pip",),required_downloads=("numpy==2.0.0",),compatibility=CompatibilityResult(status=CompatibilityStatus.COMPATIBLE,reasons=("test",)))
        EnvironmentProvisioner(manager).provision(handle,value)
        self.assertEqual(backend.exec_calls[0][1][:2],("cp","-a"))
        self.assertIn("/cache/seed/0",backend.exec_calls[2][1])

    def log_store(self):
        return LogRefs()


if __name__ == "__main__": unittest.main()
