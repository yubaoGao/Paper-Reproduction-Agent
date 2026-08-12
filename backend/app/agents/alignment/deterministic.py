"""Deterministic mappings and transparent heuristic confidence scoring."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal,InvalidOperation
from backend.app.domain import *
from .candidates import stable_id
from .normalization import name_strength,normalize_entity

def _equal(left,right):
    if left==right:return True
    try:return Decimal(str(left))==Decimal(str(right))
    except (InvalidOperation,ValueError):return str(left).strip().casefold()==str(right).strip().casefold()
def _status(score,count):
    if count==0:return AlignmentStatus.NOT_FOUND
    if count>1:return AlignmentStatus.AMBIGUOUS
    return AlignmentStatus.ALIGNED if score>=.72 else AlignmentStatus.PARTIALLY_ALIGNED
def _evidence(records):return tuple(dict.fromkeys(x for record in records for x in record.evidence))

@dataclass(frozen=True)
class DeterministicAlignment:
    experiments:tuple[ExperimentAlignmentRecord,...];datasets:tuple[DatasetAlignment,...];models:tuple[ModelAlignment,...];parameters:tuple[ParameterAlignment,...];ablations:tuple[AblationAlignment,...];metrics:tuple[MetricAlignment,...];evaluation_policies:tuple[EvaluationPolicyAlignment,...];conflicts:tuple[AlignmentConflict,...];unmatched_paper:tuple[UnmatchedAlignmentItem,...];unmatched_repository:tuple[UnmatchedAlignmentItem,...]

class AlignmentConfidenceScorer:
    """Scores evidence signals, not statistical probabilities."""
    weights={"canonical_or_alias_exact":.7,"strong_token_overlap":.45,"partial_token_overlap":.25,"experiment_relation":.4,"dataset_relation":.18,"model_relation":.18,"parameter_overlap":.09,"paired_evidence":.15,"value_match":.2}
    def score(self,signals,paper_evidence=(),repository_evidence=()):
        value=sum(self.weights.get(x,0) for x in set(signals))
        if paper_evidence and repository_evidence:value+=self.weights["paired_evidence"]
        return round(min(1,max(0,value)),4)

class DeterministicAlignmentBuilder:
    def __init__(self,scorer=None):self.scorer=scorer or AlignmentConfidenceScorer()
    def build(self,paper,repository,candidates):
        datasets=self._entities("dataset",paper.datasets,repository.datasets,candidates)
        models=self._entities("model",paper.model_variants,repository.models,candidates)
        parameters,parameter_conflicts=self._parameters(paper,repository)
        ablations=self._ablations(paper,repository,candidates)
        metrics,metric_conflicts=self._metrics(paper,repository)
        experiments,experiment_conflicts=self._experiments(paper,repository,candidates,datasets,models,parameters,ablations,metrics)
        evaluation_policies,evaluation_conflicts=self._evaluation_policies(paper,repository,experiments)
        evaluation_by_experiment={x.paper_experiment_id:x for x in evaluation_policies}
        updated_experiments=[]
        for item in experiments:
            evaluation=evaluation_by_experiment[item.paper_experiment_id]
            conflict_ids=item.conflict_ids
            if evaluation.conflict_id is not None:
                conflict_ids=tuple(dict.fromkeys((*conflict_ids,evaluation.conflict_id)))
            updated_experiments.append(item.model_copy(update={
                "evaluation_policy_alignment_id":evaluation.alignment_id,
                "conflict_ids":conflict_ids,
                "status":AlignmentStatus.CONFLICTED if evaluation.conflict_id and item.status is not AlignmentStatus.AMBIGUOUS else item.status,
            }))
        experiments=tuple(updated_experiments)
        used={x for record in experiments for x in record.repository_implementation_ids}
        unmatched_paper=[]
        for record in experiments:
            if record.status is AlignmentStatus.NOT_FOUND:
                experiment=next(x for x in paper.experiments if x.experiment_id==record.paper_experiment_id);unmatched_paper.append(UnmatchedAlignmentItem(source=AlignmentItemSource.PAPER,category="experiment",item_id=experiment.experiment_id,name=experiment.name,reason="no trustworthy repository implementation candidate",evidence=experiment.evidence))
        for group,category,name_field in ((datasets,"dataset","paper_dataset"),(models,"model","paper_model"),(ablations,"ablation","paper_ablation"),(metrics,"metric","paper_metric")):
            for item in group:
                if item.status is AlignmentStatus.NOT_FOUND:unmatched_paper.append(UnmatchedAlignmentItem(source=AlignmentItemSource.PAPER,category=category,item_id=item.alignment_id,name=getattr(item,name_field),reason=f"no repository {category} mapping",evidence=item.paper_evidence))
        for item in parameters:
            if item.mapping_status in {ParameterMappingStatus.PAPER_ONLY,ParameterMappingStatus.NOT_FOUND}:unmatched_paper.append(UnmatchedAlignmentItem(source=AlignmentItemSource.PAPER,category="parameter",item_id=item.alignment_id,name=item.paper_parameter_name or item.semantic_name,reason="no repository parameter mapping",evidence=item.paper_evidence))
        unmatched_repository=[UnmatchedAlignmentItem(source=AlignmentItemSource.REPOSITORY,category="experiment_implementation",item_id=x.implementation_id,name=x.name,reason="not selected by any paper experiment alignment",evidence=x.evidence) for x in repository.experiment_implementations if x.implementation_id not in used]
        used_components={x for item in (*datasets,*models,*ablations,*metrics) for x in (*getattr(item,"repository_dataset_ids",()),*getattr(item,"repository_model_ids",()),*getattr(item,"repository_ablation_ids",()),*getattr(item,"repository_metric_ids",()))}
        for group,category in ((repository.datasets,"dataset"),(repository.models,"model"),(repository.ablation_mechanisms,"ablation"),(repository.metrics,"metric")):
            unmatched_repository.extend(UnmatchedAlignmentItem(source=AlignmentItemSource.REPOSITORY,category=category,item_id=x.component_id,name=x.name,reason=f"not mapped to a paper {category}",evidence=x.evidence) for x in group if x.component_id not in used_components)
        for item in parameters:
            if item.mapping_status is ParameterMappingStatus.REPOSITORY_ONLY and item.paper_parameter_name is None:unmatched_repository.append(UnmatchedAlignmentItem(source=AlignmentItemSource.REPOSITORY,category="parameter",item_id=item.alignment_id,name=item.semantic_name,reason="repository parameter is not described by the paper",evidence=item.repository_evidence))
        source_conflicts=[]
        for item in repository.conflicts:
            source_conflicts.append(AlignmentConflict(conflict_id=stable_id("conflict","repository",item.conflict_id),semantic_key=f"repository:{item.semantic_key}",conflict_type=AlignmentConflictType.DOCUMENTATION_CODE_CONFLICT if item.conflict_type is RepositoryConflictType.DOCUMENTATION_CODE else AlignmentConflictType.OTHER,candidates=tuple(AlignmentConflictCandidate(source=AlignmentItemSource.REPOSITORY,value=x.value,evidence=x.evidence) for x in item.candidates),status=AlignmentConflictStatus.RESOLVED if item.status is RepositoryConflictStatus.RESOLVED else AlignmentConflictStatus.UNRESOLVED,resolution=item.resolution,reasoning=item.reasoning or "preserved repository-source conflict"))
        for item in paper.conflicts:
            source_conflicts.append(AlignmentConflict(conflict_id=stable_id("conflict","paper",item.conflict_id),semantic_key=f"paper:{item.semantic_key}",conflict_type=AlignmentConflictType.OTHER,candidates=tuple(AlignmentConflictCandidate(source=AlignmentItemSource.PAPER,value=x.value,evidence=x.evidence) for x in item.candidates),status=AlignmentConflictStatus.RESOLVED if item.status.value=="resolved" else AlignmentConflictStatus.UNRESOLVED,resolution=item.resolution,reasoning=item.reasoning or "preserved paper-source conflict"))
        return DeterministicAlignment(experiments,datasets,models,parameters,ablations,metrics,evaluation_policies,(*parameter_conflicts,*metric_conflicts,*evaluation_conflicts,*experiment_conflicts,*source_conflicts),tuple(unmatched_paper),tuple(unmatched_repository))
    def _entities(self,category,paper_entities,repo_entities,candidates):
        output=[]
        for entity in paper_entities:
            found=[x for x in candidates if x.category==category and x.paper_item_id==entity.canonical_name]
            top=max((x.score for x in found),default=0);selected=[x for x in found if x.score==top and top>=.45];ids=tuple(x.repository_item_ids[0] for x in selected);status=_status(top,len(ids));repo=[x for x in repo_entities if x.component_id in ids];signals=tuple(y for x in selected for y in x.signals)
            cls=DatasetAlignment if category=="dataset" else ModelAlignment;field="paper_dataset" if category=="dataset" else "paper_model";id_field="repository_dataset_ids" if category=="dataset" else "repository_model_ids"
            output.append(cls(**{"alignment_id":stable_id(category,entity.canonical_name),field:entity.canonical_name,id_field:ids,"status":status,"confidence":self.scorer.score(signals,entity.evidence,_evidence(repo)),"reasoning":"deterministic alias/name signals: "+(", ".join(signals) if signals else "none"),"paper_evidence":entity.evidence,"repository_evidence":_evidence(repo)}))
        return tuple(output)
    def _parameters(self,paper,repository):
        paper_items=[]
        for experiment in paper.experiments:paper_items.extend((experiment.experiment_id,x) for x in experiment.parameters)
        paper_items.extend((None,x) for x in (*paper.training_parameters,*paper.evaluation_parameters))
        configs=repository.configurations;used=set();output=[];conflicts=[]
        for experiment_id,param in paper_items:
            key=normalize_entity(param.name).canonical_name;matches=[x for x in configs if normalize_entity(x.key_path.rsplit(".",1)[-1]).canonical_name==key]
            cli=[]
            for entry in repository.entrypoints:
                for argument in entry.arguments:
                    if normalize_entity(argument.name.lstrip("-")).canonical_name==key:cli.append((argument.default,f"CLI:{entry.entrypoint_id}:{argument.source}",entry.evidence))
            used.update(x.config_id for x in matches);values={repr(x.value) for x in matches}|{repr(x[0]) for x in cli}
            repo_evidence=tuple(dict.fromkeys((*_evidence(matches),*(e for _,_,evidence in cli for e in evidence))));source_count=len(matches)+len(cli)
            if not source_count:status=ParameterMappingStatus.NOT_FOUND if param.status is InformationStatus.UNKNOWN else ParameterMappingStatus.PAPER_ONLY;confidence=.7 if param.evidence else .3;repo_value=None
            elif len(values)>1:status=ParameterMappingStatus.AMBIGUOUS;confidence=.45;repo_value=None
            else:
                repo_value=matches[0].value if matches else cli[0][0]
                if param.status is InformationStatus.UNKNOWN:status=ParameterMappingStatus.REPOSITORY_ONLY;confidence=.75
                elif repo_value is None:status=ParameterMappingStatus.SEMANTIC_MATCH_VALUE_UNKNOWN;confidence=.65
                elif _equal(param.value,repo_value):status=ParameterMappingStatus.MATCHED;confidence=self.scorer.score(("canonical_or_alias_exact","value_match"),param.evidence,repo_evidence)
                else:
                    status=ParameterMappingStatus.VALUE_CONFLICT;confidence=.95;conflict_id=stable_id("conflict","parameter",experiment_id or "global",key)
                    conflicts.append(AlignmentConflict(conflict_id=conflict_id,semantic_key=f"parameter:{experiment_id or 'global'}:{key}",conflict_type=AlignmentConflictType.PARAMETER_VALUE_MISMATCH,candidates=(AlignmentConflictCandidate(source=AlignmentItemSource.PAPER,value=param.value,evidence=param.evidence),AlignmentConflictCandidate(source=AlignmentItemSource.REPOSITORY,value=repo_value,evidence=repo_evidence)),reasoning="paper and repository provide different explicit values"))
            conflict=stable_id("conflict","parameter",experiment_id or "global",key) if status is ParameterMappingStatus.VALUE_CONFLICT else None
            repository_source=matches[0].source if source_count==1 and matches else cli[0][1] if source_count==1 else None
            output.append(ParameterAlignment(alignment_id=stable_id("parameter",experiment_id or "global",key),paper_experiment_id=experiment_id,semantic_name=key,paper_parameter_name=param.name,paper_value=param.value,paper_status=param.status,repository_config_ids=tuple(x.config_id for x in matches),repository_value=repo_value,repository_source=repository_source,mapping_status=status,confidence=confidence,paper_evidence=param.evidence,repository_evidence=repo_evidence,conflict_id=conflict))
        for config in configs:
            if config.config_id not in used:output.append(ParameterAlignment(alignment_id=stable_id("parameter","repository",config.config_id),semantic_name=normalize_entity(config.key_path.rsplit(".",1)[-1]).canonical_name,repository_config_ids=(config.config_id,),repository_value=config.value,repository_source=config.source,mapping_status=ParameterMappingStatus.REPOSITORY_ONLY,confidence=.8,repository_evidence=config.evidence))
        return tuple(output),tuple(conflicts)
    def _ablations(self,paper,repository,candidates):
        output=[]
        for experiment in paper.experiments:
            if experiment.experiment_type is not ExperimentType.ABLATION:continue
            found=[x for x in candidates if x.category=="ablation" and x.paper_item_id==experiment.experiment_id];top=max((x.score for x in found),default=0);selected=[x for x in found if x.score==top and top>=.35];ids=tuple(x.repository_item_ids[0] for x in selected);records=[x for x in repository.ablation_mechanisms if x.component_id in ids]
            output.append(AblationAlignment(alignment_id=stable_id("ablation",experiment.experiment_id),paper_experiment_id=experiment.experiment_id,paper_ablation=experiment.variant or experiment.name,repository_ablation_ids=ids,status=_status(top,len(ids)),confidence=self.scorer.score(tuple(s for x in selected for s in x.signals),experiment.evidence,_evidence(records)),reasoning="repository flag/config/weight candidate required",paper_evidence=experiment.evidence,repository_evidence=_evidence(records)))
        return tuple(output)
    def _metrics(self,paper,repository):
        output=[];conflicts=[]
        claims_by_id={x.id:x for x in (*paper.paper_claims,*(claim for experiment in paper.experiments for claim in experiment.claims))};claims=list(claims_by_id.values());groups={}
        for claim in claims:groups.setdefault(normalize_entity(claim.metric_name).canonical_name,[]).append(claim)
        for key,items in groups.items():
            matches=[x for x in repository.metrics if name_strength(normalize_entity(items[0].metric_name),normalize_entity(x.name))[0]>=.45];status=_status(1 if len(matches)==1 else .5,len(matches));repo_agg=next((str(x.details.get("aggregation")) for x in matches if x.details.get("aggregation") is not None),None);paper_agg=next((x.condition for x in items if x.condition and any(y in x.condition.casefold() for y in ("macro","micro","weighted"))),None);paper_split=next((x.split for x in items if x.split),None);repo_split=next((str(x.details.get("split")) for x in matches if x.details.get("split") is not None),None);conflict_id=None
            mismatch=(paper_agg and repo_agg and normalize_entity(paper_agg).canonical_name!=normalize_entity(repo_agg).canonical_name) or (paper_split and repo_split and normalize_entity(paper_split).canonical_name!=normalize_entity(repo_split).canonical_name)
            if mismatch:
                status=AlignmentStatus.CONFLICTED;conflict_id=stable_id("conflict","metric",key);paper_definition={"aggregation":paper_agg,"split":paper_split};repo_definition={"aggregation":repo_agg,"split":repo_split};conflicts.append(AlignmentConflict(conflict_id=conflict_id,semantic_key=f"metric:{key}:definition",conflict_type=AlignmentConflictType.METRIC_DEFINITION_MISMATCH,candidates=(AlignmentConflictCandidate(source=AlignmentItemSource.PAPER,value=paper_definition,evidence=_evidence(items)),AlignmentConflictCandidate(source=AlignmentItemSource.REPOSITORY,value=repo_definition,evidence=_evidence(matches))),reasoning="metric split or aggregation differs"))
            output.append(MetricAlignment(alignment_id=stable_id("metric",key),paper_metric=items[0].metric_name,paper_claim_ids=tuple(x.id for x in items),repository_metric_ids=tuple(x.component_id for x in matches),status=status,confidence=.9 if len(matches)==1 else (.4 if matches else 0),paper_split=paper_split,repository_split=repo_split,paper_aggregation=paper_agg,repository_aggregation=repo_agg,reasoning="deterministic metric identity, split and aggregation comparison",paper_evidence=_evidence(items),repository_evidence=_evidence(matches),conflict_id=conflict_id))
        return tuple(output),tuple(conflicts)
    def _evaluation_policies(self,paper,repository,experiments):
        output=[];conflicts=[];paper_records={x.experiment_id:x for x in paper.experiments}
        for experiment in experiments:
            paper_record=paper_records[experiment.paper_experiment_id]
            paper_policy=paper_record.evaluation_policy or paper.evaluation_policy
            repository_records=[
                item for item in repository.evaluation_policies
                if (item.implementation_id and item.implementation_id in experiment.repository_implementation_ids)
                or set(item.entrypoint_ids)&set(experiment.entrypoint_ids)
            ]
            signatures={self._policy_signature(item.policy) for item in repository_records if item.policy.is_resolved}
            code_policy=next((item.policy for item in repository_records if item.policy.is_resolved),None) if len(signatures)<=1 else None
            paper_resolved=paper_policy is not None and paper_policy.is_resolved
            code_resolved=code_policy is not None and code_policy.is_resolved
            conflict_id=None
            if len(signatures)>1:
                status=EvaluationPolicyAlignmentStatus.AMBIGUOUS;resolved=None;reason="repository contains multiple explicit final-result policies"
                conflict_id=stable_id("conflict","evaluation-policy",experiment.paper_experiment_id,"ambiguous")
                conflicts.append(AlignmentConflict(conflict_id=conflict_id,semantic_key=f"evaluation_policy:{experiment.paper_experiment_id}",conflict_type=AlignmentConflictType.EVALUATION_POLICY_CONFLICT,candidates=tuple(AlignmentConflictCandidate(source=AlignmentItemSource.REPOSITORY,value=item.policy.model_dump(mode="json"),evidence=item.evidence) for item in repository_records if item.policy.is_resolved),reasoning=reason))
            elif paper_resolved and code_resolved and self._policy_signature(paper_policy)!=self._policy_signature(code_policy):
                status=EvaluationPolicyAlignmentStatus.CONFLICT;resolved=paper_policy;reason="paper is authoritative, but repository behavior deviates and requires an evidenced sandbox adaptation"
                conflict_id=stable_id("conflict","evaluation-policy",experiment.paper_experiment_id)
                conflicts.append(AlignmentConflict(conflict_id=conflict_id,semantic_key=f"evaluation_policy:{experiment.paper_experiment_id}",conflict_type=AlignmentConflictType.EVALUATION_POLICY_CONFLICT,candidates=(AlignmentConflictCandidate(source=AlignmentItemSource.PAPER,value=paper_policy.model_dump(mode="json"),evidence=self._policy_evidence(paper_policy)),AlignmentConflictCandidate(source=AlignmentItemSource.REPOSITORY,value=code_policy.model_dump(mode="json"),evidence=self._policy_evidence(code_policy))),reasoning=reason))
            elif paper_resolved and code_resolved:
                status=EvaluationPolicyAlignmentStatus.ALIGNED;resolved=paper_policy;reason="paper and repository evaluation policies agree"
            elif paper_resolved:
                status=EvaluationPolicyAlignmentStatus.PAPER_ONLY;resolved=paper_policy;reason="paper explicitly defines the final-result policy and code does not"
            elif code_resolved:
                status=EvaluationPolicyAlignmentStatus.CODE_FALLBACK;resolved=code_policy;reason="paper is unknown; explicit repository behavior supplies the policy"
            else:
                resolved=self._scientific_default(paper_record,paper_policy,paper)
                if resolved is None:
                    status=EvaluationPolicyAlignmentStatus.UNKNOWN;reason="neither source defines a complete policy and no safe deterministic scientific default is available"
                else:
                    status=EvaluationPolicyAlignmentStatus.SCIENTIFIC_DEFAULT;reason="reporting targets are explicit and FINAL_EPOCH avoids unsupported epoch or test-metric maximization"
            repository_evidence=tuple(dict.fromkeys(value for item in repository_records for value in (*item.evidence,*self._policy_evidence(item.policy))))
            adaptation_supported=bool(repository_records) and all(item.paper_policy_adaptation_supported for item in repository_records) if status is EvaluationPolicyAlignmentStatus.CONFLICT else False
            alignment_warnings=("REPOSITORY_EVALUATION_DEVIATION",) if status is EvaluationPolicyAlignmentStatus.CONFLICT else (("INFERRED_EVALUATION_POLICY",) if status is EvaluationPolicyAlignmentStatus.SCIENTIFIC_DEFAULT else ())
            output.append(EvaluationPolicyAlignment(alignment_id=stable_id("evaluation-policy",experiment.paper_experiment_id),paper_experiment_id=experiment.paper_experiment_id,repository_policy_ids=tuple(item.policy_id for item in repository_records),paper_policy=paper_policy,code_policy=code_policy,resolved_policy=resolved,status=status,reasoning=reason,confidence=1 if status is EvaluationPolicyAlignmentStatus.ALIGNED else .9 if status in {EvaluationPolicyAlignmentStatus.PAPER_ONLY,EvaluationPolicyAlignmentStatus.CODE_FALLBACK,EvaluationPolicyAlignmentStatus.CONFLICT} else .6 if status is EvaluationPolicyAlignmentStatus.SCIENTIFIC_DEFAULT else .4 if status is EvaluationPolicyAlignmentStatus.AMBIGUOUS else 0,paper_evidence=self._policy_evidence(paper_policy),repository_evidence=repository_evidence,conflict_id=conflict_id,adaptation_supported=adaptation_supported,warnings=alignment_warnings))
        return tuple(output),tuple(conflicts)
    def _scientific_default(self,paper_record,paper_policy,paper):
        claims=tuple((*paper_record.claims,*(item for item in paper.paper_claims if item.target_id==paper_record.experiment_id)))
        if paper_policy is not None and paper_policy.reporting_split and paper_policy.reporting_metrics:
            reporting_split=paper_policy.reporting_split;reporting_metrics=paper_policy.reporting_metrics
            run_count=paper_policy.run_count;seeds=paper_policy.seeds;aggregation=paper_policy.aggregation
            evidence=paper_policy.evidence
        else:
            splits={item.split for item in claims if item.split is not None}
            if len(splits)!=1 or any(item.split is None for item in claims) or not claims:return None
            reporting_split=next(iter(splits));reporting_metrics=tuple(dict.fromkeys(item.metric_name for item in claims))
            run_count=1;seeds=();aggregation=ResultAggregation.NONE
            evidence=tuple(item.model_dump(mode="json") for claim in claims for item in claim.evidence)
        if not reporting_metrics:return None
        return EvaluationPolicy(checkpoint_policy=CheckpointPolicy.FINAL_EPOCH,reporting_split=reporting_split,reporting_metrics=reporting_metrics,run_count=run_count,seeds=seeds,aggregation=aggregation,source=EvaluationPolicySource.SCIENTIFIC_DEFAULT,evidence=evidence,confidence=EvaluationPolicyConfidence.INFERRED,warnings=("INFERRED_EVALUATION_POLICY",))
    @staticmethod
    def _policy_signature(policy):
        return policy.model_dump_json(exclude={"source","evidence","confidence","warnings"})
    @staticmethod
    def _policy_evidence(policy):
        if policy is None:return ()
        values=[]
        for item in policy.evidence:
            try:values.append(item if isinstance(item,EvidenceReference) else EvidenceReference.model_validate(item))
            except Exception:continue
        return tuple(values)
    def _experiments(self,paper,repository,candidates,datasets,models,parameters,ablations,metrics):
        output=[];conflicts=[]
        for experiment in paper.experiments:
            found=[x for x in candidates if x.category=="experiment" and x.paper_item_id==experiment.experiment_id];top=max((x.score for x in found),default=0);selected=[x for x in found if top-x.score<=.05 and x.score>=.2];ids=tuple(x.repository_item_ids[0] for x in selected);implementations=[x for x in repository.experiment_implementations if x.implementation_id in ids];status=_status(top,len(ids));conflict_ids=[]
            if len(ids)>1:
                conflict_id=stable_id("conflict","experiment",experiment.experiment_id);conflict_ids.append(conflict_id);conflicts.append(AlignmentConflict(conflict_id=conflict_id,semantic_key=f"experiment:{experiment.experiment_id}",conflict_type=AlignmentConflictType.MULTIPLE_IMPLEMENTATIONS,candidates=tuple(AlignmentConflictCandidate(source=AlignmentItemSource.REPOSITORY,value=x.implementation_id,evidence=x.evidence) for x in implementations),reasoning="multiple similarly scored repository implementations"))
            if not ids:
                conflict_id=stable_id("conflict","missing",experiment.experiment_id);conflict_ids.append(conflict_id);conflicts.append(AlignmentConflict(conflict_id=conflict_id,semantic_key=f"experiment:{experiment.experiment_id}:implementation",conflict_type=AlignmentConflictType.MISSING_IMPLEMENTATION,candidates=(AlignmentConflictCandidate(source=AlignmentItemSource.PAPER,value=experiment.name,evidence=experiment.evidence),),reasoning="no trustworthy repository implementation was found"))
            dataset=next((x for x in datasets if experiment.dataset and normalize_entity(x.paper_dataset).canonical_name==normalize_entity(experiment.dataset).canonical_name),None);model=next((x for x in models if experiment.model and normalize_entity(x.paper_model).canonical_name==normalize_entity(experiment.model).canonical_name),None);selected_parameters=tuple(x for x in parameters if x.paper_experiment_id in {None,experiment.experiment_id});param_ids=tuple(x.alignment_id for x in selected_parameters);ablation_ids=tuple(x.alignment_id for x in ablations if x.paper_experiment_id==experiment.experiment_id);selected_metrics=tuple(x for x in metrics if set(x.paper_claim_ids)&{c.id for c in experiment.claims});metric_ids=tuple(x.alignment_id for x in selected_metrics)
            conflict_ids.extend(x.conflict_id for x in selected_parameters if x.conflict_id);conflict_ids.extend(x.conflict_id for x in selected_metrics if x.conflict_id)
            if ids and conflict_ids and status is not AlignmentStatus.AMBIGUOUS:status=AlignmentStatus.CONFLICTED
            output.append(ExperimentAlignmentRecord(alignment_id=stable_id("experiment",paper.catalog_id,repository.snapshot_id,experiment.experiment_id),paper_experiment_id=experiment.experiment_id,repository_implementation_ids=ids,status=status,confidence=self.scorer.score(tuple(s for x in selected for s in x.signals),experiment.evidence,_evidence(implementations)),reasoning_summary="bounded deterministic candidate scoring; semantic review may refine unresolved cases",entrypoint_ids=tuple(dict.fromkeys(x for y in implementations for x in y.entrypoint_ids)),config_ids=tuple(dict.fromkeys(x for y in implementations for x in y.config_ids)),command_ids=tuple(dict.fromkeys(x for y in implementations for x in y.command_ids)),parameter_mapping_ids=param_ids,dataset_mapping_id=dataset.alignment_id if dataset else None,model_mapping_id=model.alignment_id if model else None,ablation_mapping_ids=ablation_ids,metric_mapping_ids=metric_ids,paper_evidence=experiment.evidence,repository_evidence=_evidence(implementations),conflict_ids=tuple(conflict_ids)))
        return tuple(output),tuple(conflicts)

class AlignmentConflictResolver:
    """Only resolves deterministically equivalent numeric representations."""
    def resolve(self,conflict):
        values=[x.value for x in conflict.candidates]
        if len(values)>=2 and all(_equal(values[0],x) for x in values[1:]):return conflict.model_copy(update={"status":AlignmentConflictStatus.RESOLVED,"resolution":values[0],"reasoning":"values are deterministically equivalent representations"})
        return conflict
