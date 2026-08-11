"""Provider-independent paper experiment intelligence models."""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import Field, JsonValue, model_validator
from .experiment import DomainModel, NonEmptyStr
from .reproduction import EvidenceReference, PaperClaim, PaperReference, ReproductionParameter, ReproductionSpecification

class ExperimentType(str, Enum):
    MAIN="main"; BASELINE="baseline"; ABLATION="ablation"; SENSITIVITY="sensitivity"; ROBUSTNESS="robustness"; EFFICIENCY="efficiency"; OTHER="other"
class ExtractionStatus(str, Enum): COMPLETE="complete"; PARTIAL="partial"; FAILED="failed"
class ConflictType(str, Enum): VALUE_MISMATCH="value_mismatch"; CLASSIFICATION_MISMATCH="classification_mismatch"; ENTITY_MISMATCH="entity_mismatch"; OTHER="other"
class ConflictStatus(str, Enum): RESOLVED="resolved"; UNRESOLVED="unresolved"

class CatalogEntity(DomainModel):
    canonical_name: NonEmptyStr
    aliases: tuple[NonEmptyStr,...]=()
    evidence: tuple[EvidenceReference,...]=()
    @model_validator(mode="after")
    def unique_names(self):
        values=[value.casefold() for value in (self.canonical_name,*self.aliases)]
        if len(values)!=len(set(values)): raise ValueError("entity names and aliases must be unique")
        return self

class PaperExperimentRecord(DomainModel):
    experiment_id: NonEmptyStr
    name: NonEmptyStr
    experiment_type: ExperimentType
    dataset: NonEmptyStr|None=None
    model: NonEmptyStr|None=None
    variant: NonEmptyStr|None=None
    parent_experiment_id: NonEmptyStr|None=None
    conditions: dict[NonEmptyStr,JsonValue]=Field(default_factory=dict)
    parameters: tuple[ReproductionParameter,...]=()
    claims: tuple[PaperClaim,...]=()
    evidence: tuple[EvidenceReference,...]=()
    source_sections: tuple[NonEmptyStr,...]=()
    source_tables: tuple[NonEmptyStr,...]=()
    source_figures: tuple[NonEmptyStr,...]=()
    @model_validator(mode="after")
    def validate_record(self):
        if self.experiment_type is ExperimentType.ABLATION and not self.variant: raise ValueError("ablation experiment requires a variant")
        if len({x.id for x in self.claims})!=len(self.claims): raise ValueError("experiment claim ids must be unique")
        if len({x.name.casefold() for x in self.parameters})!=len(self.parameters): raise ValueError("parameter names must be unique")
        return self

class ConflictCandidate(DomainModel):
    value: JsonValue
    evidence: tuple[EvidenceReference,...]
class ExtractionConflict(DomainModel):
    conflict_id: NonEmptyStr
    semantic_key: NonEmptyStr
    conflict_type: ConflictType
    candidates: tuple[ConflictCandidate,...]=Field(min_length=2)
    status: ConflictStatus=ConflictStatus.UNRESOLVED
    resolution: JsonValue|None=None
    reasoning: NonEmptyStr|None=None
    @model_validator(mode="after")
    def validate_resolution(self):
        if self.status is ConflictStatus.RESOLVED and (self.resolution is None or self.reasoning is None): raise ValueError("resolved conflict requires resolution and reasoning")
        if self.status is ConflictStatus.UNRESOLVED and self.resolution is not None: raise ValueError("unresolved conflict cannot have resolution")
        return self

class FigureObservation(DomainModel):
    figure_id: NonEmptyStr
    summary: NonEmptyStr
    visible_labels: tuple[NonEmptyStr,...]=()
    reported_metrics: dict[NonEmptyStr,float|None]=Field(default_factory=dict)
    trends: tuple[NonEmptyStr,...]=()
    uncertainties: tuple[NonEmptyStr,...]=()
    evidence: tuple[EvidenceReference,...]
    @model_validator(mode="after")
    def evidence_matches(self):
        if not any(x.locator==f"figure:{self.figure_id}" for x in self.evidence): raise ValueError("figure observation requires matching evidence")
        return self

