"""Replaceable run-scoped state and checkpoint boundaries."""

from __future__ import annotations

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
