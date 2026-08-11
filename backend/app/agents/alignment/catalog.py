"""Alignment catalog merge and referential validation."""
from __future__ import annotations
from backend.app.domain import *
from backend.app.services import AlignmentValidationError
from .candidates import stable_id
from .evidence import AlignmentEvidenceValidationError,AlignmentEvidenceValidator

def _unique(values,field):
    result={}
    for value in values:result[getattr(value,field)]=value
    return tuple(result.values())
def _merge(base,semantic,field,status_field="status"):
    result={getattr(x,field):x for x in base}
    strong={"aligned","conflicted","matched","value_conflict"}
    for item in semantic:
        key=getattr(item,field);old=result.get(key)
        if old is None or getattr(getattr(old,status_field,None),"value","") not in strong:result[key]=item
    return tuple(result.values())
def _all_evidence(catalog):
    evidence=list(catalog.evidence)
    for group in (catalog.experiment_alignments,catalog.dataset_mappings,catalog.model_mappings,catalog.parameter_mappings,catalog.ablation_mappings,catalog.metric_mappings):
        for item in group:evidence.extend(getattr(item,"paper_evidence",()));evidence.extend(getattr(item,"repository_evidence",()))
    for conflict in catalog.conflicts:
        for candidate in conflict.candidates:evidence.extend(candidate.evidence)
    for item in (*catalog.unmatched_paper_items,*catalog.unmatched_repository_items,*catalog.ambiguities):evidence.extend(item.evidence)
    return tuple(dict.fromkeys(evidence))

class AlignmentCatalogMerger:
    def merge(self,paper,repository,deterministic,stages,missing=(),warnings=()):
        semantic=[x for _,stage in stages for x in stage.experiment_alignments];experiments=_merge(deterministic.experiments,semantic,"alignment_id")
        datasets=_merge(deterministic.datasets,[x for _,s in stages for x in s.dataset_mappings],"alignment_id")
        models=_merge(deterministic.models,[x for _,s in stages for x in s.model_mappings],"alignment_id")
        parameters=_merge(deterministic.parameters,[x for _,s in stages for x in s.parameter_mappings],"alignment_id","mapping_status")
        ablations=_merge(deterministic.ablations,[x for _,s in stages for x in s.ablation_mappings],"alignment_id")
        metrics=_merge(deterministic.metrics,[x for _,s in stages for x in s.metric_mappings],"alignment_id")
        conflict_map={x.conflict_id:x for x in deterministic.conflicts}
        for item in (x for _,s in stages for x in s.conflicts):
            if item.conflict_id not in conflict_map:
                conflict_map[item.conflict_id]=item.model_copy(update={"status":AlignmentConflictStatus.UNRESOLVED,"resolution":None,"resolution_recommendation":item.resolution_recommendation or (str(item.resolution) if item.resolution is not None else None)})
        conflicts=tuple(conflict_map.values())
        used={x for record in experiments for x in record.repository_implementation_ids};unmatched_repo=tuple(x for x in deterministic.unmatched_repository if x.category!="experiment_implementation" or x.item_id not in used);unmatched_paper=tuple(x for x in deterministic.unmatched_paper if x.category!="experiment" or any(y.paper_experiment_id==x.item_id and y.status is AlignmentStatus.NOT_FOUND for y in experiments))
        status=AlignmentAnalysisStatus.PARTIAL if missing else AlignmentAnalysisStatus.COMPLETE
        ambiguities=[AlignmentAmbiguity(ambiguity_id=stable_id("ambiguity",x.paper_experiment_id),paper_item_id=x.paper_experiment_id,candidate_repository_ids=x.repository_implementation_ids,reasoning=x.reasoning_summary,evidence=(*x.paper_evidence,*x.repository_evidence)) for x in experiments if x.status is AlignmentStatus.AMBIGUOUS and len(x.repository_implementation_ids)>=2]
        for group,ids_field,name_field in ((datasets,"repository_dataset_ids","paper_dataset"),(models,"repository_model_ids","paper_model"),(ablations,"repository_ablation_ids","paper_ablation"),(metrics,"repository_metric_ids","paper_metric")):
            ambiguities.extend(AlignmentAmbiguity(ambiguity_id=stable_id("ambiguity",x.alignment_id),paper_item_id=getattr(x,name_field),candidate_repository_ids=getattr(x,ids_field),reasoning=x.reasoning,evidence=(*x.paper_evidence,*x.repository_evidence)) for x in group if x.status is AlignmentStatus.AMBIGUOUS and len(getattr(x,ids_field))>=2)
        catalog=PaperCodeAlignmentCatalog(catalog_id=stable_id("alignment-catalog",paper.catalog_id,repository.snapshot_id),paper_catalog_id=paper.catalog_id,paper=paper.paper,repository_catalog_id=repository.catalog_id,repository=repository.repository,repository_snapshot_id=repository.snapshot_id,resolved_commit_sha=repository.resolved_commit_sha,experiment_alignments=experiments,dataset_mappings=datasets,model_mappings=models,parameter_mappings=parameters,ablation_mappings=ablations,metric_mappings=metrics,unmatched_paper_items=unmatched_paper,unmatched_repository_items=unmatched_repo,ambiguities=tuple(ambiguities),conflicts=conflicts,alignment_status=status,alignment_metadata=AlignmentMetadata(stages_completed=tuple(x for x,_ in stages),missing_components=tuple(dict.fromkeys(missing)),warnings=tuple(dict.fromkeys(warnings)),prompt_versions={x:"v1" for x in ("candidate_classification","stage_alignment","repair","catalog_review")}))
        return catalog.model_copy(update={"evidence":_all_evidence(catalog)})

