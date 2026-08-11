"""The bounded, read-only Paper Experiment Extraction Agent workflow."""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
from pydantic import BaseModel,ConfigDict,Field
from backend.app.domain import ExtractionStatus, ExtractionTrace, FigureObservation, PaperDocument
from backend.app.llm import LLMCallMetadata, LLMRole, LLMRouter, StructuredOutputError
from .catalog import CatalogMerger,CatalogValidator,CatalogValidationError,PaperExtractionError
from .context import ContextBuilder,DeterministicTableExtractor
from .evidence import EvidenceValidationError,EvidenceValidator
from .prompt_registry import PromptRegistry
from .schemas import CatalogReview,ExtractionResult,StageExtraction

class AgentSettings(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    max_repair_attempts:int=Field(default=2,ge=0,le=4)
    primary_provider:str="deepseek"; primary_model:str="deepseek-v4-pro"
    fast_provider:str="qwen"; fast_model:str="qwen3.6-flash"
    figure_artifact_root:Path=Path("workspace/paper-assets")

class PaperExperimentExtractionAgent:
    """Coordinates deterministic extraction and two LLM roles; it has no tools."""
    STAGES=("overview","datasets_and_models","training_and_evaluation","main_and_baseline_experiments","ablation_sensitivity_robustness_efficiency","paper_claims")
    def __init__(self,router:LLMRouter,*,settings:AgentSettings|None=None,context_builder:ContextBuilder|None=None,evidence_validator:EvidenceValidator|None=None,catalog_validator:CatalogValidator|None=None,prompts:PromptRegistry|None=None):
        self.router=router; self.settings=settings or AgentSettings(); self.prompts=prompts or PromptRegistry()
        self.context_builder=context_builder or ContextBuilder(router,prompts=self.prompts)
        self.evidence_validator=evidence_validator or EvidenceValidator(); self.catalog_validator=catalog_validator or CatalogValidator(self.evidence_validator)
        self.merger=CatalogMerger(); self.tables=DeterministicTableExtractor()
    def extract(self,document:PaperDocument)->ExtractionResult:
        started=datetime.now(timezone.utc); extraction_id=f"extract:{document.document_id}"
        context=self.context_builder.build(document); metadata=list(context.llm_metadata); repairs=0; warnings=[]; missing=[]; stages=[]
        table_facts=tuple(x for x in self.tables.extract(document) if x.table_id in context.selected_tables)
        base=json.dumps({"document_id":document.document_id,"context":[x.model_dump() for x in context.items],"deterministic_table_facts":[x.model_dump() for x in table_facts]},ensure_ascii=False)
        for stage_name in self.STAGES:
            stage,stage_metadata,stage_repairs,error=self._run_stage(stage_name,base,document)
            metadata.extend(stage_metadata); repairs+=stage_repairs
            if stage is None:
                missing.append(stage_name); warnings.append(error or f"{stage_name} failed")
            else: stages.append(stage)
        observations=[]
        for figure_id in context.selected_figures:
            observation,call,error=self._observe_figure(document,figure_id)
            if call: metadata.append(call)
            if observation: observations.append(observation)
            else: missing.append(f"figure:{figure_id}"); warnings.append(error or f"figure {figure_id} failed")
        if observations:
            figure_content=base+"\nSTRUCTURED FIGURE OBSERVATIONS:\n"+json.dumps([x.model_dump(mode="json") for x in observations],ensure_ascii=False)
            stage,calls,count,error=self._run_stage("figure_scientific_interpretation",figure_content,document)
            metadata.extend(calls); repairs+=count
            if stage: stages.append(stage)
            else: missing.append("figure_scientific_interpretation"); warnings.append(error or "figure interpretation failed")
        if not stages or not any(stage.experiments for stage in stages): raise PaperExtractionError("extraction produced no experiment records")
        catalog=self.merger.merge(document,tuple(stages),missing=tuple(missing),warnings=tuple(warnings),figure_observations=tuple(observations))
        try: self.catalog_validator.validate(catalog,document)
        except CatalogValidationError as exc: raise PaperExtractionError(f"catalog validation failed: {exc}") from exc
        review_prompt=self.prompts.get("catalog_review")
        try:
            review=self.router.for_role(LLMRole.FAST).generate_structured(role=LLMRole.FAST,system_prompt=review_prompt.system,content=f"{review_prompt.task}\nCATALOG SUMMARY:\n{catalog.model_dump_json(exclude={'evidence'})}",output_schema=CatalogReview,prompt_name=review_prompt.name,prompt_version=review_prompt.version)
            metadata.append(review.metadata)
            if not review.value.valid or review.value.missing_components:
                new_missing=tuple(dict.fromkeys((*catalog.extraction_metadata.missing_components,*review.value.missing_components)))
                catalog=catalog.model_copy(update={"extraction_status":ExtractionStatus.PARTIAL,"extraction_metadata":catalog.extraction_metadata.model_copy(update={"missing_components":new_missing,"warnings":tuple(dict.fromkeys((*catalog.extraction_metadata.warnings,*review.value.warnings)))})})
        except Exception as exc:
            warning=f"catalog review unavailable: {exc}"; warnings.append(warning)
            missing_components=tuple(dict.fromkeys((*catalog.extraction_metadata.missing_components,"catalog_review")))
            catalog=catalog.model_copy(update={"extraction_status":ExtractionStatus.PARTIAL,"extraction_metadata":catalog.extraction_metadata.model_copy(update={"missing_components":missing_components,"warnings":tuple(dict.fromkeys((*catalog.extraction_metadata.warnings,warning)))})})
        finished=datetime.now(timezone.utc)
        trace=ExtractionTrace(extraction_id=extraction_id,document_id=document.document_id,started_at=started,finished_at=finished,primary_provider=self.settings.primary_provider,primary_model=self.settings.primary_model,fast_provider=self.settings.fast_provider,fast_model=self.settings.fast_model,prompt_versions={name:"v1" for name in ("context_classification","stage_extraction","figure_observation","catalog_review","repair")},selected_sections=context.selected_sections,selected_tables=context.selected_tables,selected_figures=context.selected_figures,vision_calls=sum(x.role is LLMRole.VISION for x in metadata),primary_calls=sum(x.role is LLMRole.PRIMARY for x in metadata),fast_calls=sum(x.role is LLMRole.FAST for x in metadata),repair_attempts=repairs,warnings=tuple(dict.fromkeys((*warnings,*catalog.extraction_metadata.warnings))),usage_metadata=tuple(x.model_dump(mode="json") for x in metadata),status=catalog.extraction_status)
        return ExtractionResult(catalog=catalog,trace=trace)
    def _run_stage(self,name,content,document):
        prompt=self.prompts.get("stage_extraction"); calls=[]; repairs=0; issue=None
        request=f"{prompt.task}\nSTAGE: {name}\nUNTRUSTED PAPER CONTEXT:\n{content}"
        try:
            response=self.router.for_role(LLMRole.PRIMARY).generate_structured(role=LLMRole.PRIMARY,system_prompt=prompt.system,content=request,output_schema=StageExtraction,prompt_name=prompt.name,prompt_version=prompt.version); calls.append(response.metadata); candidate=response.value
            self._validate_stage(candidate,document); return candidate,calls,repairs,None
        except Exception as exc: issue=str(exc)
        for attempt in range(self.settings.max_repair_attempts):
            repairs+=1; role=LLMRole.FAST if attempt==0 else LLMRole.PRIMARY; repair_prompt=self.prompts.get("repair")
            try:
                response=self.router.for_role(role).generate_structured(role=role,system_prompt=repair_prompt.system,content=f"{repair_prompt.task}\nSTAGE: {name}\nVALIDATION ISSUE: {issue}\nUNTRUSTED CONTEXT:\n{content}",output_schema=StageExtraction,prompt_name=repair_prompt.name,prompt_version=repair_prompt.version); calls.append(response.metadata); candidate=response.value
                self._validate_stage(candidate,document); return candidate,calls,repairs,None
            except Exception as exc: issue=str(exc)
        return None,calls,repairs,f"{name} retry exhaustion: {issue}"
    def _validate_stage(self,stage,document):
        evidence=list(stage.evidence)
        for entity in (*stage.datasets,*stage.model_variants): evidence.extend(entity.evidence)
        for parameter in (*stage.training_parameters,*stage.evaluation_parameters): evidence.extend(parameter.evidence)
        for record in stage.experiments:
            evidence.extend(record.evidence)
            for parameter in record.parameters: evidence.extend(parameter.evidence); self.evidence_validator.validate_parameter(parameter,document)
            for claim in record.claims: evidence.extend(claim.evidence); self.evidence_validator.validate_claim(claim,document)
        for parameter in (*stage.training_parameters,*stage.evaluation_parameters): self.evidence_validator.validate_parameter(parameter,document)
        for claim in stage.claims: evidence.extend(claim.evidence); self.evidence_validator.validate_claim(claim,document)
        self.evidence_validator.validate_all(evidence,document)
    def _observe_figure(self,document,figure_id):
        figure=next(x for x in document.figures if x.figure_id==figure_id)
        if not figure.image_reference: return None,None,"figure has no extracted image"
        image=self.settings.figure_artifact_root/figure.image_reference; prompt=self.prompts.get("figure_observation")
        try:
            response=self.router.for_role(LLMRole.VISION).generate_structured(role=LLMRole.VISION,system_prompt=prompt.system,content=f"{prompt.task}\nFigure id: {figure_id}\nUNTRUSTED CAPTION: {figure.caption}\nUse evidence locator figure:{figure_id}",images=(str(image),),output_schema=FigureObservation,prompt_name=prompt.name,prompt_version=prompt.version)
            self.evidence_validator.validate_all(response.value.evidence,document); return response.value,response.metadata,None
        except Exception as exc: return None,None,str(exc)
