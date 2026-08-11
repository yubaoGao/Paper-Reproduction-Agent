"""Application contracts for static repository analysis."""
from __future__ import annotations
from abc import ABC,abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from pydantic import BaseModel,ConfigDict,Field
from backend.app.domain import RepositoryReference,RepositorySnapshot

class RepositoryAnalysisError(RuntimeError): pass
class InvalidRepositorySourceError(RepositoryAnalysisError): pass
class UnsafeRepositorySourceError(InvalidRepositorySourceError): pass
class RepositoryResolutionError(RepositoryAnalysisError): pass
class RepositoryStaticAnalysisError(RepositoryAnalysisError): pass

class RepositoryAnalysisSettings(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    materialization_root:Path=Path("workspace/repositories")
    max_repository_bytes:int=Field(default=512*1024*1024,ge=1024)
    max_file_bytes:int=Field(default=2*1024*1024,ge=1024)
    max_files:int=Field(default=50_000,ge=1)
    git_timeout_seconds:float=Field(default=120,gt=0)
    max_context_files:int=Field(default=60,ge=1)
    max_context_chars_per_file:int=Field(default=12_000,ge=256)
    max_repair_attempts:int=Field(default=2,ge=0,le=4)

@dataclass(frozen=True)
class ResolvedRepositorySource:
    repository:RepositoryReference; root:Path; resolved_commit_sha:str; materialized:bool

class RepositoryCredentialProvider(Protocol):
    def authorization_header(self,credential_reference:str)->str: ...

class RepositorySourceResolver(ABC):
    @abstractmethod
    def resolve(self,reference:RepositoryReference)->ResolvedRepositorySource: ...

class RepositorySnapshotBuilder(ABC):
    @abstractmethod
    def build(self,source:ResolvedRepositorySource)->RepositorySnapshot: ...
