"""Cross-source validation by delegation to the existing paper/repository validators."""
from __future__ import annotations
from pydantic import BaseModel
from backend.app.agents.paper.evidence import EvidenceValidator
from backend.app.agents.repository.evidence import RepositoryEvidenceValidator
from backend.app.domain import EvidenceReference,EvidenceSourceType

class AlignmentEvidenceValidationError(ValueError):pass
def _walk(value):
    if isinstance(value,EvidenceReference):yield value
    elif isinstance(value,BaseModel):
        for field in value.__class__.model_fields:yield from _walk(getattr(value,field))
    elif isinstance(value,(tuple,list,set)):
        for item in value:yield from _walk(item)
    elif isinstance(value,dict):
        for item in value.values():yield from _walk(item)
def _key(item):return item.model_dump_json()

class AlignmentEvidenceValidator:
    def __init__(self,paper_validator=None,repository_validator=None):self.paper=paper_validator or EvidenceValidator();self.repository=repository_validator or RepositoryEvidenceValidator()
    def validate(self,evidence,paper_catalog,repository_catalog,*,paper_document=None,repository_snapshot=None,static_analysis=None):
        paper_allowed={_key(x) for x in _walk(paper_catalog) if x.source_type is EvidenceSourceType.PAPER};repo_allowed={_key(x) for x in _walk(repository_catalog) if x.source_type is EvidenceSourceType.REPOSITORY}
        for item in evidence:
            if item.source_type is EvidenceSourceType.PAPER:
                if paper_document is not None:self.paper.validate(item,paper_document)
                elif _key(item) not in paper_allowed:raise AlignmentEvidenceValidationError(f"paper evidence is not present in validated paper catalog: {item.locator}")
            elif item.source_type is EvidenceSourceType.REPOSITORY:
                if repository_snapshot is not None and (static_analysis is not None or (item.locator or "").startswith(("file:","script:"))):self.repository.validate(item,repository_snapshot,static_analysis)
                elif _key(item) not in repo_allowed:raise AlignmentEvidenceValidationError(f"repository evidence is not present in validated repository catalog: {item.locator}")
            else:raise AlignmentEvidenceValidationError("alignment evidence must come from paper or repository")
    def validate_mapping(self,mapping,paper_catalog,repository_catalog,**backing):
        paper=tuple(getattr(mapping,"paper_evidence",()));repository=tuple(getattr(mapping,"repository_evidence",()));status=getattr(mapping,"status",None);mapping_status=getattr(mapping,"mapping_status",None)
        requires_pair=(status is not None and status.value not in {"not_found"}) or (mapping_status is not None and mapping_status.value in {"matched","semantic_match_value_unknown","value_conflict","ambiguous"})
        if requires_pair and (not paper or not repository):raise AlignmentEvidenceValidationError("mapped cross-source conclusion requires paper and repository evidence")
        self.validate((*paper,*repository),paper_catalog,repository_catalog,**backing)
