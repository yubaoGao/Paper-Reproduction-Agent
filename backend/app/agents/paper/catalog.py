"""Deterministic catalog merge, conflict preservation, and validation."""
from __future__ import annotations
import re
from collections import defaultdict
from backend.app.domain import (
    CatalogEntity, ConflictCandidate, ConflictType, EvidenceReference,
    ExperimentType, ExtractionConflict, ExtractionMetadata, ExtractionStatus,
    PaperClaim, PaperDocument, PaperExperimentCatalog, PaperExperimentRecord,
    ReproductionParameter,
)
from .evidence import EvidenceValidationError, EvidenceValidator
from .identity import StableExperimentIdentityGenerator
from .schemas import StageExtraction

class PaperExtractionError(RuntimeError): pass
class CatalogValidationError(PaperExtractionError): pass

def _norm(value:str|None)->str: return re.sub(r"[^a-z0-9]+","",(value or "").casefold())
def _unique(items):
    seen=set(); result=[]
    for item in items:
        key=item.model_dump_json() if hasattr(item,"model_dump_json") else str(item)
        if key not in seen: seen.add(key); result.append(item)
    return tuple(result)

class CatalogMerger:
    def __init__(self, identity_generator: StableExperimentIdentityGenerator | None = None):
        self.identity_generator = identity_generator or StableExperimentIdentityGenerator()

    def merge(self,document:PaperDocument,stages:tuple[StageExtraction,...],*,missing=(),warnings=(),figure_observations=()):
        datasets=self._entities([x for stage in stages for x in stage.datasets])
        models=self._entities([x for stage in stages for x in stage.model_variants])
        raw_experiments=[x for stage in stages for x in stage.experiments]
        experiments,references=self.identity_generator.assign(document.paper,raw_experiments,datasets=datasets,models=models)
        training=self._parameters([x for stage in stages for x in stage.training_parameters])
        evaluation=self._parameters([x for stage in stages for x in stage.evaluation_parameters])
        stage_claims=self.identity_generator.remap_claims([x for stage in stages for x in stage.claims],references)
        claims,conflicts=self._claims(stage_claims,experiments)
        evidence=_unique([x for stage in stages for x in stage.evidence])
        stage_missing=tuple(x for stage in stages for x in stage.missing_components)
        stage_warnings=tuple(x for stage in stages for x in stage.warnings)
        all_missing=tuple(dict.fromkeys((*missing,*stage_missing)))
        status=ExtractionStatus.PARTIAL if all_missing else ExtractionStatus.COMPLETE
        return PaperExperimentCatalog(
            catalog_id=f"catalog:{document.document_id}",document_id=document.document_id,paper=document.paper,
            datasets=datasets,model_variants=models,experiments=experiments,
            training_parameters=training,evaluation_parameters=evaluation,paper_claims=claims,
            evidence=evidence,conflicts=conflicts,figure_observations=tuple(figure_observations),extraction_status=status,
            extraction_metadata=ExtractionMetadata(stages_completed=tuple(str(i+1) for i in range(len(stages))),missing_components=all_missing,warnings=tuple(dict.fromkeys((*warnings,*stage_warnings)))),
        )
    def _entities(self,items):
        groups=[]
        for item in items:
            names={_norm(item.canonical_name),*(_norm(x) for x in item.aliases)}
            found=next((group for group in groups if names & group[0]),None)
            if found:
                found[0].update(names); found[1].append(item)
            else: groups.append([set(names),[item]])
        result=[]
        for _keys,values in groups:
            canonical=values[0].canonical_name
            aliases=tuple(dict.fromkeys(x for value in values for x in (value.canonical_name,*value.aliases) if x.casefold()!=canonical.casefold()))
            result.append(CatalogEntity(canonical_name=canonical,aliases=aliases,evidence=_unique([x for value in values for x in value.evidence])))
        return tuple(result)
    def _parameters(self,items):
        result={}
        for item in items:
            key=item.name.casefold()
            if key not in result: result[key]=item
            elif result[key].value==item.value and result[key].status==item.status:
                result[key]=result[key].model_copy(update={"evidence":_unique((*result[key].evidence,*item.evidence))})
        return tuple(result.values())
    def _claims(self,items,experiments):
        # Include claims nested by stage experiment and normalize their target id.
        items=list(items)+[claim for record in experiments for claim in record.claims]
        grouped=defaultdict(list)
        for claim in items:
            key=(claim.target_id,_norm(claim.metric_name),_norm(claim.dataset),_norm(claim.split),_norm(claim.condition),claim.unit)
            grouped[key].append(claim)
        output=[]; conflicts=[]
        for index,(key,values) in enumerate(grouped.items(),start=1):
            by_value=defaultdict(list)
            for claim in values: by_value[claim.value].append(claim)
            for same in by_value.values():
                base=same[0]; output.append(base.model_copy(update={"evidence":_unique([x for claim in same for x in claim.evidence])}))
            if len(by_value)>1:
                conflicts.append(ExtractionConflict(conflict_id=f"conflict-{index}",semantic_key="|".join(str(x or "") for x in key),conflict_type=ConflictType.VALUE_MISMATCH,candidates=tuple(ConflictCandidate(value=value,evidence=_unique([x for claim in claims for x in claim.evidence])) for value,claims in by_value.items())))
        unique_output=[]; used_ids=set()
        for claim in output:
            claim_id=claim.id
            if claim_id in used_ids:
                suffix=claim.target_id or str(len(used_ids)+1); claim_id=f"{claim_id}:{suffix}"
                counter=2
                while claim_id in used_ids: claim_id=f"{claim.id}:{suffix}:{counter}"; counter+=1
                claim=claim.model_copy(update={"id":claim_id})
            used_ids.add(claim_id); unique_output.append(claim)
        return tuple(unique_output),tuple(conflicts)

