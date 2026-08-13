"""Internal, bounded schemas for repository analysis."""
from __future__ import annotations
from pydantic import BaseModel,ConfigDict
from backend.app.domain import EvidenceReference,RepositoryAnalysisCatalog,RepositoryAnalysisTrace,RepositoryComponentRecord,RepositoryConflict,RepositoryEvaluationPolicyRecord,RepositoryExperimentImplementation,RepositoryFact,RepositorySnapshot
from backend.app.llm import LLMCallMetadata

class RepositoryContextItem(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    locator:str; kind:str; text:str; score:int=0
class RepositoryAnalysisContext(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    items:tuple[RepositoryContextItem,...]; selected_files:tuple[str,...]; selected_symbols:tuple[str,...]; llm_metadata:tuple[LLMCallMetadata,...]=()
class FileDecision(BaseModel): path:str; relevant:bool; reason:str
class FileClassification(BaseModel): decisions:tuple[FileDecision,...]
class RepositoryStageExtraction(BaseModel):
    components:tuple[RepositoryComponentRecord,...]=(); implementations:tuple[RepositoryExperimentImplementation,...]=(); evaluation_policies:tuple[RepositoryEvaluationPolicyRecord,...]=(); conflicts:tuple[RepositoryConflict,...]=(); facts:tuple[RepositoryFact,...]=(); evidence:tuple[EvidenceReference,...]=(); missing_components:tuple[str,...]=(); warnings:tuple[str,...]=()
class RepositoryCatalogReview(BaseModel): valid:bool; missing_components:tuple[str,...]=(); warnings:tuple[str,...]=()
class RepositoryAnalysisResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    catalog:RepositoryAnalysisCatalog; trace:RepositoryAnalysisTrace; snapshot:RepositorySnapshot
class PromptSpec(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    name:str; version:str; system:str; task:str
