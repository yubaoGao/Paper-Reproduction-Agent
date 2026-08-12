"""Stable, static-only repository intelligence domain models."""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit
from pydantic import Field,JsonValue,model_validator
from .experiment import DomainModel,EvaluationPolicy,NonEmptyStr
from .reproduction import EvidenceReference,InformationStatus

class RepositorySourceType(str,Enum): LOCAL_DIRECTORY="local_directory"; GIT_URL="git_url"
class SubmodulePolicy(str,Enum): RECORD_ONLY="record_only"
class RepositoryFileType(str,Enum): SOURCE="source"; CONFIG="config"; MANIFEST="manifest"; DOCUMENTATION="documentation"; SCRIPT="script"; BUILD="build"; DATA="data"; BINARY="binary"; OTHER="other"
class SymbolKind(str,Enum): FUNCTION="function"; CLASS="class"; METHOD="method"; MODULE="module"; CLI_ENTRYPOINT="cli_entrypoint"; GLOBAL="global"; CONFIG="config"
class EntrypointType(str,Enum): TRAINING="training"; EVALUATION="evaluation"; INFERENCE="inference"; PREPROCESSING="preprocessing"; GENERIC="generic"
class RepositoryAnalysisStatus(str,Enum): COMPLETE="complete"; PARTIAL="partial"; FAILED="failed"
class RepositoryConflictType(str,Enum): DOCUMENTATION_CODE="documentation_code"; DEPENDENCY_VERSION="dependency_version"; DEFAULT_VALUE="default_value"; DATASET_NAME="dataset_name"; ENTRYPOINT="entrypoint"; CONFIG_CLI="config_cli"; OTHER="other"
class RepositoryConflictStatus(str,Enum): RESOLVED="resolved"; UNRESOLVED="unresolved"

class RepositoryReference(DomainModel):
    repository_id:NonEmptyStr; source_type:RepositorySourceType; source_uri:NonEmptyStr
    requested_ref:NonEmptyStr|None=None; credential_reference:NonEmptyStr|None=None
    submodule_policy:SubmodulePolicy=SubmodulePolicy.RECORD_ONLY
    metadata:dict[NonEmptyStr,JsonValue]=Field(default_factory=dict)
    @model_validator(mode="after")
    def validate_source(self):
        if self.source_type is RepositorySourceType.GIT_URL:
            parsed=urlsplit(self.source_uri)
            if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment: raise ValueError("remote repository URL must be credential-free HTTPS without query or fragment")
        return self

class RepositoryFile(DomainModel):
    path:NonEmptyStr; file_type:RepositoryFileType; language:NonEmptyStr|None=None; size:int=Field(ge=0)
    content_hash:NonEmptyStr; is_text:bool; generated:bool=False; vendor:bool=False; analysis_eligible:bool=True
    @model_validator(mode="after")
    def relative_path(self):
        if self.path.startswith(("/","\\")) or ":" in self.path.split("/",1)[0] or ".." in self.path.split("/"): raise ValueError("repository file path must be safe and relative")
        return self

class SubmoduleRecord(DomainModel): path:NonEmptyStr; url:NonEmptyStr; commit_sha:NonEmptyStr|None=None; materialized:bool=False
class GitLfsPointer(DomainModel): path:NonEmptyStr; oid:NonEmptyStr; size:int|None=Field(default=None,ge=0); object_type:NonEmptyStr|None=None
class RepositorySnapshotMetadata(DomainModel): created_at:datetime; file_count:int=Field(ge=0); total_bytes:int=Field(ge=0); ignored_files:int=Field(ge=0); warnings:tuple[NonEmptyStr,...]=()
class RepositorySnapshot(DomainModel):
    snapshot_id:NonEmptyStr; repository:RepositoryReference; resolved_commit_sha:NonEmptyStr; root:NonEmptyStr; content_hash:NonEmptyStr
    files:tuple[RepositoryFile,...]; languages:tuple[NonEmptyStr,...]=(); manifests:tuple[NonEmptyStr,...]=(); configs:tuple[NonEmptyStr,...]=(); documentation_files:tuple[NonEmptyStr,...]=(); submodules:tuple[SubmoduleRecord,...]=(); lfs_pointers:tuple[GitLfsPointer,...]=(); metadata:RepositorySnapshotMetadata
    @model_validator(mode="after")
    def validate_snapshot(self):
        paths=[x.path for x in self.files]
        if len(paths)!=len(set(paths)): raise ValueError("snapshot file paths must be unique")
        if len(self.resolved_commit_sha)!=40 and self.resolved_commit_sha!="WORKTREE": raise ValueError("resolved commit must be a full SHA or WORKTREE")
        return self

