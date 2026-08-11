import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from backend.app.domain import (
    Artifact,
    ArtifactKind,
    DatasetSource,
    EnvironmentSpecification,
    EventType,
    ExperimentRun,
    ExperimentSpecification,
    ExperimentTaskType,
    LogPayload,
    Metric,
    MetricExpectation,
    RepositorySource,
    ResourceRequest,
    RunError,
    RunEvent,
    RunRequest,
    RunResult,
    RunStatus,
    RuntimeOptions,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_specification() -> ExperimentSpecification:
    return ExperimentSpecification(
        id="dmsf-mvsa-s-full",
        name="DMSF MVSA-S Full Model",
        description="Reproduce the full DMSF model on MVSA-S.",
        task_type=ExperimentTaskType.FULL_REPRODUCTION,
        repository=RepositorySource(
            uri="https://github.com/example/dmsf.git",
            revision="paper-release",
        ),
        dataset=DatasetSource(uri="dataset://mvsa-s", name="MVSA-S"),
        entrypoint="train.py",
        command=("python", "train.py", "--dataset", "MVSA-S"),
        environment=EnvironmentSpecification(
            python_version="3.11", dependencies=("torch==2.5.1",)
        ),
        hyperparameters={"epochs": 30, "batch_size": 32},
        expected_metrics=(
            MetricExpectation(name="accuracy", value=0.7533, split="test"),
            MetricExpectation(name="f1", value=0.7531, split="test"),
        ),
        seed=42,
        tags=("dmsf", "full-model"),
    )


class ExperimentDomainModelTests(unittest.TestCase):
    def test_experiment_specification_validation(self) -> None:
        specification = make_specification()

        self.assertEqual(specification.task_type, ExperimentTaskType.FULL_REPRODUCTION)
        self.assertEqual(specification.expected_metrics[0].value, 0.7533)

        with self.assertRaises(ValidationError):
            ExperimentSpecification(
                id="missing-command",
                name="Missing command",
                description="No executable target.",
                task_type=ExperimentTaskType.CUSTOM,
                repository=RepositorySource(uri="https://example.com/repo.git"),
            )

    def test_experiment_run_status_validation(self) -> None:
        run = ExperimentRun(
            run_id="run-1",
            experiment_id="dmsf-mvsa-s-full",
            status=RunStatus.RUNNING,
            created_at=NOW,
            started_at=NOW + timedelta(seconds=1),
            attempt=2,
            runtime="curie",
        )

        self.assertEqual(run.status, RunStatus.RUNNING)
        self.assertEqual(run.attempt, 2)

        with self.assertRaises(ValidationError):
            ExperimentRun(
                run_id="run-failed",
                experiment_id="dmsf-mvsa-s-full",
                status=RunStatus.FAILED,
                created_at=NOW,
            )

    def test_run_request_creation(self) -> None:
        specification = make_specification()
        request = RunRequest(
            run_id="run-1",
            experiment=specification,
            repository_source=specification.repository,
            dataset_source=specification.dataset,
            environment=specification.environment,
            resources=ResourceRequest(cpu_cores=4, memory_mb=8192, gpu_count=1),
            runtime_options=RuntimeOptions(timeout_seconds=7200),
        )

        self.assertEqual(request.experiment.id, specification.id)
        self.assertEqual(request.resources.gpu_count, 1)
        self.assertFalse(request.runtime_options.network_access)

    def test_metric_validation(self) -> None:
        metric = Metric(name="accuracy", value=0.7533, step=30, split="test")
        self.assertEqual(metric.name, "accuracy")

        for invalid_value in (True, float("nan"), float("inf")):
            with self.subTest(value=invalid_value), self.assertRaises(ValidationError):
                Metric(name="accuracy", value=invalid_value)

    def test_artifact_validation(self) -> None:
        artifact = Artifact(
            name="evaluation-report",
            kind=ArtifactKind.REPORT,
            uri="artifacts://run-1/report.md",
            media_type="text/markdown",
            checksum="sha256:abc123",
        )

        self.assertEqual(artifact.kind, ArtifactKind.REPORT)
        with self.assertRaises(ValidationError):
            Artifact(name="empty-uri", kind=ArtifactKind.LOG, uri="")

    def test_run_event_creation_requires_typed_payload(self) -> None:
        event = RunEvent(
            run_id="run-1",
            event_type=EventType.LOG,
            timestamp=NOW,
            payload=LogPayload(level="INFO", message="training started"),
        )
        self.assertEqual(event.payload.message, "training started")

        with self.assertRaises(ValidationError):
            RunEvent(
                run_id="run-1",
                event_type=EventType.METRIC,
                timestamp=NOW,
                payload=LogPayload(message="not a metric"),
            )

    def test_successful_run_result(self) -> None:
        result = RunResult(
            run_id="run-1",
            status=RunStatus.SUCCEEDED,
            metrics=(Metric(name="accuracy", value=0.7533),),
            exit_code=0,
            started_at=NOW,
            finished_at=NOW + timedelta(minutes=10),
        )

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertIsNone(result.error)

        with self.assertRaises(ValidationError):
            RunResult(
                run_id="bad-success",
                status=RunStatus.SUCCEEDED,
                exit_code=1,
                finished_at=NOW,
            )

    def test_failed_run_result(self) -> None:
        error = RunError(code="process_failed", message="training exited", retryable=True)
        result = RunResult(
            run_id="run-2",
            status=RunStatus.FAILED,
            error=error,
            exit_code=2,
            finished_at=NOW,
        )

        self.assertEqual(result.error.code, "process_failed")
        with self.assertRaises(ValidationError):
            RunResult(
                run_id="bad-failure",
                status=RunStatus.FAILED,
                exit_code=2,
                finished_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
