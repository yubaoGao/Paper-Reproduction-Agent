"""Stable paper-to-code alignment models; intentionally non-executable."""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import Field,JsonValue,model_validator
from .experiment import DomainModel,EvaluationPolicy,EvaluationPolicySource,NonEmptyStr
from .reproduction import EvidenceReference,InformationStatus,PaperReference
from .repository import RepositoryReference

class AlignmentStatus(str,Enum): ALIGNED="aligned"; PARTIALLY_ALIGNED="partially_aligned"; AMBIGUOUS="ambiguous"; NOT_FOUND="not_found"; CONFLICTED="conflicted"
class ParameterMappingStatus(str,Enum): MATCHED="matched"; SEMANTIC_MATCH_VALUE_UNKNOWN="semantic_match_value_unknown"; VALUE_CONFLICT="value_conflict"; PAPER_ONLY="paper_only"; REPOSITORY_ONLY="repository_only"; AMBIGUOUS="ambiguous"; NOT_FOUND="not_found"
class AlignmentAnalysisStatus(str,Enum): COMPLETE="complete"; PARTIAL="partial"; FAILED="failed"
class AlignmentConflictType(str,Enum): PARAMETER_VALUE_MISMATCH="parameter_value_mismatch"; MISSING_IMPLEMENTATION="missing_implementation"; MULTIPLE_IMPLEMENTATIONS="multiple_implementations"; METRIC_DEFINITION_MISMATCH="metric_definition_mismatch"; EVALUATION_POLICY_CONFLICT="evaluation_policy_conflict"; DATASET_MISMATCH="dataset_mismatch"; MODEL_VARIANT_MISMATCH="model_variant_mismatch"; DOCUMENTATION_CODE_CONFLICT="documentation_code_conflict"; OTHER="other"
class AlignmentConflictStatus(str,Enum): RESOLVED="resolved"; UNRESOLVED="unresolved"
class AlignmentItemSource(str,Enum): PAPER="paper"; REPOSITORY="repository"

class DatasetAlignment(DomainModel):
    alignment_id:NonEmptyStr; paper_dataset:NonEmptyStr; repository_dataset_ids:tuple[NonEmptyStr,...]=(); status:AlignmentStatus; confidence:float=Field(ge=0,le=1); reasoning:NonEmptyStr; paper_evidence:tuple[EvidenceReference,...]=(); repository_evidence:tuple[EvidenceReference,...]=()
class ModelAlignment(DomainModel):
    alignment_id:NonEmptyStr; paper_model:NonEmptyStr; repository_model_ids:tuple[NonEmptyStr,...]=(); status:AlignmentStatus; confidence:float=Field(ge=0,le=1); reasoning:NonEmptyStr; paper_evidence:tuple[EvidenceReference,...]=(); repository_evidence:tuple[EvidenceReference,...]=()
class ParameterAlignment(DomainModel):
    alignment_id:NonEmptyStr; paper_experiment_id:NonEmptyStr|None=None; semantic_name:NonEmptyStr; paper_parameter_name:NonEmptyStr|None=None; paper_value:JsonValue|None=None; paper_status:InformationStatus|None=None; repository_config_ids:tuple[NonEmptyStr,...]=(); repository_value:JsonValue|None=None; repository_source:NonEmptyStr|None=None; mapping_status:ParameterMappingStatus; confidence:float=Field(ge=0,le=1); paper_evidence:tuple[EvidenceReference,...]=(); repository_evidence:tuple[EvidenceReference,...]=(); conflict_id:NonEmptyStr|None=None
class AblationAlignment(DomainModel):
    alignment_id:NonEmptyStr; paper_experiment_id:NonEmptyStr|None=None; paper_ablation:NonEmptyStr; repository_ablation_ids:tuple[NonEmptyStr,...]=(); status:AlignmentStatus; confidence:float=Field(ge=0,le=1); reasoning:NonEmptyStr; paper_evidence:tuple[EvidenceReference,...]=(); repository_evidence:tuple[EvidenceReference,...]=()
