from pydantic import BaseModel,ConfigDict
from backend.app.domain import *
from backend.app.llm import LLMCallMetadata
class AlignmentCandidate(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    candidate_id:str;category:str;paper_item_id:str;repository_item_ids:tuple[str,...];score:float;signals:tuple[str,...];paper_evidence:tuple[EvidenceReference,...]=();repository_evidence:tuple[EvidenceReference,...]=()
class CandidateDecision(BaseModel): candidate_id:str;relevant:bool;reason:str
class CandidateClassification(BaseModel):decisions:tuple[CandidateDecision,...]
class AlignmentContextItem(BaseModel):locator:str;kind:str;text:str;score:int=0
class AlignmentContext(BaseModel):items:tuple[AlignmentContextItem,...];selected_contexts:tuple[str,...];llm_metadata:tuple[LLMCallMetadata,...]=()
class AlignmentStageExtraction(BaseModel):
    experiment_alignments:tuple[ExperimentAlignmentRecord,...]=();dataset_mappings:tuple[DatasetAlignment,...]=();model_mappings:tuple[ModelAlignment,...]=();parameter_mappings:tuple[ParameterAlignment,...]=();ablation_mappings:tuple[AblationAlignment,...]=();metric_mappings:tuple[MetricAlignment,...]=();conflicts:tuple[AlignmentConflict,...]=();warnings:tuple[str,...]=();missing_components:tuple[str,...]=()
class AlignmentCatalogReview(BaseModel):valid:bool;missing_components:tuple[str,...]=();warnings:tuple[str,...]=()
class AlignmentResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    catalog:PaperCodeAlignmentCatalog;trace:AlignmentTrace
class PromptSpec(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    name:str;version:str;system:str;task:str
