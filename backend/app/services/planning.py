"""Configuration and typed failures for reproduction planning."""
from pydantic import BaseModel,ConfigDict,Field

class ReproductionPlanningError(RuntimeError): pass
class PlanningValidationError(ReproductionPlanningError): pass
class PlanningSettings(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    max_repair_attempts:int=Field(default=2,ge=0,le=4)
    max_semantic_candidates:int=Field(default=12,ge=2,le=50)

