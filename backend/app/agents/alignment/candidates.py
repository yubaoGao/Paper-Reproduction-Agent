"""Deterministic, bounded paper/repository candidate generation."""
from __future__ import annotations
import hashlib
from backend.app.domain import ExperimentType
from .normalization import name_strength,normalize_entity
from .schemas import AlignmentCandidate

def stable_id(prefix,*parts):return f"{prefix}:"+hashlib.sha256("|".join(str(x) for x in parts).encode()).hexdigest()[:16]
class AlignmentCandidateGenerator:
    def __init__(self,max_per_experiment=8):self.max_per_experiment=max_per_experiment
    def generate(self,paper,repository):
        output=[]
        for entity in paper.datasets:
            left=normalize_entity(entity.canonical_name,entity.aliases)
            for item in repository.datasets:
                score,signals=name_strength(left,normalize_entity(item.name))
                if score:output.append(self._candidate("dataset",entity.canonical_name,(item.component_id,),score,signals,entity.evidence,item.evidence))
        for entity in paper.model_variants:
            left=normalize_entity(entity.canonical_name,entity.aliases)
            for item in repository.models:
                score,signals=name_strength(left,normalize_entity(item.name))
                if score:output.append(self._candidate("model",entity.canonical_name,(item.component_id,),score,signals,entity.evidence,item.evidence))
        for experiment in paper.experiments:
            candidates=[]
            for implementation in repository.experiment_implementations:
                score=0.0;signals=[]
                value,found=name_strength(normalize_entity(experiment.name,(experiment.variant,) if experiment.variant else ()),normalize_entity(implementation.name));score+=value*.45;signals.extend(found)
                if experiment.dataset and set(implementation.dataset_ids)&{x.component_id for x in repository.datasets if name_strength(normalize_entity(experiment.dataset),normalize_entity(x.name))[0]>=.72}:score+=.25;signals.append("dataset_relation")
                if experiment.model and set(implementation.model_ids)&{x.component_id for x in repository.models if name_strength(normalize_entity(experiment.model),normalize_entity(x.name))[0]>=.72}:score+=.2;signals.append("model_relation")
                paper_params={normalize_entity(x.name).canonical_name for x in experiment.parameters};repo_params={normalize_entity(x).canonical_name for x in implementation.parameter_keys}
                if paper_params&repo_params:score+=.1;signals.append("parameter_overlap")
                if score>0:candidates.append(self._candidate("experiment",experiment.experiment_id,(implementation.implementation_id,),min(score,1),tuple(signals),experiment.evidence,implementation.evidence))
                relation_score=name_strength(normalize_entity(experiment.name),normalize_entity(implementation.name))[0]
                if relation_score>=.45:
                    if experiment.dataset:
                        entity=next((x for x in paper.datasets if normalize_entity(experiment.dataset).canonical_name in normalize_entity(x.canonical_name,x.aliases).keys),None)
                        for component in repository.datasets:
                            if entity and component.component_id in implementation.dataset_ids:output.append(self._candidate("dataset",entity.canonical_name,(component.component_id,),.6,("experiment_relation",),entity.evidence,component.evidence))
                    if experiment.model:
                        entity=next((x for x in paper.model_variants if normalize_entity(experiment.model).canonical_name in normalize_entity(x.canonical_name,x.aliases).keys),None)
                        for component in repository.models:
                            if entity and component.component_id in implementation.model_ids:output.append(self._candidate("model",entity.canonical_name,(component.component_id,),.6,("experiment_relation",),entity.evidence,component.evidence))
            output.extend(sorted(candidates,key=lambda x:(-x.score,x.candidate_id))[:self.max_per_experiment])
        for experiment in paper.experiments:
            if experiment.experiment_type is ExperimentType.ABLATION:
                for item in repository.ablation_mechanisms:
                    score,signals=name_strength(normalize_entity(experiment.variant or experiment.name),normalize_entity(item.name))
                    details=" ".join(str(x) for x in item.details.values()).casefold();paper_tokens=set(__import__("re").findall(r"[a-z0-9]+",(experiment.variant or experiment.name).casefold()));repo_tokens=set(__import__("re").findall(r"[a-z0-9]+",item.name.casefold().replace("_"," ")))
                    if score or paper_tokens&repo_tokens and any(x in details for x in ("false","0","disable")):output.append(self._candidate("ablation",experiment.experiment_id,(item.component_id,),max(score,.55),(*signals,"repository_ablation_mechanism"),experiment.evidence,item.evidence))
        paper_parameters=[]
        for experiment in paper.experiments:paper_parameters.extend((experiment.experiment_id,x) for x in experiment.parameters)
        paper_parameters.extend(("global",x) for x in (*paper.training_parameters,*paper.evaluation_parameters))
        for owner,param in paper_parameters:
            for config in repository.configurations:
                score,signals=name_strength(normalize_entity(param.name),normalize_entity(config.key_path.rsplit(".",1)[-1]))
                if score:output.append(self._candidate("parameter",f"{owner}:{param.name}",(config.config_id,),score,signals,param.evidence,config.evidence))
        claims_by_id={x.id:x for x in (*paper.paper_claims,*(claim for experiment in paper.experiments for claim in experiment.claims))};claims=list(claims_by_id.values())
        for claim in claims:
            for metric in repository.metrics:
                score,signals=name_strength(normalize_entity(claim.metric_name),normalize_entity(metric.name))
                if score:output.append(self._candidate("metric",claim.id,(metric.component_id,),score,signals,claim.evidence,metric.evidence))
        deduplicated={}
        for item in output:
            old=deduplicated.get(item.candidate_id)
            if old is None or item.score>old.score:deduplicated[item.candidate_id]=item
            elif item.score==old.score:deduplicated[item.candidate_id]=old.model_copy(update={"signals":tuple(dict.fromkeys((*old.signals,*item.signals))),"paper_evidence":tuple(dict.fromkeys((*old.paper_evidence,*item.paper_evidence))),"repository_evidence":tuple(dict.fromkeys((*old.repository_evidence,*item.repository_evidence)))})
        grouped={}
        for item in deduplicated.values():grouped.setdefault((item.category,item.paper_item_id),[]).append(item)
        bounded=[]
        for values in grouped.values():bounded.extend(sorted(values,key=lambda x:(-x.score,x.candidate_id))[:self.max_per_experiment])
        return tuple(sorted(bounded,key=lambda x:(x.category,x.paper_item_id,-x.score,x.candidate_id)))
    @staticmethod
    def _candidate(category,paper_id,repo_ids,score,signals,paper_ev,repo_ev):return AlignmentCandidate(candidate_id=stable_id("candidate",category,paper_id,*repo_ids),category=category,paper_item_id=paper_id,repository_item_ids=repo_ids,score=round(score,4),signals=tuple(dict.fromkeys(signals)),paper_evidence=paper_ev,repository_evidence=repo_ev)
