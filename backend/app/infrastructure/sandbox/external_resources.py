"""Bridge validated external bindings into trusted read-only sandbox mounts."""

from backend.app.domain import (
    ExternalResourceType, ResourceAccess, ResourceBindingValidationStatus,
)

from .models import MountCategory, RegisteredResource, ResourceKind, SandboxMount


class SandboxExternalResourceBinder:
    def __init__(self, trusted_registry, binding_provider) -> None:
        self.trusted_registry = trusted_registry
        self.binding_provider = binding_provider

    def mount_for(self, reference) -> SandboxMount:
        binding = self.binding_provider.get_internal(reference.resource_id)
        if binding.resource_id != reference.resource_id:
            raise ValueError("external resource binding identity mismatch")
        if binding.resource_type is not reference.resource_type:
            raise ValueError("external resource binding type mismatch")
        if binding.validation_status is not ResourceBindingValidationStatus.VALIDATED:
            raise ValueError("sandbox only accepts validated external resources")
        if binding.access is not ResourceAccess.READ_ONLY:
            raise ValueError("sandbox external resources must be read-only")
        category = {
            ExternalResourceType.DATASET: MountCategory.DATASET_READ_ONLY,
            ExternalResourceType.CHECKPOINT: MountCategory.CHECKPOINT_READ_ONLY,
            ExternalResourceType.PRETRAINED_MODEL: MountCategory.PRETRAINED_MODEL_READ_ONLY,
        }[binding.resource_type]
        self.trusted_registry.register_or_validate(
            RegisteredResource(
                resource_id=binding.resource_id,
                kind=ResourceKind.HOST_PATH,
                category=category,
                host_path=binding.host_path,
                metadata={"external_resource_type": binding.resource_type.value},
            )
        )
        return SandboxMount(
            resource_id=binding.resource_id,
            target=reference.logical_mount_path,
            category=category,
            read_only=True,
        )
