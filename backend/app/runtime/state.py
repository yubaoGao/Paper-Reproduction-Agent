"""Replaceable run-scoped state and checkpoint boundaries."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Protocol, runtime_checkable


def run_namespace(
    run_id: str,
    experiment_id: str,
    node: str | None = None,
) -> tuple[str, ...]:
    base = ("run", run_id, "experiment", experiment_id)
    return base if node is None else (*base, "agent", node)


def run_thread_id(run_id: str, experiment_id: str) -> str:
    return f"run:{run_id}:experiment:{experiment_id}:curie"


@runtime_checkable
class CurieStateStore(Protocol):
    def put(self, namespace: tuple[str, ...], key: str, value: object) -> None: ...

    def get(self, namespace: tuple[str, ...], key: str) -> object | None: ...


@runtime_checkable
class CurieStateStoreFactory(Protocol):
    def create(self, run_id: str, experiment_id: str) -> CurieStateStore: ...


@runtime_checkable
class CheckpointFactory(Protocol):
    def create(self, thread_id: str) -> object: ...


class InMemoryCurieStateStore:
    """Thread-safe single-run store for tests and single-worker deployments."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, ...], object] = {}
        self._lock = RLock()

    def put(self, namespace: tuple[str, ...], key: str, value: object) -> None:
        with self._lock:
            self._values[(*namespace, key)] = deepcopy(value)

    def get(self, namespace: tuple[str, ...], key: str) -> object | None:
        with self._lock:
            return deepcopy(self._values.get((*namespace, key)))


class InMemoryCurieStateStoreFactory:
    def create(self, run_id: str, experiment_id: str) -> CurieStateStore:
        return InMemoryCurieStateStore()


class InMemoryCheckpointFactory:
    def create(self, thread_id: str) -> object:
        return {"thread_id": thread_id}