class MetricAlignment(DomainModel):
    alignment_id:NonEmptyStr; paper_metric:NonEmptyStr; paper_claim_ids:tuple[NonEmptyStr,...]=(); repository_metric_ids:tuple[NonEmptyStr,...]=(); status:AlignmentStatus; confidence:float=Field(ge=0,le=1); paper_split:NonEmptyStr|None=None; repository_split:NonEmptyStr|None=None; paper_aggregation:NonEmptyStr|None=None; repository_aggregation:NonEmptyStr|None=None; reasoning:NonEmptyStr; paper_evidence:tuple[EvidenceReference,...]=(); repository_evidence:tuple[EvidenceReference,...]=(); conflict_id:NonEmptyStr|None=None
class EvaluationPolicyAlignmentStatus(str,Enum):
    ALIGNED="aligned"; PAPER_ONLY="paper_only"; CODE_FALLBACK="code_fallback"; SCIENTIFIC_DEFAULT="scientific_default"; CONFLICT="conflict"; AMBIGUOUS="ambiguous"; UNKNOWN="unknown"
class EvaluationPolicyAlignment(DomainModel):
    alignment_id:NonEmptyStr
    paper_experiment_id:NonEmptyStr
    repository_policy_ids:tuple[NonEmptyStr,...]=()
    paper_policy:EvaluationPolicy|None=None
    code_policy:EvaluationPolicy|None=None
    resolved_policy:EvaluationPolicy|None=None
    status:EvaluationPolicyAlignmentStatus
    reasoning:NonEmptyStr
    confidence:float=Field(ge=0,le=1)
    paper_evidence:tuple[EvidenceReference,...]=()
    repository_evidence:tuple[EvidenceReference,...]=()
    conflict_id:NonEmptyStr|None=None
    adaptation_supported:bool=False
    warnings:tuple[NonEmptyStr,...]=()
    @model_validator(mode="after")
    def valid_resolution(self):
        if len(self.repository_policy_ids)!=len(set(self.repository_policy_ids)):raise ValueError("repository evaluation policy ids must be unique")
        if self.paper_policy is not None and self.paper_policy.source is not EvaluationPolicySource.PAPER_EXPLICIT:raise ValueError("paper alignment policy must be PAPER_EXPLICIT")
        if self.code_policy is not None and self.code_policy.source is not EvaluationPolicySource.CODE_EXPLICIT:raise ValueError("code alignment policy must be CODE_EXPLICIT")
        if self.status in {EvaluationPolicyAlignmentStatus.ALIGNED,EvaluationPolicyAlignmentStatus.PAPER_ONLY,EvaluationPolicyAlignmentStatus.CODE_FALLBACK,EvaluationPolicyAlignmentStatus.SCIENTIFIC_DEFAULT,EvaluationPolicyAlignmentStatus.CONFLICT} and self.resolved_policy is None:raise ValueError("resolved evaluation alignment requires policy")
        if self.status in {EvaluationPolicyAlignmentStatus.AMBIGUOUS,EvaluationPolicyAlignmentStatus.UNKNOWN} and self.resolved_policy is not None:raise ValueError("unresolved evaluation alignment cannot select policy")
        if self.status in {EvaluationPolicyAlignmentStatus.CONFLICT,EvaluationPolicyAlignmentStatus.AMBIGUOUS} and not self.conflict_id:raise ValueError("evaluation policy conflict requires conflict record")
        if self.status in {EvaluationPolicyAlignmentStatus.ALIGNED,EvaluationPolicyAlignmentStatus.PAPER_ONLY} and self.resolved_policy!=self.paper_policy:raise ValueError("paper-backed evaluation alignment must retain the paper policy")
        if self.status is EvaluationPolicyAlignmentStatus.CODE_FALLBACK:
            if self.code_policy is None:raise ValueError("code fallback requires an explicit code policy")
            resolved_behavior=self.resolved_policy.model_dump(exclude={"reporting_metrics"})
            code_behavior=self.code_policy.model_dump(exclude={"reporting_metrics"})
            if resolved_behavior!=code_behavior:raise ValueError("code fallback may only replace reporting scope with paper-required metrics")
        if self.status is EvaluationPolicyAlignmentStatus.ALIGNED and self.code_policy is None:raise ValueError("aligned evaluation policy requires code policy")
        if self.status is EvaluationPolicyAlignmentStatus.CONFLICT and (self.paper_policy is None or self.code_policy is None):raise ValueError("paper/code evaluation conflict requires both policies")
        if self.status is EvaluationPolicyAlignmentStatus.CONFLICT:
            if self.resolved_policy!=self.paper_policy:raise ValueError("paper/code conflict must retain PAPER_EXPLICIT as authoritative")
            if "REPOSITORY_EVALUATION_DEVIATION" not in self.warnings:raise ValueError("paper/code conflict must record repository deviation")
        if self.status is EvaluationPolicyAlignmentStatus.SCIENTIFIC_DEFAULT and self.resolved_policy.source is not EvaluationPolicySource.SCIENTIFIC_DEFAULT:raise ValueError("scientific-default alignment requires a scientific default policy")
        if self.adaptation_supported and self.status is not EvaluationPolicyAlignmentStatus.CONFLICT:raise ValueError("adaptation support only applies to paper/code conflict")
        return self
