"""Catalog-bounded deterministic and semantic reproduction goal resolution."""
from __future__ import annotations
import json,re
from backend.app.domain import (
    AblationDefinition,ExperimentType,GoalResolutionResult,GoalResolutionStatus,
    PaperExperimentCatalog,ReproductionSpecification,ReproductionTarget,
    TargetType,UserReproductionGoal,
)
from backend.app.llm import LLMRole,LLMRouter
from .prompt_registry import PromptRegistry
from .schemas import GoalSemanticSelection

class GoalResolutionError(RuntimeError): pass

class ReproductionGoalResolver:
    def __init__(self,router:LLMRouter|None=None,prompts:PromptRegistry|None=None): self.router=router; self.prompts=prompts or PromptRegistry()
    def resolve(self,catalog:PaperExperimentCatalog,goal:UserReproductionGoal)->GoalResolutionResult:
        text=goal.text.casefold(); records=list(catalog.experiments); selected=records[:]
        recognized=False
        all_ablation=any(x in text for x in ("all ablation","全部 ablation","所有消融","全部消融"))
        all_main=any(x in text for x in ("all main","全部主要实验","所有主实验","论文全部主要实验"))
        if all_ablation: recognized=True; selected=[x for x in records if x.experiment_type is ExperimentType.ABLATION]
        elif all_main: recognized=True; selected=[x for x in records if x.experiment_type is ExperimentType.MAIN]
        else:
            table_match=re.search(r"table\s*([\w.-]+)",text,re.I)
            if table_match: recognized=True; selected=[x for x in selected if table_match.group(1) in x.source_tables]
            dataset_matches=[]
            for entity in catalog.datasets:
                if any(name.casefold() in text for name in (entity.canonical_name,*entity.aliases)): dataset_matches.append(entity)
            if dataset_matches:
                recognized=True
                names={name.casefold() for entity in dataset_matches for name in (entity.canonical_name,*entity.aliases)}
                selected=[x for x in selected if x.dataset and x.dataset.casefold() in names]
            named=[x for x in selected if any(value and value.casefold() in text for value in (x.name,x.variant,x.model))]
            if named: recognized=True; selected=named
            if "full model" in text or "完整模型" in text:
                recognized=True
                selected=[x for x in selected if x.experiment_type is ExperimentType.MAIN or (x.variant and x.variant.casefold()=="full model")]
            if dataset_matches and len(selected)>1:
                mains=[x for x in selected if x.experiment_type is ExperimentType.MAIN]
                if mains: selected=mains
        metric_names=tuple(dict.fromkeys(claim.metric_name for claim in catalog.paper_claims if claim.metric_name.casefold() in text))
        recognized=recognized or bool(metric_names)
        generic=text.strip() in {"复现实验","reproduce experiments","reproduce the experiments","复现论文"}
        if generic and len(records)>1: return self._ambiguous(records,"目标没有限定实验范围")
        if (not selected or not recognized) and self.router:
            semantic=self._semantic(catalog,goal)
            if semantic.ambiguous: return GoalResolutionResult(status=GoalResolutionStatus.AMBIGUOUS,candidate_experiment_ids=semantic.experiment_ids,reason=semantic.reason or "语义目标存在歧义",clarification_questions=semantic.clarification_questions)
            known={x.experiment_id for x in records}
            if not set(semantic.experiment_ids).issubset(known): raise GoalResolutionError("semantic resolver returned unknown experiment ids")
            known_metrics={claim.metric_name for claim in catalog.paper_claims}
            if not set(semantic.metric_names).issubset(known_metrics): raise GoalResolutionError("semantic resolver returned unknown metric names")
            selected=[x for x in records if x.experiment_id in semantic.experiment_ids]; metric_names=semantic.metric_names
        if not selected or not recognized: return GoalResolutionResult(status=GoalResolutionStatus.NOT_FOUND,reason="Catalog 中没有匹配的实验")
        is_plural=all_ablation or all_main or any(x in text for x in ("以及"," and ","全部","所有","multiple"))
        if len(selected)>1 and not is_plural and not metric_names: return self._ambiguous(selected,"多个 Catalog 实验同样匹配该目标")
        return GoalResolutionResult(status=GoalResolutionStatus.RESOLVED,specification=self._specification(catalog,goal,selected,metric_names))
    def _semantic(self,catalog,goal):
        prompt=self.prompts.get("goal_resolution")
        summary=[{"id":x.experiment_id,"name":x.name,"type":x.experiment_type.value,"dataset":x.dataset,"model":x.model,"variant":x.variant,"metrics":[c.metric_name for c in x.claims]} for x in catalog.experiments]
        return self.router.for_role(LLMRole.PRIMARY).generate_structured(role=LLMRole.PRIMARY,system_prompt=prompt.system,content=f"{prompt.task}\nUSER GOAL: {goal.text}\nCATALOG: {json.dumps(summary,ensure_ascii=False)}",output_schema=GoalSemanticSelection,prompt_name=prompt.name,prompt_version=prompt.version).value
    @staticmethod
    def _ambiguous(records,reason):
        return GoalResolutionResult(status=GoalResolutionStatus.AMBIGUOUS,candidate_experiment_ids=tuple(x.experiment_id for x in records),reason=reason,clarification_questions=("请指定数据集、表格、实验类型或模型变体。",))
    @staticmethod
    def _specification(catalog,goal,records,metrics):
        target_type={ExperimentType.MAIN:TargetType.MAIN_EXPERIMENT,ExperimentType.ABLATION:TargetType.ABLATION,ExperimentType.BASELINE:TargetType.BASELINE}
        targets=tuple(ReproductionTarget(id=x.experiment_id,target_type=target_type.get(x.experiment_type,TargetType.CUSTOM),section=x.source_sections[0] if x.source_sections else None,table=f"Table {x.source_tables[0]}" if x.source_tables else None,figure=f"Figure {x.source_figures[0]}" if x.source_figures else None,experiment_name=x.name,dataset=x.dataset,model=x.model,variant=x.variant,description=f"Catalog experiment {x.experiment_id}") for x in records)
        ids={x.experiment_id for x in records}
        claims=tuple(x for x in catalog.paper_claims if (x.target_id in ids or x.target_id is None) and (not metrics or x.metric_name in metrics))
        claim_ids={x.id for x in claims}
        ablations=tuple(AblationDefinition(id=f"ablation:{x.experiment_id}",name=x.variant or x.name,modified_components={"variant":x.variant or x.name},expected_claims=tuple(c.id for c in claims if c.target_id==x.experiment_id),target_dataset=x.dataset,description=f"Catalog ablation {x.experiment_id}") for x in records if x.experiment_type is ExperimentType.ABLATION)
        parameters=[]
        for record in records:
            parameters.extend(record.parameters)
        parameters.extend(catalog.training_parameters); parameters.extend(catalog.evaluation_parameters)
        unique={x.name.casefold():x for x in parameters}
        return ReproductionSpecification(id=f"repro:{goal.goal_id}",paper=catalog.paper,user_goal=goal.text,targets=targets,claims=claims,ablations=ablations,parameters=tuple(unique.values()))
