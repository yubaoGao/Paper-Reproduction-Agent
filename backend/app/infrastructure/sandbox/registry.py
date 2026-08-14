"""Trusted resource and run ownership registries."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from .models import RegisteredResource, ResourceKind, RunResources


class ResourceRegistrationError(ValueError):
    pass


class TrustedResourceRegistry:
    def __init__(self, resources=()) -> None:
        self._resources = {}
        self._lock = RLock()
        for resource in resources:
            self.register(resource)

    def register(self, resource: RegisteredResource) -> None:
        with self._lock:
            if resource.resource_id in self._resources:
                raise ResourceRegistrationError("resource ID is already registered")
            if resource.kind is ResourceKind.HOST_PATH:
                path = Path(resource.host_path).resolve(strict=True)
                resource = resource.model_copy(update={"host_path": str(path)})
            self._resources[resource.resource_id] = resource

    def register_or_validate(self, resource: RegisteredResource) -> None:
        """Idempotently register one exact resource; never replace an existing ID."""
        with self._lock:
            existing = self._resources.get(resource.resource_id)
            if existing is None:
                if resource.kind is ResourceKind.HOST_PATH:
                    resource = resource.model_copy(
                        update={"host_path": str(Path(resource.host_path).resolve(strict=True))}
                    )
                if resource.resource_id in self._resources:
                    raise ResourceRegistrationError("resource ID is already registered")
                self._resources[resource.resource_id] = resource
                return
            candidate = resource
            if resource.kind is ResourceKind.HOST_PATH:
                candidate = resource.model_copy(
                    update={"host_path": str(Path(resource.host_path).resolve(strict=True))}
                )
            if existing != candidate:
                raise ResourceRegistrationError("resource ID is registered with different metadata")

    def resolve(self, resource_id: str) -> RegisteredResource:
        with self._lock:
            try:
                return self._resources[resource_id]
            except KeyError as exc:
                raise ResourceRegistrationError(
                    f"resource {resource_id!r} is not registered"
                ) from exc

    def remove_run_resources(self, run_id: str) -> None:
        with self._lock:
            self._resources = {
                key: value
                for key, value in self._resources.items()
                if value.owner_run_id != run_id
            }


class RunResourceRegistry:
    """Records exact IDs; cleanup never discovers resources by name or glob."""

    def __init__(self) -> None:
        self._runs: dict[str, RunResources] = {}
        self._lock = RLock()

    def create(self, run_id: str) -> RunResources:
        with self._lock:
            if run_id in self._runs:
                raise ValueError("run resources already exist")
            value = RunResources(run_id=run_id)
            self._runs[run_id] = value
            return value

    def get(self, run_id: str) -> RunResources:
        with self._lock:
            try:
                return self._runs[run_id]
            except KeyError as exc:
                raise KeyError(f"unknown sandbox run {run_id!r}") from exc

    def update(self, run_id: str, **changes) -> RunResources:
        with self._lock:
            value = self.get(run_id).model_copy(update=changes)
            self._runs[run_id] = value
            return value

    def remove(self, run_id: str) -> RunResources:
        with self._lock:
            return self._runs.pop(run_id)
