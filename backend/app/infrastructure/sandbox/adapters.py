"""Production Task 09 ports backed by a managed Linux sandbox session."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from backend.app.domain import Artifact, ArtifactKind
from backend.app.runtime.curie_models import (
    CommandExecutionResult,
    ExecutionStatus,
    WorkspaceReferences,
)

from .policy import SandboxPathGuard


@runtime_checkable
class SecretProvider(Protocol):
    """Returns explicitly allowed run secrets without exposing source paths."""

    def resolve(self, run_id: str, names: tuple[str, ...]) -> dict[str, str]: ...


class DockerSandboxWorkspaceAdapter:
    def __init__(self, runtime_service) -> None:
        self.runtime_service = runtime_service

    def prepare(self, context) -> WorkspaceReferences:
        self.runtime_service.prepare(context)
        return WorkspaceReferences(
            run_workspace="/workspace",
            repository_workspace="/workspace/repository",
            artifact_output="/output",
        )

    def cleanup(self, run_id: str) -> None:
        self.runtime_service.cleanup(run_id)

    def metadata(self, run_id: str) -> dict:
        session = self.runtime_service.session_registry.get(run_id)
        environment = session.handle.environment_plan
        audit = self.runtime_service.manager.audit(run_id)
        return {
            "sandbox_audit": audit.model_dump(mode="json"),
            "environment_provenance": {
                "strategy": environment.strategy.value,
                "environment_id": environment.reused_environment_id,
                "fingerprint": environment.environment_fingerprint.model_dump(
                    mode="json"
                ),
                "base_image_digest": environment.base_image_digest,
                "package_cache_source_ids": list(
                    environment.package_cache_source_ids
                ),
                "required_downloads": list(environment.required_downloads),
                "resolved_system_packages": list(
                    environment.resolved_system_packages
                ),
                "provenance": environment.provenance,
            },
        }


class DockerSandboxCommandExecutionAdapter:
    """Executes argv directly through Docker SDK; never invokes a host shell."""

    def __init__(self, manager, sessions, *, log_store, secret_provider=None) -> None:
        self.manager = manager
        self.sessions = sessions
        self.secret_provider = secret_provider
        self.log_store = log_store

    def execute(self, request) -> CommandExecutionResult:
        session = self.sessions.get(request.run_id)
        environment = {}
        secrets = {}
        if request.environment_references:
            if self.secret_provider is None:
                raise RuntimeError("requested environment values have no SecretProvider")
            secrets = self.secret_provider.resolve(
                request.run_id,
                request.environment_references,
            )
            if set(secrets) != set(request.environment_references):
                raise RuntimeError("SecretProvider did not resolve exactly the approved names")
            environment.update(secrets)
        result = self.manager.exec(
            session.handle,
            program=request.program,
            argv=request.argv,
            cwd=request.working_directory_reference,
            environment=environment,
            timeout_seconds=request.timeout_seconds,
        )
        stdout = _redact(result.stdout, secrets.values())
        stderr = _redact(result.stderr, secrets.values())
        stdout_reference = self.log_store.write(
            request.run_id,
            request.command_id,
            "stdout",
            stdout,
        ) if stdout else None
        stderr_reference = self.log_store.write(
            request.run_id,
            request.command_id,
            "stderr",
            stderr,
        ) if stderr else None
        if result.timed_out:
            return CommandExecutionResult(
                status=ExecutionStatus.TIMED_OUT,
                duration_seconds=result.duration_seconds,
                stdout=stdout or None,
                stderr=stderr or None,
                stdout_reference=stdout_reference,
                stderr_reference=stderr_reference,
            )
        status = (
            ExecutionStatus.SUCCEEDED
            if result.exit_code == 0
            else ExecutionStatus.FAILED
        )
        return CommandExecutionResult(
            status=status,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
            stdout=stdout or None,
            stderr=stderr or None,
            stdout_reference=stdout_reference,
            stderr_reference=stderr_reference,
        )


class RunLogStore:
    """Trusted, non-mounted per-run log store with bounded redacted content."""

    def __init__(self, root, *, max_bytes: int = 2 * 1024 * 1024) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("run log root must be an administrator-created directory")
        self.max_bytes = max_bytes

    def write(self, run_id: str, command_id: str, stream: str, value: str) -> str:
        if stream not in {"stdout", "stderr"}:
            raise ValueError("log stream must be stdout or stderr")
        run_key = hashlib.sha256(run_id.encode()).hexdigest()
        command_key = hashlib.sha256(command_id.encode()).hexdigest()
        directory = self.root / run_key
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or directory.resolve().parent != self.root:
            raise RuntimeError("run log directory escaped the trusted log root")
        target = directory / f"{command_key}.{stream}.log"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags, 0o600)
        try:
            os.write(descriptor, value.encode("utf-8")[: self.max_bytes])
        finally:
            os.close(descriptor)
        return f"run-log://{run_id}/{command_key}.{stream}.log"


class SandboxArtifactCollectionAdapter:
    """Collects references only from approved run-private output roots."""

    def __init__(
        self,
        manager,
        sessions,
        *,
        approved_roots=("/output", "/workspace/repository/.paperrepro"),
    ) -> None:
        self.manager = manager
        self.sessions = sessions
        self.approved_roots = tuple(approved_roots)

    def collect(self, context, workspace) -> tuple[Artifact, ...]:
        session = self.sessions.get(context.run_id)
        artifacts = []
        for root in self.approved_roots:
            SandboxPathGuard.require_allowed(
                root,
                ("/output", "/workspace/repository/.paperrepro"),
            )
            result = self.manager.exec(
                session.handle,
                program="find",
                argv=(root, "-xdev", "-type", "f", "-printf", "%p\\0%s\\0"),
                cwd="/workspace",
                timeout_seconds=60,
            )
            if result.timed_out:
                raise RuntimeError("artifact enumeration timed out")
            if result.exit_code not in (0, 1):
                raise RuntimeError("artifact enumeration failed")
            fields = result.stdout.split("\0")
            for path, raw_size in zip(fields[0::2], fields[1::2]):
                if not path:
                    continue
                normalized = SandboxPathGuard.require_allowed(path, (root,))
                artifacts.append(
                    Artifact(
                        name=normalized.rsplit("/", 1)[-1],
                        kind=_artifact_kind(normalized),
                        uri=f"sandbox://{context.run_id}{normalized}",
                        size_bytes=int(raw_size),
                        metadata={
                            "run_id": context.run_id,
                            "container_path": normalized,
                        },
                    )
                )
        return tuple(artifacts)


def _redact(value: str, secrets) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _artifact_kind(path: str) -> ArtifactKind:
    lowered = path.casefold()
    if lowered.endswith((".pt", ".pth", ".ckpt", ".safetensors")):
        return ArtifactKind.CHECKPOINT
    if lowered.endswith((".log", ".stdout", ".stderr")):
        return ArtifactKind.LOG
    if lowered.endswith((".yaml", ".yml", ".toml", ".ini")):
        return ArtifactKind.CONFIG
    if lowered.endswith((".png", ".jpg", ".jpeg", ".svg", ".pdf")):
        return ArtifactKind.PLOT
    if lowered.endswith((".json", ".csv", ".tsv")):
        return ArtifactKind.RESULT
    return ArtifactKind.OTHER
