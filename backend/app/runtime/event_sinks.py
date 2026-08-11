"""Transport-free event sinks useful to runtimes and unit tests."""

from __future__ import annotations

from threading import Lock

from backend.app.domain import RunEvent


class InMemoryEventSink:
    """Thread-safe append-only event sink preserving publication order."""

    def __init__(self) -> None:
        self._events: list[RunEvent] = []
        self._lock = Lock()

    def publish(self, event: RunEvent) -> None:
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> tuple[RunEvent, ...]:
        """Return an immutable snapshot in publication order."""

        with self._lock:
            return tuple(self._events)

    def events_for(self, run_id: str) -> tuple[RunEvent, ...]:
        """Return an ordered snapshot for one run."""

        with self._lock:
            return tuple(event for event in self._events if event.run_id == run_id)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
