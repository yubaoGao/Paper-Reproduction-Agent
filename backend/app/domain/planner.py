"""Provider- and runtime-neutral reproduction planning models."""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import Field,JsonValue,model_validator
from .experiment import DomainModel,ExperimentSpecification,NonEmptyStr
from .reproduction import EvidenceReference,PaperReference
from .repository import RepositoryReference

class ReproductionPolicy(str,Enum):STRICT="strict";PAPER_FAITHFUL="paper_faithful";CODE_FAITHFUL="code_faithful"
class PlanStatus(str,Enum):READY="ready";NEEDS_CONFIRMATION="needs_confirmation";BLOCKED="blocked"
class DecisionSource(str,Enum):USER="user";PAPER="paper";REPOSITORY="repository";ALIGNMENT="alignment";PLANNER_DECISION="planner_decision";MATCHED="matched";REPOSITORY_FILL="repository_fill"
class BlockerSeverity(str,Enum):BLOCKING="blocking";CONFIRMATION="confirmation"
class PlannerDecision(DomainModel):
    decision_id:NonEmptyStr;experiment_id:NonEmptyStr|None=None;semantic_key:NonEmptyStr;selected_value:JsonValue|None=None;source:DecisionSource;alternative_values:tuple[JsonValue,...]=();policy:ReproductionPolicy;reason:NonEmptyStr;paper_evidence:tuple[EvidenceReference,...]=();repository_evidence:tuple[EvidenceReference,...]=();alignment_reference:NonEmptyStr|None=None;confidence:float=Field(ge=0,le=1);requires_confirmation:bool=False
class PlanBlocker(DomainModel):blocker_id:NonEmptyStr;code:NonEmptyStr;message:NonEmptyStr;severity:BlockerSeverity;paper_experiment_id:NonEmptyStr|None=None;alignment_reference:NonEmptyStr|None=None;conflict_id:NonEmptyStr|None=None
class ExperimentDependency(DomainModel):
    experiment_id:NonEmptyStr;depends_on_experiment_ids:tuple[NonEmptyStr,...];reason:NonEmptyStr
    @model_validator(mode="after")
    def valid_edges(self):
        if self.experiment_id in self.depends_on_experiment_ids:raise ValueError("experiment cannot depend on itself")
        if len(set(self.depends_on_experiment_ids))!=len(self.depends_on_experiment_ids):raise ValueError("dependency ids must be unique")
        return self
class UnresolvedPlanItem(DomainModel):item_id:NonEmptyStr;category:NonEmptyStr;paper_experiment_id:NonEmptyStr|None=None;reason:NonEmptyStr;candidate_ids:tuple[NonEmptyStr,...]=();requires_confirmation:bool=False
class PlanningOverrides(DomainModel):
    parameters:dict[NonEmptyStr,JsonValue]=Field(default_factory=dict);dataset_bindings:dict[NonEmptyStr,NonEmptyStr]=Field(default_factory=dict);entrypoint_ids:dict[NonEmptyStr,NonEmptyStr]=Field(default_factory=dict);config_ids:dict[NonEmptyStr,tuple[NonEmptyStr,...]]=Field(default_factory=dict);dependencies:dict[NonEmptyStr,tuple[NonEmptyStr,...]]=Field(default_factory=dict)
class PlanningMetadata(DomainModel):stages_completed:tuple[NonEmptyStr,...]=();warnings:tuple[NonEmptyStr,...]=();prompt_versions:dict[NonEmptyStr,NonEmptyStr]=Field(default_factory=dict)
class ReproductionExecutionPlan(DomainModel):
    plan_id:NonEmptyStr;reproduction_specification_id:NonEmptyStr;paper:PaperReference;repository:RepositoryReference;repository_snapshot_id:NonEmptyStr;resolved_commit_sha:NonEmptyStr;alignment_catalog_id:NonEmptyStr;policy:ReproductionPolicy;target_experiment_ids:tuple[NonEmptyStr,...];experiments:tuple[ExperimentSpecification,...]=();execution_order:tuple[NonEmptyStr,...]=();dependencies:tuple[ExperimentDependency,...]=();shared_settings:dict[NonEmptyStr,JsonValue]=Field(default_factory=dict);warnings:tuple[NonEmptyStr,...]=();blockers:tuple[PlanBlocker,...]=();decisions:tuple[PlannerDecision,...]=();unresolved_items:tuple[UnresolvedPlanItem,...]=();status:PlanStatus;metadata:PlanningMetadata
    @model_validator(mode="after")
    def consistent(self):
        ids=[x.id for x in self.experiments]
        if len(ids)!=len(set(ids)):raise ValueError("planned experiment ids must be unique")
        if len(self.execution_order)!=len(set(self.execution_order)) or not set(self.execution_order)<=set(ids):raise ValueError("execution order contains duplicate or unknown experiment")
        positions={value:index for index,value in enumerate(self.execution_order)}
        for dependency in self.dependencies:
            if dependency.experiment_id not in ids or not set(dependency.depends_on_experiment_ids)<=set(ids):raise ValueError("dependency references unknown experiment")
            if self.execution_order and any(positions[parent]>=positions[dependency.experiment_id] for parent in dependency.depends_on_experiment_ids):raise ValueError("execution order violates dependencies")
        if self.status is PlanStatus.READY and (self.blockers or any(x.requires_confirmation for x in self.decisions) or any(x.requires_confirmation for x in self.unresolved_items)):raise ValueError("ready plan cannot contain blockers or confirmation items")
        if self.status is PlanStatus.BLOCKED and not self.blockers:raise ValueError("blocked plan requires blockers")
        if self.status is PlanStatus.NEEDS_CONFIRMATION and (self.blockers or not (any(x.requires_confirmation for x in self.decisions) or any(x.requires_confirmation for x in self.unresolved_items))):raise ValueError("needs-confirmation plan requires non-blocking confirmation items")
        return self
class PlanningTrace(DomainModel):
    plan_id:NonEmptyStr;reproduction_specification_id:NonEmptyStr;paper_catalog_id:NonEmptyStr;repository_catalog_id:NonEmptyStr;alignment_catalog_id:NonEmptyStr;repository_snapshot_id:NonEmptyStr;resolved_commit_sha:NonEmptyStr;policy:ReproductionPolicy;started_at:datetime;finished_at:datetime;deterministic_decision_count:int=Field(ge=0);primary_calls:int=Field(ge=0);fast_calls:int=Field(ge=0);repair_attempts:int=Field(ge=0);prompt_versions:dict[NonEmptyStr,NonEmptyStr];warnings:tuple[NonEmptyStr,...]=();blocker_codes:tuple[NonEmptyStr,...]=();usage:tuple[JsonValue,...]=();status:PlanStatus
    @model_validator(mode="after")
    def ordered_time(self):
        if self.finished_at<self.started_at:raise ValueError("planning trace time range is inverted")
        return self
