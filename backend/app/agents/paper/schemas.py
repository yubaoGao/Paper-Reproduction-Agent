"""Bounded workflow schemas internal to paper extraction."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
from backend.app.domain import (
    CatalogEntity, EvidenceReference, ExtractionTrace, FigureObservation, PaperClaim,
    PaperExperimentCatalog, PaperExperimentRecord, ReproductionParameter,
)
from backend.app.llm import LLMCallMetadata

class ContextItem(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    locator: str; kind: str; text: str; score: int=0
class ExtractionContext(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    document_id: str
    items: tuple[ContextItem,...]
    selected_sections: tuple[str,...]=()
    selected_tables: tuple[str,...]=()
    selected_figures: tuple[str,...]=()
    llm_metadata: tuple[LLMCallMetadata,...]=()
class ContextDecision(BaseModel):
    locator: str; relevant: bool; reason: str
class ContextClassificationResult(BaseModel):
    decisions: tuple[ContextDecision,...]
class TableFact(BaseModel):
    table_id: str; row_label: str; metric: str; value: float; locator: str; raw_value: str
class StageExtraction(BaseModel):
    datasets: tuple[CatalogEntity,...]=()
    model_variants: tuple[CatalogEntity,...]=()
    experiments: tuple[PaperExperimentRecord,...]=()
    training_parameters: tuple[ReproductionParameter,...]=()
    evaluation_parameters: tuple[ReproductionParameter,...]=()
    claims: tuple[PaperClaim,...]=()
    evidence: tuple[EvidenceReference,...]=()
    missing_components: tuple[str,...]=()
    warnings: tuple[str,...]=()
class CatalogReview(BaseModel):
    valid: bool
    missing_components: tuple[str,...]=()
    warnings: tuple[str,...]=()
class GoalSemanticSelection(BaseModel):
    experiment_ids: tuple[str,...]
    metric_names: tuple[str,...]=()
    ambiguous: bool=False
    reason: str=""
    clarification_questions: tuple[str,...]=()
class ExtractionResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    catalog: PaperExperimentCatalog
    trace: ExtractionTrace
class PromptSpec(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    name: str; version: str; system: str; task: str
