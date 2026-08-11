"""Stable boundary between platform orchestration and experiment runtimes."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.app.domain import RunEvent, RunRequest, RunResult


@runtime_checkable
class RunEventSink(Protocol):
    """Destination for ordered events produced by an experiment runtime."""

    def publish(self, event: RunEvent) -> None:
        """Publish one event without coupling the runtime to a transport."""


@runtime_checkable
class ExperimentRuntime(Protocol):
    """Provider-neutral interface implemented by experiment runtimes."""

    def run(self, request: RunRequest, event_sink: RunEventSink) -> RunResult:
        """Execute one admitted run and return its terminal result."""
