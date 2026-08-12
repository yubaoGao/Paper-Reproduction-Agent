import unittest
from datetime import datetime, timezone

from backend.app.domain import (
    EventType,
    ExperimentSpecification,
    ExperimentTaskType,
    LogPayload,
    RepositorySource,
    RunEvent,
    RunRequest,
)
from backend.app.runtime import (
    CurieRuntimeAdapter,
    ExecutionBackendUnavailableError,
    ExperimentRuntime,
    InMemoryEventSink,
    RunEventSink,
)


def make_request() -> RunRequest:
    repository = RepositorySource(uri="https://github.com/example/paper.git", revision="v1")
    specification = ExperimentSpecification(
        id="spec-1",
        name="Paper baseline",
        description="Reproduce the published baseline.",
        task_type=ExperimentTaskType.BASELINE_REPRODUCTION,
        repository=repository,
        command=("python", "train.py"),
        seed=7,
    )
    return RunRequest(
        run_id="run-1",
        experiment=specification,
        repository_source=repository,
    )


class RuntimeContractTests(unittest.TestCase):
    def test_in_memory_event_sink_preserves_publication_order(self) -> None:
        sink = InMemoryEventSink()
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)

        for message in ("first", "second", "third"):
            sink.publish(
                RunEvent(
                    run_id="run-1",
                    event_type=EventType.LOG,
                    timestamp=timestamp,
                    payload=LogPayload(message=message),
                )
            )

        self.assertIsInstance(sink, RunEventSink)
        self.assertEqual(
            [event.payload.message for event in sink.events_for("run-1")],
            ["first", "second", "third"],
        )
        self.assertIsInstance(sink.events, tuple)

    def test_curie_adapter_satisfies_contract_and_translates_request(self) -> None:
        adapter = CurieRuntimeAdapter()
        request = make_request()
        translated = adapter.translate_request(request)

        self.assertIsInstance(adapter, ExperimentRuntime)
        self.assertEqual(translated.run_id, request.run_id)
        self.assertEqual(translated.experiment_id, request.experiment.id)
        self.assertEqual(translated.command.program, "python")
        self.assertEqual(translated.command.arguments, ("train.py",))

        with self.assertRaisesRegex(ExecutionBackendUnavailableError, "not configured"):
            adapter.run(request, InMemoryEventSink())


if __name__ == "__main__":
    unittest.main()
