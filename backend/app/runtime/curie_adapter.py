"""Production Curie runtime adapter using explicit execution ports."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.domain import RunError, RunRequest, RunResult, RunStatus

from .curie_models import CurieExecutionContext, CurieExecutionResult
from .event_bridge import CurieEventBridge
from .ports import ExecutionBackendUnavailableError
from .state import InMemoryCheckpointFactory, InMemoryCurieStateStoreFactory
from .translation import CurieInputTranslator
from .workflow import CurieReproductionWorkflow


CurieRuntimeInput = CurieExecutionContext


class CurieRuntimeAdapter:
    """Translate and execute one fixed specification through retained Curie stages."""

    def __init__(
        self,
        *,
        command_port=None,
        coding_port=None,
        workspace_port=None,
        artifact_port=None,
        state_store_factory=None,
        checkpoint_factory=None,
        translator=None,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.command_port = command_port
        self.coding_port = coding_port
        self.workspace_port = workspace_port
        self.artifact_port = artifact_port
        self.state_store_factory = (
            state_store_factory or InMemoryCurieStateStoreFactory()
        )
        self.checkpoint_factory = checkpoint_factory or InMemoryCheckpointFactory()
        self.translator = translator or CurieInputTranslator()
        self.max_attempts = max_attempts

    def translate_request(self, request: RunRequest) -> CurieExecutionContext:
        return self.translator.translate(request)

    def run(self, request: RunRequest, event_sink) -> RunResult:
        missing = [
            name
            for name, value in (
                ("CommandExecutionPort", self.command_port),
                ("WorkspacePort", self.workspace_port),
                ("ArtifactCollectionPort", self.artifact_port),
            )
            if value is None
        ]
        if missing:
            raise ExecutionBackendUnavailableError(
                "Curie execution backend is not configured: " + ", ".join(missing)
            )

        context = self.translate_request(request)
        bridge = CurieEventBridge(request.run_id, event_sink)
        bridge.run_started()
        store = self.state_store_factory.create(
            request.run_id,
            request.experiment.id,
        )
        checkpoint = self.checkpoint_factory.create(context.thread_id)
        namespace = tuple(context.namespace.split("/"))
        store.put(namespace, "context", context.model_dump(mode="json"))
        store.put(namespace, "checkpoint", checkpoint)
        workflow = CurieReproductionWorkflow(
            self.command_port,
            self.workspace_port,
            self.artifact_port,
            coding_port=self.coding_port,
            state_store=store,
            max_attempts=self.max_attempts,
        )
        try:
            curie_result = workflow.execute(
                context,
                bridge,
                request.runtime_options.timeout_seconds,
            )
        except Exception as exc:
            now = datetime.now(timezone.utc)
            curie_result = CurieExecutionResult(
                run_id=request.run_id,
                experiment_id=request.experiment.id,
                status=RunStatus.FAILED,
                error=RunError(
                    code="CURIE_WORKFLOW_ERROR",
                    message=str(exc),
                    retryable=False,
                ),
                started_at=now,
                finished_at=now,
            )

        result = self.translate_result(curie_result)
        bridge.terminal(result.status, result.error, result.exit_code)
        return result

    def translate_result(self, value: CurieExecutionResult) -> RunResult:
        metadata = {
            "runtime": "curie",
            "experiment_id": value.experiment_id,
            "attempts": value.attempts,
            "plans": [item.model_dump(mode="json") for item in value.plans],
            "validations": [
                item.model_dump(mode="json") for item in value.validation_results
            ],
            "patches": [item.model_dump(mode="json") for item in value.patches],
            "analysis": value.analysis,
            "conclusion": value.conclusion,
            "warnings": list(value.warnings),
            "agent_trace": [
                item.model_dump(mode="json") for item in value.agent_trace
            ],
        }
        return RunResult(
            run_id=value.run_id,
            status=value.status,
            metrics=value.metrics,
            artifacts=value.artifacts,
            error=value.error,
            exit_code=value.exit_code,
            started_at=value.started_at,
            finished_at=value.finished_at,
            metadata=metadata,
        )
