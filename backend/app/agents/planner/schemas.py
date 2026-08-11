from pydantic import BaseModel,ConfigDict
from backend.app.domain import PlanningTrace,ReproductionExecutionPlan
from backend.app.llm import LLMCallMetadata

class SemanticSelection(BaseModel):
    model_config=ConfigDict(extra="forbid")
    paper_experiment_id:str
    entrypoint_id:str
    config_ids:tuple[str,...]=()
    reason:str

class SemanticSelectionSet(BaseModel):
    model_config=ConfigDict(extra="forbid")
    selections:tuple[SemanticSelection,...]=()

class PlanReview(BaseModel):
    model_config=ConfigDict(extra="forbid")
    valid:bool
    warnings:tuple[str,...]=()

class PlanningResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    plan:ReproductionExecutionPlan
    trace:PlanningTrace

class PromptSpec(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    name:str;version:str;system:str;task:str