class ExperimentAlignmentRecord(DomainModel):
    alignment_id:NonEmptyStr; paper_experiment_id:NonEmptyStr; repository_implementation_ids:tuple[NonEmptyStr,...]=(); status:AlignmentStatus; confidence:float=Field(ge=0,le=1); reasoning_summary:NonEmptyStr; entrypoint_ids:tuple[NonEmptyStr,...]=(); config_ids:tuple[NonEmptyStr,...]=(); command_ids:tuple[NonEmptyStr,...]=(); parameter_mapping_ids:tuple[NonEmptyStr,...]=(); dataset_mapping_id:NonEmptyStr|None=None; model_mapping_id:NonEmptyStr|None=None; ablation_mapping_ids:tuple[NonEmptyStr,...]=(); metric_mapping_ids:tuple[NonEmptyStr,...]=(); evaluation_policy_alignment_id:NonEmptyStr|None=None; paper_evidence:tuple[EvidenceReference,...]=(); repository_evidence:tuple[EvidenceReference,...]=(); conflict_ids:tuple[NonEmptyStr,...]=()
class AlignmentConflictCandidate(DomainModel): source:AlignmentItemSource; value:JsonValue; evidence:tuple[EvidenceReference,...]
class AlignmentConflict(DomainModel):
    conflict_id:NonEmptyStr; semantic_key:NonEmptyStr; conflict_type:AlignmentConflictType; candidates:tuple[AlignmentConflictCandidate,...]=Field(min_length=1); status:AlignmentConflictStatus=AlignmentConflictStatus.UNRESOLVED; resolution:JsonValue|None=None; resolution_recommendation:NonEmptyStr|None=None; reasoning:NonEmptyStr|None=None
    @model_validator(mode="after")
    def consistent(self):
        if self.conflict_type is not AlignmentConflictType.MISSING_IMPLEMENTATION and len(self.candidates)<2:raise ValueError("alignment conflict requires two candidates except for missing implementation")
        if self.status is AlignmentConflictStatus.RESOLVED and (self.resolution is None or self.resolution not in [x.value for x in self.candidates]):raise ValueError("resolved alignment conflict requires a candidate resolution")
        if self.status is AlignmentConflictStatus.UNRESOLVED and self.resolution is not None:raise ValueError("unresolved alignment conflict cannot have resolution")
        return self