class CatalogValidator:
    def __init__(self,evidence_validator:EvidenceValidator|None=None): self.evidence_validator=evidence_validator or EvidenceValidator()
    def validate(self,catalog:PaperExperimentCatalog,document:PaperDocument)->None:
        if catalog.document_id!=document.document_id: raise CatalogValidationError("catalog document_id mismatch")
        experiments={x.experiment_id:x for x in catalog.experiments}
        dataset_names={_norm(name) for entity in catalog.datasets for name in (entity.canonical_name,*entity.aliases)}
        all_claims=list(catalog.paper_claims)
        evidence=list(catalog.evidence)
        for entity in (*catalog.datasets,*catalog.model_variants):
            if not entity.evidence: raise CatalogValidationError(f"entity {entity.canonical_name} has no evidence")
            evidence.extend(entity.evidence)
        for parameter in (*catalog.training_parameters,*catalog.evaluation_parameters): evidence.extend(parameter.evidence); self.evidence_validator.validate_parameter(parameter,document)
        for record in catalog.experiments:
            if not record.evidence: raise CatalogValidationError(f"experiment {record.experiment_id} has no evidence")
            if record.dataset and _norm(record.dataset) not in dataset_names: raise CatalogValidationError(f"unknown dataset in experiment {record.experiment_id}")
            if record.experiment_type is ExperimentType.ABLATION and not record.parent_experiment_id: raise CatalogValidationError(f"ablation {record.experiment_id} has no target experiment")
            for section in record.source_sections:
                if not any(x.section_id==section for x in document.sections): raise CatalogValidationError(f"unknown source section: {section}")
            for table in record.source_tables:
                if not any(x.table_id==table for x in document.tables): raise CatalogValidationError(f"unknown source table: {table}")
            for figure in record.source_figures:
                if not any(x.figure_id==figure for x in document.figures): raise CatalogValidationError(f"unknown source figure: {figure}")
            all_claims.extend(record.claims); evidence.extend(record.evidence)
            for parameter in record.parameters: evidence.extend(parameter.evidence); self.evidence_validator.validate_parameter(parameter,document)
        for claim in all_claims:
            if claim.target_id and claim.target_id not in experiments: raise CatalogValidationError(f"claim {claim.id} has dangling experiment reference")
            evidence.extend(claim.evidence)
            self.evidence_validator.validate_claim(claim,document)
        for observation in catalog.figure_observations: evidence.extend(observation.evidence)
        for conflict in catalog.conflicts:
            if conflict.status.value=="resolved" and not any(conflict.resolution==candidate.value for candidate in conflict.candidates): raise CatalogValidationError(f"conflict {conflict.conflict_id} resolves to a non-candidate value")
            for candidate in conflict.candidates: evidence.extend(candidate.evidence)
        semantic_seen={}
        conflict_keys={item.semantic_key for item in catalog.conflicts}
        for claim in catalog.paper_claims:
            key=(claim.target_id,_norm(claim.metric_name),_norm(claim.dataset),_norm(claim.split),_norm(claim.condition),claim.unit)
            semantic="|".join(str(x or "") for x in key)
            if key in semantic_seen:
                if semantic_seen[key]==claim.value: raise CatalogValidationError("duplicate semantic claim")
                if semantic not in conflict_keys: raise CatalogValidationError("conflicting claims lack ExtractionConflict")
            semantic_seen[key]=claim.value
        try: self.evidence_validator.validate_all(_unique(evidence),document)
        except EvidenceValidationError as exc: raise CatalogValidationError(str(exc)) from exc
