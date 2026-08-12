"""OpenHands coding integration constrained to an existing experiment sandbox."""

from __future__ import annotations

import json

from backend.app.domain import Artifact, ArtifactKind
from backend.app.runtime.curie_models import CodingResult

from .policy import SandboxPathGuard


class OpenHandsExecutionError(RuntimeError):
    pass


class SandboxedOpenHandsController:
    """Runs a trusted controller entrypoint inside the current sandbox.

    The image-provided controller owns OpenHands SDK compatibility. It receives
    no Docker socket and can execute only through the already-created container.
    """

    def __init__(
        self,
        manager,
        sessions,
        *,
        controller_program="/opt/paperrepro/bin/openhands-controller",
        max_iterations: int = 20,
        timeout_seconds: int = 1800,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("OpenHands max_iterations must be positive")
        self.manager = manager
        self.sessions = sessions
        self.controller_program = controller_program
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds

    def run(self, request) -> dict:
        session = self.sessions.get(request.run_id)
        payload = json.dumps(
            {
                "instruction": request.instruction,
                "workspace": "/workspace/repository",
                "allowed_change_categories": list(request.allowed_change_categories),
                "locked_constraint_keys": list(request.locked_constraint_keys),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        result = self.manager.exec(
            session.handle,
            program=self.controller_program,
            argv=(
                "--workspace",
                "/workspace/repository",
                "--max-iterations",
                str(self.max_iterations),
                "--request-json",
                payload,
            ),
            cwd="/workspace/repository",
            timeout_seconds=self.timeout_seconds,
        )
        if result.timed_out:
            raise OpenHandsExecutionError("OpenHands execution timed out")
        if result.exit_code != 0:
            raise OpenHandsExecutionError("OpenHands controller failed")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise OpenHandsExecutionError(
                "OpenHands controller returned invalid structured output"
            ) from exc
        for path in value.get("changed_paths", []):
            SandboxPathGuard.require_allowed(path, ("/workspace/repository",))
        if value.get("mounts") or value.get("docker_socket"):
            raise OpenHandsExecutionError("OpenHands cannot change sandbox policy")
        return value


class OpenHandsCodingAgentAdapter:
    def __init__(self, controller: SandboxedOpenHandsController) -> None:
        self.controller = controller

    def apply(self, request) -> CodingResult:
        value = self.controller.run(request)
        artifact = None
        patch_path = value.get("patch_path")
        if patch_path:
            normalized = SandboxPathGuard.require_allowed(
                patch_path,
                ("/workspace/repository/.paperrepro",),
            )
            artifact = Artifact(
                name=normalized.rsplit("/", 1)[-1],
                kind=ArtifactKind.OTHER,
                uri=f"sandbox://{request.run_id}{normalized}",
                metadata={"run_id": request.run_id, "container_path": normalized},
            )
        return CodingResult(
            patch_id=value["patch_id"],
            summary=value["summary"],
            changed_categories=tuple(value.get("changed_categories", ())),
            proposed_values=dict(value.get("proposed_values", {})),
            artifact=artifact,
        )