class CodeSymbol(DomainModel):
    symbol_id:NonEmptyStr; path:NonEmptyStr; name:NonEmptyStr; qualified_name:NonEmptyStr; kind:SymbolKind; language:NonEmptyStr
    start_line:int=Field(ge=1); end_line:int=Field(ge=1); references:tuple[NonEmptyStr,...]=(); decorators:tuple[NonEmptyStr,...]=()
    @model_validator(mode="after")
    def lines(self):
        if self.end_line<self.start_line: raise ValueError("symbol line range is inverted")
        return self
class CodeIndex(DomainModel): symbols:tuple[CodeSymbol,...]=(); imports:dict[NonEmptyStr,tuple[NonEmptyStr,...]]=Field(default_factory=dict); parse_warnings:tuple[NonEmptyStr,...]=()
class CliArgument(DomainModel): name:NonEmptyStr; value_type:NonEmptyStr|None=None; default:JsonValue|None=None; required:bool=False; choices:tuple[JsonValue,...]=(); source:NonEmptyStr
class EntrypointCandidate(DomainModel):
    entrypoint_id:NonEmptyStr; entrypoint_type:EntrypointType; path:NonEmptyStr; symbol_id:NonEmptyStr|None=None; interpreter:NonEmptyStr|None=None
    arguments:tuple[CliArgument,...]=(); config_paths:tuple[NonEmptyStr,...]=(); confidence:float=Field(ge=0,le=1); evidence:tuple[EvidenceReference,...]
class RepositoryConfigRecord(DomainModel): config_id:NonEmptyStr; path:NonEmptyStr; key_path:NonEmptyStr; value:JsonValue; source:NonEmptyStr; references:tuple[NonEmptyStr,...]=(); dynamic_override:bool=False; evidence:tuple[EvidenceReference,...]
class DependencyRecord(DomainModel): dependency_id:NonEmptyStr; name:NonEmptyStr; version_spec:NonEmptyStr|None=None; ecosystem:NonEmptyStr; optional:bool=False; source_path:NonEmptyStr; evidence:tuple[EvidenceReference,...]
class RepositoryCommand(DomainModel): command_id:NonEmptyStr; source_path:NonEmptyStr; command:NonEmptyStr; entrypoint_path:NonEmptyStr|None=None; arguments:tuple[NonEmptyStr,...]=(); environment_variables:tuple[NonEmptyStr,...]=(); evidence:tuple[EvidenceReference,...]
class RepositoryComponentRecord(DomainModel): component_id:NonEmptyStr; name:NonEmptyStr; kind:NonEmptyStr; paths:tuple[NonEmptyStr,...]; symbol_ids:tuple[NonEmptyStr,...]=(); details:dict[NonEmptyStr,JsonValue]=Field(default_factory=dict); evidence:tuple[EvidenceReference,...]
class RepositoryExperimentImplementation(DomainModel):
    implementation_id:NonEmptyStr; name:NonEmptyStr; entrypoint_ids:tuple[NonEmptyStr,...]=Field(min_length=1); config_ids:tuple[NonEmptyStr,...]=(); dataset_ids:tuple[NonEmptyStr,...]=(); model_ids:tuple[NonEmptyStr,...]=(); parameter_keys:tuple[NonEmptyStr,...]=(); command_ids:tuple[NonEmptyStr,...]=(); evidence:tuple[EvidenceReference,...]=Field(min_length=1)
class RepositoryEvaluationPolicyRecord(DomainModel):
    policy_id:NonEmptyStr
    policy:EvaluationPolicy
    implementation_id:NonEmptyStr|None=None
    entrypoint_ids:tuple[NonEmptyStr,...]=()
    training_command_id:NonEmptyStr|None=None
    evaluation_command_id:NonEmptyStr|None=None
    paper_policy_adaptation_supported:bool=False
    evidence:tuple[EvidenceReference,...]=Field(min_length=1)
    @model_validator(mode="after")
    def explicit_code_policy(self):
        if self.policy.source.value!="code_explicit":raise ValueError("repository evaluation policy must be CODE_EXPLICIT")
        if not self.policy.evidence:raise ValueError("repository evaluation policy requires policy evidence")
        if self.paper_policy_adaptation_supported and self.evaluation_command_id is None:raise ValueError("paper-policy adaptation requires an explicit evaluation command")
        return self
