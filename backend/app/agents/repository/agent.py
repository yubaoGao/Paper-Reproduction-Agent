"""Production repository-intelligence workflow; static analysis only."""
from __future__ import annotations
import json
from datetime import datetime,timezone
from backend.app.domain import EvidenceReference,RepositoryAnalysisStatus,RepositoryAnalysisTrace
from backend.app.infrastructure.repository import DefaultRepositorySnapshotBuilder,GitRepositoryResolver,RepositoryStaticAnalyzer
from backend.app.llm import ANALYSIS_CONTROL_FLOW_ERRORS,LLMProviderError,LLMRole,LLMRouter,StructuredOutputError
from backend.app.services import RepositoryAnalysisError,RepositoryAnalysisSettings
from .catalog import RepositoryCatalogMerger,RepositoryCatalogValidator,RepositoryCatalogValidationError
from .context import RepositoryContextBuilder
from .evidence import RepositoryEvidenceValidator
from .prompt_registry import RepositoryPromptRegistry
from .schemas import RepositoryAnalysisResult,RepositoryCatalogReview,RepositoryStageExtraction

class RepositoryAnalyzerAgent:
    """Coordinates safe resolution, parsing and bounded semantic interpretation."""
    STAGES=("project_overview","environment_dependencies","entrypoints_and_cli","datasets","models_losses_variants","experiments_ablations","metrics_checkpoints_artifacts")
    def __init__(self,router:LLMRouter,*,settings=None,resolver=None,snapshot_builder=None,static_analyzer=None,prompts=None):
        self.router=router;self.settings=settings or RepositoryAnalysisSettings();self.prompts=prompts or RepositoryPromptRegistry();self.resolver=resolver or GitRepositoryResolver(self.settings);self.snapshot_builder=snapshot_builder or DefaultRepositorySnapshotBuilder(self.settings);self.static=static_analyzer or RepositoryStaticAnalyzer();self.evidence=RepositoryEvidenceValidator();self.validator=RepositoryCatalogValidator(self.evidence);self.merger=RepositoryCatalogMerger()
        self.context=RepositoryContextBuilder(router,self.prompts,max_files=self.settings.max_context_files,max_chars_per_file=self.settings.max_context_chars_per_file)
    def analyze(self,reference,*,paper_catalog=None,reproduction_specification=None):
        started=datetime.now(timezone.utc);calls=[];repairs=0;warnings=[];missing=[];stages=[]
        try:
            resolved=self.resolver.resolve(reference);snapshot=self.snapshot_builder.build(resolved)
            metric_names=self._metric_targets(paper_catalog,reproduction_specification)
            static=self.static.analyze(snapshot,metric_names=metric_names)
            if static.warnings:missing.append("static_analysis")
            context=self.context.build(snapshot,static,paper_catalog,reproduction_specification);calls.extend(context.llm_metadata)
            payload=json.dumps({"snapshot_id":snapshot.snapshot_id,"files":[x.model_dump() for x in context.items],"entrypoints":[x.model_dump(mode="json") for x in static.entrypoints],"configs":[x.model_dump(mode="json") for x in static.configurations]},ensure_ascii=False)
            for name in self.STAGES:
                value,stage_calls,count,error=self._stage(name,payload,snapshot,static);calls.extend(stage_calls);repairs+=count
                if value is None:missing.append(name);warnings.append(error or f"{name} failed")
                else:stages.append((name,value))
            catalog=self.merger.merge(snapshot,static,tuple(stages),tuple(missing),tuple(warnings));self.validator.validate(catalog,snapshot,static)
            catalog,catalog_calls,review_warning=self._review(catalog);calls.extend(catalog_calls)
            if review_warning:
                missing=tuple(dict.fromkeys((*catalog.analysis_metadata.missing_components,"catalog_review")))
                catalog=catalog.model_copy(update={"analysis_status":RepositoryAnalysisStatus.PARTIAL,"analysis_metadata":catalog.analysis_metadata.model_copy(update={"missing_components":missing,"warnings":tuple(dict.fromkeys((*catalog.analysis_metadata.warnings,review_warning)))})})
            finished=datetime.now(timezone.utc)
            trace=RepositoryAnalysisTrace(analysis_id=f"repository-analysis:{snapshot.snapshot_id}",repository_id=reference.repository_id,commit_sha=snapshot.resolved_commit_sha,started_at=started,finished_at=finished,selected_files=context.selected_files,selected_symbols=context.selected_symbols,primary_calls=sum(x.role is LLMRole.PRIMARY for x in calls),fast_calls=sum(x.role is LLMRole.FAST for x in calls),repair_count=repairs,prompt_versions={name:self.prompts.get(name).version for name in ("context_classification","stage_analysis","repair","catalog_review")},usage=tuple(x.model_dump(mode="json") for x in calls),warnings=catalog.analysis_metadata.warnings,status=catalog.analysis_status)
            return RepositoryAnalysisResult(catalog=catalog,trace=trace,snapshot=snapshot)
        except ANALYSIS_CONTROL_FLOW_ERRORS:
            raise
        except RepositoryAnalysisError:raise
        except Exception as exc:raise RepositoryAnalysisError(f"repository analysis failed: {exc}") from exc
    @staticmethod
    def _metric_targets(paper_catalog,reproduction_specification):
        selected=set(getattr(reproduction_specification,"selected_experiment_ids",()) or ())
        selected.update(target.paper_experiment_id for target in getattr(reproduction_specification,"targets",()) if target.paper_experiment_id)
        claims=[]
        if paper_catalog is not None:
            claims.extend(claim for claim in paper_catalog.paper_claims if not selected or claim.target_id is None or claim.target_id in selected)
            claims.extend(claim for experiment in paper_catalog.experiments if not selected or experiment.experiment_id in selected for claim in experiment.claims)
        elif reproduction_specification is not None:
            claims.extend(reproduction_specification.claims)
        return tuple(dict.fromkeys(claim.metric_name for claim in claims))
    def _stage(self,name,payload,snapshot,static):
        calls=[];repairs=0;issue=""
        prompt=self.prompts.get("stage_analysis")
        try:
            response=self.router.for_role(LLMRole.PRIMARY).generate_structured(role=LLMRole.PRIMARY,system_prompt=prompt.system,content=f"{prompt.task}\nSTAGE: {name}\nUNTRUSTED REPOSITORY CONTEXT:\n{payload}",output_schema=RepositoryStageExtraction,prompt_name=prompt.name,prompt_version=prompt.version);calls.append(response.metadata);self._validate_stage(response.value,snapshot,static);return response.value,calls,repairs,None
        except ANALYSIS_CONTROL_FLOW_ERRORS:
            raise
        except (LLMProviderError, StructuredOutputError) as exc:return None,calls,repairs,str(exc)
        except Exception as exc:issue=str(exc)
        for attempt in range(self.settings.max_repair_attempts):
            repairs+=1;role=LLMRole.FAST if attempt==0 else LLMRole.PRIMARY;prompt=self.prompts.get("repair")
            try:
                response=self.router.for_role(role).generate_structured(role=role,system_prompt=prompt.system,content=f"{prompt.task}\nSTAGE: {name}\nVALIDATION ISSUE: {issue}\nUNTRUSTED REPOSITORY CONTEXT:\n{payload}",output_schema=RepositoryStageExtraction,prompt_name=prompt.name,prompt_version=prompt.version);calls.append(response.metadata);self._validate_stage(response.value,snapshot,static);return response.value,calls,repairs,None
            except ANALYSIS_CONTROL_FLOW_ERRORS:
                raise
            except (LLMProviderError, StructuredOutputError) as exc:return None,calls,repairs,str(exc)
            except Exception as exc:issue=str(exc)
        return None,calls,repairs,f"{name} retry exhaustion: {issue}"
    def _validate_stage(self,stage,snapshot,static):
        evidence=list(stage.evidence)
        for item in (*stage.components,*stage.implementations,*stage.evaluation_policies):evidence.extend(item.evidence)
        for item in stage.evaluation_policies:
            for value in item.policy.evidence:
                try:evidence.append(value if isinstance(value,EvidenceReference) else EvidenceReference.model_validate(value))
                except Exception as exc:raise ValueError(f"invalid evaluation policy evidence: {item.policy_id}") from exc
        for conflict in stage.conflicts:
            for candidate in conflict.candidates:evidence.extend(candidate.evidence)
        for fact in stage.facts:evidence.extend(fact.evidence)
        self.evidence.validate_all(evidence,snapshot,static)
    def _review(self,catalog):
        prompt=self.prompts.get("catalog_review")
        try:
            response=self.router.for_role(LLMRole.FAST).generate_structured(role=LLMRole.FAST,system_prompt=prompt.system,content=f"{prompt.task}\nCATALOG:\n{catalog.model_dump_json(exclude={'evidence','code_index'})}",output_schema=RepositoryCatalogReview,prompt_name=prompt.name,prompt_version=prompt.version)
            if not response.value.valid or response.value.missing_components:
                missing=tuple(dict.fromkeys((*catalog.analysis_metadata.missing_components,*response.value.missing_components)))
                catalog=catalog.model_copy(update={"analysis_status":RepositoryAnalysisStatus.PARTIAL,"analysis_metadata":catalog.analysis_metadata.model_copy(update={"missing_components":missing,"warnings":tuple(dict.fromkeys((*catalog.analysis_metadata.warnings,*response.value.warnings)))})})
            return catalog,[response.metadata],None
        except ANALYSIS_CONTROL_FLOW_ERRORS:
            raise
        except Exception as exc:return catalog,[],f"catalog review unavailable: {exc}"