class UnmatchedAlignmentItem(DomainModel): source:AlignmentItemSource; category:NonEmptyStr; item_id:NonEmptyStr; name:NonEmptyStr; reason:NonEmptyStr; evidence:tuple[EvidenceReference,...]=()
class AlignmentAmbiguity(DomainModel): ambiguity_id:NonEmptyStr; paper_item_id:NonEmptyStr; candidate_repository_ids:tuple[NonEmptyStr,...]=Field(min_length=2); reasoning:NonEmptyStr; evidence:tuple[EvidenceReference,...]=()
class AlignmentMetadata(DomainModel): stages_completed:tuple[NonEmptyStr,...]=(); missing_components:tuple[NonEmptyStr,...]=(); warnings:tuple[NonEmptyStr,...]=(); prompt_versions:dict[NonEmptyStr,NonEmptyStr]=Field(default_factory=dict); confidence_method:NonEmptyStr="weighted deterministic signals plus bounded semantic review"
class PaperCodeAlignmentCatalog(DomainModel):
    catalog_id:NonEmptyStr; paper_catalog_id:NonEmptyStr; paper:PaperReference; repository_catalog_id:NonEmptyStr; repository:RepositoryReference; repository_snapshot_id:NonEmptyStr; resolved_commit_sha:NonEmptyStr
    experiment_alignments:tuple[ExperimentAlignmentRecord,...]=(); dataset_mappings:tuple[DatasetAlignment,...]=(); model_mappings:tuple[ModelAlignment,...]=(); parameter_mappings:tuple[ParameterAlignment,...]=(); ablation_mappings:tuple[AblationAlignment,...]=(); metric_mappings:tuple[MetricAlignment,...]=(); evaluation_policy_alignments:tuple[EvaluationPolicyAlignment,...]=(); unmatched_paper_items:tuple[UnmatchedAlignmentItem,...]=(); unmatched_repository_items:tuple[UnmatchedAlignmentItem,...]=(); ambiguities:tuple[AlignmentAmbiguity,...]=(); conflicts:tuple[AlignmentConflict,...]=(); evidence:tuple[EvidenceReference,...]=(); alignment_status:AlignmentAnalysisStatus; alignment_metadata:AlignmentMetadata
    @model_validator(mode="after")
    def shape(self):
        if self.alignment_status is AlignmentAnalysisStatus.FAILED:raise ValueError("failed alignment is represented by exception")
        if self.alignment_status is AlignmentAnalysisStatus.PARTIAL and not self.alignment_metadata.missing_components:raise ValueError("partial alignment requires missing components")
        groups=((self.experiment_alignments,"alignment_id"),(self.dataset_mappings,"alignment_id"),(self.model_mappings,"alignment_id"),(self.parameter_mappings,"alignment_id"),(self.ablation_mappings,"alignment_id"),(self.metric_mappings,"alignment_id"),(self.evaluation_policy_alignments,"alignment_id"),(self.conflicts,"conflict_id"),(self.ambiguities,"ambiguity_id"))
        for values,field in groups:
            ids=[getattr(x,field) for x in values]
            if len(ids)!=len(set(ids)):raise ValueError(f"duplicate alignment identifier: {field}")
        return self
class AlignmentTrace(DomainModel):
    alignment_id:NonEmptyStr; paper_catalog_id:NonEmptyStr; repository_snapshot_id:NonEmptyStr; resolved_commit_sha:NonEmptyStr; started_at:datetime; finished_at:datetime; candidate_counts:dict[NonEmptyStr,int]=Field(default_factory=dict); selected_contexts:tuple[NonEmptyStr,...]=(); primary_calls:int=Field(ge=0); fast_calls:int=Field(ge=0); repair_attempts:int=Field(ge=0); prompt_versions:dict[NonEmptyStr,NonEmptyStr]; usage:tuple[JsonValue,...]=(); warnings:tuple[NonEmptyStr,...]=(); status:AlignmentAnalysisStatus