class RepositoryConflictCandidate(DomainModel): value:JsonValue; evidence:tuple[EvidenceReference,...]
class RepositoryConflict(DomainModel):
    conflict_id:NonEmptyStr; semantic_key:NonEmptyStr; conflict_type:RepositoryConflictType; candidates:tuple[RepositoryConflictCandidate,...]=Field(min_length=2); status:RepositoryConflictStatus=RepositoryConflictStatus.UNRESOLVED; resolution:JsonValue|None=None; reasoning:NonEmptyStr|None=None
    @model_validator(mode="after")
    def validate_resolution(self):
        values=[repr(x.value) for x in self.candidates]
        if len(values)!=len(set(values)):raise ValueError("conflict candidates must contain distinct values")
        if self.status is RepositoryConflictStatus.RESOLVED and self.resolution is None:raise ValueError("resolved conflict requires a resolution")
        if self.status is RepositoryConflictStatus.UNRESOLVED and self.resolution is not None:raise ValueError("unresolved conflict cannot have a resolution")
        return self
class RepositoryFact(DomainModel): name:NonEmptyStr; value:JsonValue|None=None; status:InformationStatus; evidence:tuple[EvidenceReference,...]=(); confidence:float|None=Field(default=None,ge=0,le=1)
class RepositoryAnalysisMetadata(DomainModel): stages_completed:tuple[NonEmptyStr,...]=(); missing_components:tuple[NonEmptyStr,...]=(); warnings:tuple[NonEmptyStr,...]=(); prompt_versions:dict[NonEmptyStr,NonEmptyStr]=Field(default_factory=dict)
class RepositoryAnalysisCatalog(DomainModel):
    catalog_id:NonEmptyStr; repository:RepositoryReference; snapshot_id:NonEmptyStr; resolved_commit_sha:NonEmptyStr; languages:tuple[NonEmptyStr,...]
    project_structure:tuple[NonEmptyStr,...]=(); code_index:CodeIndex=Field(default_factory=CodeIndex); documentation:tuple[RepositoryComponentRecord,...]=(); environment_definitions:tuple[RepositoryComponentRecord,...]=(); dependencies:tuple[DependencyRecord,...]=(); entrypoints:tuple[EntrypointCandidate,...]=(); configurations:tuple[RepositoryConfigRecord,...]=(); datasets:tuple[RepositoryComponentRecord,...]=(); models:tuple[RepositoryComponentRecord,...]=(); experiment_implementations:tuple[RepositoryExperimentImplementation,...]=(); evaluation_policies:tuple[RepositoryEvaluationPolicyRecord,...]=(); ablation_mechanisms:tuple[RepositoryComponentRecord,...]=(); metrics:tuple[RepositoryComponentRecord,...]=(); checkpoints:tuple[RepositoryComponentRecord,...]=(); artifact_paths:tuple[RepositoryComponentRecord,...]=(); commands:tuple[RepositoryCommand,...]=(); evidence:tuple[EvidenceReference,...]=(); conflicts:tuple[RepositoryConflict,...]=(); unknowns:tuple[RepositoryFact,...]=(); analysis_status:RepositoryAnalysisStatus; analysis_metadata:RepositoryAnalysisMetadata
    @model_validator(mode="after")
    def validate_catalog(self):
        if self.analysis_status is RepositoryAnalysisStatus.FAILED: raise ValueError("failed analysis is represented by exception")
        if self.analysis_status is RepositoryAnalysisStatus.PARTIAL and not self.analysis_metadata.missing_components: raise ValueError("partial analysis requires missing components")
        for records,label in ((self.entrypoints,"entrypoint"),(self.configurations,"config"),(self.dependencies,"dependency"),(self.experiment_implementations,"implementation")):
            ids=[getattr(x,f"{label}_id") for x in records]
            if len(ids)!=len(set(ids)): raise ValueError(f"duplicate {label} ids")
        policy_ids=[x.policy_id for x in self.evaluation_policies]
        if len(policy_ids)!=len(set(policy_ids)):raise ValueError("duplicate evaluation policy ids")
        return self
class RepositoryAnalysisTrace(DomainModel):
    analysis_id:NonEmptyStr; repository_id:NonEmptyStr; commit_sha:NonEmptyStr; started_at:datetime; finished_at:datetime; selected_files:tuple[NonEmptyStr,...]=(); selected_symbols:tuple[NonEmptyStr,...]=(); primary_calls:int=Field(ge=0); fast_calls:int=Field(ge=0); repair_count:int=Field(ge=0); prompt_versions:dict[NonEmptyStr,NonEmptyStr]; usage:tuple[JsonValue,...]=(); warnings:tuple[NonEmptyStr,...]=(); status:RepositoryAnalysisStatus
