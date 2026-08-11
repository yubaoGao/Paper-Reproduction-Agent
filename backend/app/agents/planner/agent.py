"""Production-oriented planning orchestrator; it never executes experiments."""
from __future__ import annotations
import json
from datetime import datetime,timezone
from backend.app.domain import PlanningMetadata,PlanningOverrides,PlanningTrace,ReproductionPolicy
from backend.app.llm import LLMRole
from backend.app.services import PlanningSettings,ReproductionPlanningError
from .catalog import ReproductionPlanValidator
from .deterministic import DeterministicPlanBuilder
from .prompt_registry import PlannerPromptRegistry
from .schemas import PlanReview,PlanningResult,SemanticSelectionSet

class ReproductionPlannerAgent:
    def __init__(self,router=None,*,settings=None,prompts=None,validator=None):
        self.router=router; self.settings=settings or PlanningSettings(); self.prompts=prompts or PlannerPromptRegistry(); self.validator=validator or ReproductionPlanValidator(); self.builder=DeterministicPlanBuilder()

    def plan(self,specification,paper_catalog,repository_catalog,alignment_catalog,*,policy=ReproductionPolicy.STRICT,overrides=None):
        started=datetime.now(timezone.utc); overrides=overrides or PlanningOverrides(); calls=[]; repairs=0; warnings=[]
        try:
            self.validator.validate_inputs(specification,paper_catalog,repository_catalog,alignment_catalog)
            initial=self.builder.build(specification,paper_catalog,repository_catalog,alignment_catalog,policy,overrides)
            semantic={}
            ambiguous=[x for x in initial.blockers if x.code in {"ambiguous_entrypoint","ambiguous_config"}]
            if ambiguous and self.router is not None:
                selection,metadata,repairs=self._resolve(ambiguous,repository_catalog,alignment_catalog); calls.extend(metadata)
                semantic={x.paper_experiment_id:{"entrypoint_id":x.entrypoint_id,"config_ids":x.config_ids} for x in selection.selections}
            plan=self.builder.build(specification,paper_catalog,repository_catalog,alignment_catalog,policy,overrides,semantic)
            self.validator.validate(plan,paper_catalog,repository_catalog,alignment_catalog)
            if self.router is not None:
                try:
                    prompt=self.prompts.get("plan_review")
                    response=self.router.for_role(LLMRole.FAST).generate_structured(role=LLMRole.FAST,system_prompt=prompt.system,content=f"{prompt.task}\nPLAN SUMMARY:\n{plan.model_dump_json(exclude={'decisions'})}",output_schema=PlanReview,prompt_name=prompt.name,prompt_version=prompt.version)
                    calls.append(response.metadata); warnings.extend(response.value.warnings)
                except Exception as exc: warnings.append(f"plan review unavailable: {exc}")
            if warnings: plan=plan.model_copy(update={"warnings":tuple(dict.fromkeys((*plan.warnings,*warnings))),"metadata":plan.metadata.model_copy(update={"warnings":tuple(dict.fromkeys((*plan.metadata.warnings,*warnings)))})})
            finished=datetime.now(timezone.utc)
            versions={"semantic_selection":"v1","repair":"v1","plan_review":"v1"}
            plan=plan.model_copy(update={"metadata":plan.metadata.model_copy(update={"prompt_versions":versions})})
            trace=PlanningTrace(plan_id=plan.plan_id,reproduction_specification_id=specification.id,paper_catalog_id=paper_catalog.catalog_id,repository_catalog_id=repository_catalog.catalog_id,alignment_catalog_id=alignment_catalog.catalog_id,repository_snapshot_id=repository_catalog.snapshot_id,resolved_commit_sha=repository_catalog.resolved_commit_sha,policy=policy,started_at=started,finished_at=finished,deterministic_decision_count=len(plan.decisions),primary_calls=sum(x.role is LLMRole.PRIMARY for x in calls),fast_calls=sum(x.role is LLMRole.FAST for x in calls),repair_attempts=repairs,prompt_versions=versions,warnings=plan.warnings,blocker_codes=tuple(x.code for x in plan.blockers),usage=tuple(x.model_dump(mode="json") for x in calls),status=plan.status)
            return PlanningResult(plan=plan,trace=trace)
        except ReproductionPlanningError: raise
        except Exception as exc: raise ReproductionPlanningError(f"reproduction planning failed: {exc}") from exc

    def _resolve(self,ambiguous,repo,alignment):
        ids={x.entrypoint_id for x in repo.entrypoints}; config_ids={x.config_id for x in repo.configurations}; targets={x.paper_experiment_id for x in ambiguous}; records={x.paper_experiment_id:x for x in alignment.experiment_alignments}
        context=[{"paper_experiment_id":x,"entrypoint_ids":records[x].entrypoint_ids,"config_ids":records[x].config_ids} for x in targets]
        calls=[]; issue=""
        for attempt in range(self.settings.max_repair_attempts+1):
            role=LLMRole.PRIMARY if attempt==0 or attempt>1 else LLMRole.FAST; name="semantic_selection" if attempt==0 else "repair"; prompt=self.prompts.get(name)
            try:
                response=self.router.for_role(role).generate_structured(role=role,system_prompt=prompt.system,content=f"{prompt.task}\nVALIDATION ISSUE: {issue}\nBOUNDED CANDIDATES:\n{json.dumps(context)}",output_schema=SemanticSelectionSet,prompt_name=prompt.name,prompt_version=prompt.version); calls.append(response.metadata)
                selected=response.value
                if {x.paper_experiment_id for x in selected.selections}!=targets: raise ValueError("selection must cover every ambiguous experiment")
                for item in selected.selections:
                    record=records[item.paper_experiment_id]
                    if item.entrypoint_id not in ids or item.entrypoint_id not in record.entrypoint_ids: raise ValueError("selection contains an invalid entrypoint")
                    if not set(item.config_ids)<=config_ids or not set(item.config_ids)<=set(record.config_ids): raise ValueError("selection contains an invalid config")
                return selected,calls,attempt
            except Exception as exc: issue=str(exc)
        return SemanticSelectionSet(),calls,self.settings.max_repair_attempts
