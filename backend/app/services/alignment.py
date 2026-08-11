from pydantic import BaseModel,ConfigDict,Field
class PaperCodeAlignmentError(RuntimeError):pass
class AlignmentValidationError(PaperCodeAlignmentError):pass
class AlignmentSettings(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    max_candidates_per_experiment:int=Field(default=8,ge=1,le=30);max_context_items:int=Field(default=30,ge=5);max_context_chars:int=Field(default=30000,ge=2000);fast_candidate_threshold:int=Field(default=40,ge=1);max_repair_attempts:int=Field(default=2,ge=0,le=4)
