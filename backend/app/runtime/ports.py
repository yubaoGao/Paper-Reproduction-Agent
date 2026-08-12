"""Infrastructure-neutral execution ports used by the Curie runtime."""

from typing import Protocol, runtime_checkable

from backend.app.domain import Artifact

from .curie_models import (
    CodingRequest,
    CodingResult,
    CommandExecutionRequest,
    CommandExecutionResult,
    CurieExecutionContext,
    WorkspaceReferences,
)


class ExecutionBackendUnavailableError(RuntimeError):
    """Raised when a required production execution adapter is not configured."""


@runtime_checkable
class CommandExecutionPort(Protocol):
    def execute(self, request: CommandExecutionRequest) -> CommandExecutionResult: ...


@runtime_checkable
class CodingAgentPort(Protocol):
    def apply(self, request: CodingRequest) -> CodingResult: ...


@runtime_checkable
class WorkspacePort(Protocol):
    def prepare(self, context: CurieExecutionContext) -> WorkspaceReferences: ...


@runtime_checkable
class ArtifactCollectionPort(Protocol):
    def collect(
        self,
        context: CurieExecutionContext,
        workspace: WorkspaceReferences,
    ) -> tuple[Artifact, ...]: ...


@runtime_checkable
class RuntimeMetadataPort(Protocol):
    def metadata(self, run_id: str) -> dict: ...