class PaperCodeAlignmentValidator:
    def __init__(self,evidence_validator=None):self.evidence=evidence_validator or AlignmentEvidenceValidator()
    def validate(self,catalog,paper,repository,*,paper_document=None,repository_snapshot=None,static_analysis=None):
        if catalog.paper_catalog_id!=paper.catalog_id or catalog.repository_catalog_id!=repository.catalog_id or catalog.repository_snapshot_id!=repository.snapshot_id or catalog.resolved_commit_sha!=repository.resolved_commit_sha:raise AlignmentValidationError("alignment catalog source identity mismatch")
        experiments={x.experiment_id for x in paper.experiments};implementations={x.implementation_id for x in repository.experiment_implementations};entries={x.entrypoint_id for x in repository.entrypoints};configs={x.config_id for x in repository.configurations};commands={x.command_id for x in repository.commands};datasets={x.component_id for x in repository.datasets};models={x.component_id for x in repository.models};ablations={x.component_id for x in repository.ablation_mechanisms};metrics={x.component_id for x in repository.metrics}
        paper_dataset_names={x.canonical_name.casefold() for x in paper.datasets};paper_model_names={x.canonical_name.casefold() for x in paper.model_variants}
        mapping_ids={x.alignment_id for x in (*catalog.dataset_mappings,*catalog.model_mappings,*catalog.parameter_mappings,*catalog.ablation_mappings,*catalog.metric_mappings)};conflict_ids={x.conflict_id for x in catalog.conflicts}
        backing={"paper_document":paper_document,"repository_snapshot":repository_snapshot,"static_analysis":static_analysis}
        aligned_paper_ids=[x.paper_experiment_id for x in catalog.experiment_alignments]
        if len(aligned_paper_ids)!=len(set(aligned_paper_ids)):raise AlignmentValidationError("duplicate paper experiment alignment")
        for item in catalog.experiment_alignments:
            if item.paper_experiment_id not in experiments or not set(item.repository_implementation_ids)<=implementations or not set(item.entrypoint_ids)<=entries or not set(item.config_ids)<=configs or not set(item.command_ids)<=commands:raise AlignmentValidationError(f"dangling experiment alignment: {item.alignment_id}")
            if not set((*item.parameter_mapping_ids,*item.ablation_mapping_ids,*item.metric_mapping_ids))<=mapping_ids or item.dataset_mapping_id and item.dataset_mapping_id not in mapping_ids or item.model_mapping_id and item.model_mapping_id not in mapping_ids or not set(item.conflict_ids)<=conflict_ids:raise AlignmentValidationError(f"dangling mapping reference: {item.alignment_id}")
            self._status(item.status,item.repository_implementation_ids,item.alignment_id);self.evidence.validate_mapping(item,paper,repository,**backing)
        for item in catalog.dataset_mappings:
            if item.paper_dataset.casefold() not in paper_dataset_names or not set(item.repository_dataset_ids)<=datasets:raise AlignmentValidationError(f"dangling dataset mapping: {item.alignment_id}")
            self._status(item.status,item.repository_dataset_ids,item.alignment_id);self.evidence.validate_mapping(item,paper,repository,**backing)
        for item in catalog.model_mappings:
            if item.paper_model.casefold() not in paper_model_names or not set(item.repository_model_ids)<=models:raise AlignmentValidationError(f"dangling model mapping: {item.alignment_id}")
            self._status(item.status,item.repository_model_ids,item.alignment_id);self.evidence.validate_mapping(item,paper,repository,**backing)
        for item in catalog.parameter_mappings:
            if item.paper_experiment_id and item.paper_experiment_id not in experiments or not set(item.repository_config_ids)<=configs or item.conflict_id and item.conflict_id not in conflict_ids:raise AlignmentValidationError(f"dangling parameter mapping: {item.alignment_id}")
            self.evidence.validate_mapping(item,paper,repository,**backing)
            if item.mapping_status in {ParameterMappingStatus.PAPER_ONLY,ParameterMappingStatus.NOT_FOUND} and (item.repository_config_ids or item.repository_source):raise AlignmentValidationError(f"paper-only parameter has repository references: {item.alignment_id}")
            if item.mapping_status is ParameterMappingStatus.VALUE_CONFLICT and not item.conflict_id:raise AlignmentValidationError(f"value conflict lacks conflict record: {item.alignment_id}")
        for item in catalog.ablation_mappings:
            if item.paper_experiment_id and item.paper_experiment_id not in experiments or not set(item.repository_ablation_ids)<=ablations:raise AlignmentValidationError(f"dangling ablation mapping: {item.alignment_id}")
            self._status(item.status,item.repository_ablation_ids,item.alignment_id);self.evidence.validate_mapping(item,paper,repository,**backing)
        claim_ids={x.id for x in paper.paper_claims}|{x.id for experiment in paper.experiments for x in experiment.claims}
        for item in catalog.metric_mappings:
            if not set(item.paper_claim_ids)<=claim_ids or not set(item.repository_metric_ids)<=metrics or item.conflict_id and item.conflict_id not in conflict_ids:raise AlignmentValidationError(f"dangling metric mapping: {item.alignment_id}")
            self._status(item.status,item.repository_metric_ids,item.alignment_id);self.evidence.validate_mapping(item,paper,repository,**backing)
        for conflict in catalog.conflicts:
            if conflict.status is AlignmentConflictStatus.RESOLVED and conflict.resolution not in [x.value for x in conflict.candidates]:raise AlignmentValidationError(f"invalid conflict resolution: {conflict.conflict_id}")
            self.evidence.validate(tuple(x for candidate in conflict.candidates for x in candidate.evidence),paper,repository,**backing)
        all_repository_ids=implementations|datasets|models|ablations|metrics|configs|entries|commands
        for ambiguity in catalog.ambiguities:
            if not set(ambiguity.candidate_repository_ids)<=all_repository_ids:raise AlignmentValidationError(f"dangling ambiguity: {ambiguity.ambiguity_id}")
            self.evidence.validate(ambiguity.evidence,paper,repository,**backing)
        for item in (*catalog.unmatched_paper_items,*catalog.unmatched_repository_items):self.evidence.validate(item.evidence,paper,repository,**backing)
        self.evidence.validate(catalog.evidence,paper,repository,**backing);return catalog
    @staticmethod
    def _status(status,ids,label):
        if status is AlignmentStatus.NOT_FOUND and ids:raise AlignmentValidationError(f"NOT_FOUND mapping has repository candidates: {label}")
        if status in {AlignmentStatus.ALIGNED,AlignmentStatus.PARTIALLY_ALIGNED,AlignmentStatus.CONFLICTED} and not ids:raise AlignmentValidationError(f"positive mapping has no repository candidate: {label}")
        if status is AlignmentStatus.AMBIGUOUS and len(ids)<2:raise AlignmentValidationError(f"ambiguous mapping requires multiple candidates: {label}")