class ExtractionMetadata(DomainModel):
    stages_completed: tuple[NonEmptyStr,...]=()
    missing_components: tuple[NonEmptyStr,...]=()
    warnings: tuple[NonEmptyStr,...]=()
class PaperExperimentCatalog(DomainModel):
    catalog_id: NonEmptyStr
    document_id: NonEmptyStr
    paper: PaperReference
    datasets: tuple[CatalogEntity,...]=()
    model_variants: tuple[CatalogEntity,...]=()
    experiments: tuple[PaperExperimentRecord,...]=()
    training_parameters: tuple[ReproductionParameter,...]=()
    evaluation_parameters: tuple[ReproductionParameter,...]=()
    paper_claims: tuple[PaperClaim,...]=()
    evidence: tuple[EvidenceReference,...]=()
    conflicts: tuple[ExtractionConflict,...]=()
    figure_observations: tuple[FigureObservation,...]=()
    extraction_status: ExtractionStatus
    extraction_metadata: ExtractionMetadata
    @model_validator(mode="after")
    def validate_shape(self):
        ids=[x.experiment_id for x in self.experiments]
        if len(ids)!=len(set(ids)): raise ValueError("experiment ids must be unique")
        claims=[x.id for x in self.paper_claims]
        if len(claims)!=len(set(claims)): raise ValueError("claim ids must be unique")
        known=set(ids)
        if any(x.parent_experiment_id and x.parent_experiment_id not in known for x in self.experiments): raise ValueError("dangling parent experiment")
        if self.extraction_status is ExtractionStatus.FAILED: raise ValueError("failed extraction is represented by exception")
        if self.extraction_status is ExtractionStatus.PARTIAL and not self.extraction_metadata.missing_components: raise ValueError("partial extraction requires missing components")
        return self

class ExtractionTrace(DomainModel):
    extraction_id: NonEmptyStr
    document_id: NonEmptyStr
    started_at: datetime
    finished_at: datetime
    primary_provider: NonEmptyStr
    primary_model: NonEmptyStr
    fast_provider: NonEmptyStr
    fast_model: NonEmptyStr
    prompt_versions: dict[NonEmptyStr,NonEmptyStr]
    selected_sections: tuple[NonEmptyStr,...]=()
    selected_tables: tuple[NonEmptyStr,...]=()
    selected_figures: tuple[NonEmptyStr,...]=()
    vision_calls: int=Field(default=0,ge=0)
    primary_calls: int=Field(default=0,ge=0)
    fast_calls: int=Field(default=0,ge=0)
    repair_attempts: int=Field(default=0,ge=0)
    warnings: tuple[NonEmptyStr,...]=()
    usage_metadata: tuple[JsonValue,...]=()
    status: ExtractionStatus
    @model_validator(mode="after")
    def times_ordered(self):
        if self.finished_at<self.started_at: raise ValueError("trace time range is inverted")
        return self

class GoalResolutionStatus(str, Enum): RESOLVED="resolved"; AMBIGUOUS="ambiguous"; NOT_FOUND="not_found"
class UserReproductionGoal(DomainModel): goal_id: NonEmptyStr; text: NonEmptyStr
class GoalResolutionResult(DomainModel):
    status: GoalResolutionStatus
    specification: ReproductionSpecification|None=None
    candidate_experiment_ids: tuple[NonEmptyStr,...]=()
    reason: NonEmptyStr|None=None
    clarification_questions: tuple[NonEmptyStr,...]=()
    @model_validator(mode="after")
    def validate_outcome(self):
        if self.status is GoalResolutionStatus.RESOLVED and self.specification is None: raise ValueError("resolved goal requires specification")
        if self.status is not GoalResolutionStatus.RESOLVED and self.specification is not None: raise ValueError("unresolved goal cannot have specification")
        if self.status is GoalResolutionStatus.AMBIGUOUS and not self.candidate_experiment_ids: raise ValueError("ambiguous goal requires candidates")
        return self
