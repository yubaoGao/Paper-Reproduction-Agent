"""Bounded Paper-Code Alignment Agent with no execution tools."""
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime,timezone
from backend.app.domain import AlignmentAnalysisStatus,AlignmentTrace
from backend.app.llm import LLMRole,LLMRouter
from backend.app.services import AlignmentSettings,PaperCodeAlignmentError
from .candidates import AlignmentCandidateGenerator
from .catalog import AlignmentCatalogMerger,PaperCodeAlignmentValidator
from .context import AlignmentContextBuilder
from .deterministic import DeterministicAlignmentBuilder
from .evidence import AlignmentEvidenceValidator
from .prompt_registry import AlignmentPromptRegistry
from .schemas import AlignmentCatalogReview,AlignmentResult,AlignmentStageExtraction

class PaperCodeAlignmentAgent:
    STAGES=("dataset_model","experiment","association","parameters","ablations","metrics","conflicts")
    def __init__(self,router:LLMRouter,*,settings=None,candidate_generator=None,prompts=None,evidence_validator=None,catalog_validator=None):
        self.router=router;self.settings=settings or AlignmentSettings();self.prompts=prompts or AlignmentPromptRegistry();self.candidates=candidate_generator or AlignmentCandidateGenerator(self.settings.max_candidates_per_experiment);self.deterministic=DeterministicAlignmentBuilder();self.evidence=evidence_validator or AlignmentEvidenceValidator();self.validator=catalog_validator or PaperCodeAlignmentValidator(self.evidence);self.merger=AlignmentCatalogMerger();self.context=AlignmentContextBuilder(router,self.prompts,max_items=self.settings.max_context_items,max_chars=self.settings.max_context_chars,fast_threshold=self.settings.fast_candidate_threshold)
    def align(self,paper_catalog,repository_catalog,*,reproduction_specification=None,paper_document=None,repository_snapshot=None,static_analysis=None):
        started=datetime.now(timezone.utc);calls=[];repairs=0;warnings=[];missing=[];stages=[];selected=[]
        try:
            candidates=self.candidates.generate(paper_catalog,repository_catalog);deterministic=self.deterministic.build(paper_catalog,repository_catalog,candidates)
            for stage_name in self.STAGES:
                try:context=self.context.build(stage_name,paper_catalog,repository_catalog,candidates,deterministic,reproduction_specification);calls.extend(context.llm_metadata);selected.extend(context.selected_contexts)
                except Exception as exc:missing.append(stage_name);warnings.append(f"{stage_name} context failed: {exc}");continue
                value,stage_calls,count,error=self._stage(stage_name,context,paper_catalog,repository_catalog,paper_document,repository_snapshot,static_analysis);calls.extend(stage_calls);repairs+=count
                if value is None:missing.append(stage_name);warnings.append(error or f"{stage_name} failed")
                else:stages.append((stage_name,value));warnings.extend(value.warnings)
            catalog=self.merger.merge(paper_catalog,repository_catalog,deterministic,tuple(stages),tuple(missing),tuple(warnings));self.validator.validate(catalog,paper_catalog,repository_catalog,paper_document=paper_document,repository_snapshot=repository_snapshot,static_analysis=static_analysis)
            catalog,review_calls,review_error=self._review(catalog);calls.extend(review_calls)
            if review_error:
                missing_components=tuple(dict.fromkeys((*catalog.alignment_metadata.missing_components,"catalog_review")));catalog=catalog.model_copy(update={"alignment_status":AlignmentAnalysisStatus.PARTIAL,"alignment_metadata":catalog.alignment_metadata.model_copy(update={"missing_components":missing_components,"warnings":tuple(dict.fromkeys((*catalog.alignment_metadata.warnings,review_error)))})})
            finished=datetime.now(timezone.utc);counts=Counter(x.category for x in candidates)
            trace=AlignmentTrace(alignment_id=f"alignment:{catalog.catalog_id}",paper_catalog_id=paper_catalog.catalog_id,repository_snapshot_id=repository_catalog.snapshot_id,resolved_commit_sha=repository_catalog.resolved_commit_sha,started_at=started,finished_at=finished,candidate_counts=dict(counts),selected_contexts=tuple(dict.fromkeys(selected)),primary_calls=sum(x.role is LLMRole.PRIMARY for x in calls),fast_calls=sum(x.role is LLMRole.FAST for x in calls),repair_attempts=repairs,prompt_versions={x:"v1" for x in ("candidate_classification","stage_alignment","repair","catalog_review")},usage=tuple(x.model_dump(mode="json") for x in calls),warnings=catalog.alignment_metadata.warnings,status=catalog.alignment_status)
            return AlignmentResult(catalog=catalog,trace=trace)
        except PaperCodeAlignmentError:raise
        except Exception as exc:raise PaperCodeAlignmentError(f"paper-code alignment failed: {exc}") from exc
    def _stage(self,name,context,paper,repository,paper_document,snapshot,static):
        calls=[];repairs=0;issue="";payload=json.dumps([x.model_dump() for x in context.items],ensure_ascii=False);prompt=self.prompts.get("stage_alignment")
        try:
            response=self.router.for_role(LLMRole.PRIMARY).generate_structured(role=LLMRole.PRIMARY,system_prompt=prompt.system,content=f"{prompt.task}\nSTAGE: {name}\nUNTRUSTED BOUNDED CONTEXT:\n{payload}",output_schema=AlignmentStageExtraction,prompt_name=prompt.name,prompt_version=prompt.version);calls.append(response.metadata);self._validate_stage(response.value,paper,repository,paper_document,snapshot,static);return response.value,calls,repairs,None
        except Exception as exc:issue=str(exc)
        for attempt in range(self.settings.max_repair_attempts):
            repairs+=1;role=LLMRole.FAST if attempt==0 else LLMRole.PRIMARY;prompt=self.prompts.get("repair")
            try:
                response=self.router.for_role(role).generate_structured(role=role,system_prompt=prompt.system,content=f"{prompt.task}\nSTAGE: {name}\nVALIDATION ISSUE: {issue}\nUNTRUSTED BOUNDED CONTEXT:\n{payload}",output_schema=AlignmentStageExtraction,prompt_name=prompt.name,prompt_version=prompt.version);calls.append(response.metadata);self._validate_stage(response.value,paper,repository,paper_document,snapshot,static);return response.value,calls,repairs,None
            except Exception as exc:issue=str(exc)
        return None,calls,repairs,f"{name} retry exhaustion: {issue}"
    def _validate_stage(self,stage,paper,repository,paper_document,snapshot,static):
        backing={"paper_document":paper_document,"repository_snapshot":snapshot,"static_analysis":static}
        for group in (stage.experiment_alignments,stage.dataset_mappings,stage.model_mappings,stage.parameter_mappings,stage.ablation_mappings,stage.metric_mappings):
            for mapping in group:self.evidence.validate_mapping(mapping,paper,repository,**backing)
        for conflict in stage.conflicts:self.evidence.validate(tuple(x for candidate in conflict.candidates for x in candidate.evidence),paper,repository,**backing)
    def _review(self,catalog):
        prompt=self.prompts.get("catalog_review")
        try:
            response=self.router.for_role(LLMRole.FAST).generate_structured(role=LLMRole.FAST,system_prompt=prompt.system,content=f"{prompt.task}\nCATALOG SUMMARY:\n{catalog.model_dump_json(exclude={'evidence'})}",output_schema=AlignmentCatalogReview,prompt_name=prompt.name,prompt_version=prompt.version)
            if not response.value.valid or response.value.missing_components:
                missing=tuple(dict.fromkeys((*catalog.alignment_metadata.missing_components,*(response.value.missing_components or ("catalog_review_validation",)))));catalog=catalog.model_copy(update={"alignment_status":AlignmentAnalysisStatus.PARTIAL,"alignment_metadata":catalog.alignment_metadata.model_copy(update={"missing_components":missing,"warnings":tuple(dict.fromkeys((*catalog.alignment_metadata.warnings,*response.value.warnings)))})})
            return catalog,[response.metadata],None
        except Exception as exc:return catalog,[],f"catalog review unavailable: {exc}"
