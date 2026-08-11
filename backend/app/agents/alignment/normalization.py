"""Conservative entity normalization used only to generate candidates."""
from __future__ import annotations
import re
from dataclasses import dataclass

_SEMANTIC={"lr":"learningrate","learning_rate":"learningrate","batchsize":"batchsize","batch_size":"batchsize","wd":"weightdecay","weight_decay":"weightdecay","centre":"center","mvsa_single":"mvsas","mvsa_s":"mvsas"}
def canonical_name(value:str)->str:
    raw=re.sub(r"[^a-z0-9]+","",value.casefold())
    normalized=_SEMANTIC.get(value.casefold().replace("-","_"),_SEMANTIC.get(raw,raw))
    for suffix in ("dataset","model","metric","score","implementation"):
        if normalized.endswith(suffix) and len(normalized)>len(suffix)+1:normalized=normalized[:-len(suffix)]
    return normalized
def tokens(value:str)->tuple[str,...]:return tuple(x for x in re.findall(r"[a-z0-9]+",value.casefold()) if x not in {"the","model","experiment","config","dataset"})
@dataclass(frozen=True)
class NormalizedEntity:
    original_name:str;canonical_name:str;aliases:tuple[str,...]
    @property
    def keys(self):return {canonical_name(x) for x in (self.original_name,*self.aliases) if x}
def normalize_entity(name,aliases=()):return NormalizedEntity(name,canonical_name(name),tuple(aliases))
def name_strength(left:NormalizedEntity,right:NormalizedEntity)->tuple[float,tuple[str,...]]:
    if left.keys&right.keys:return 1.0,("canonical_or_alias_exact",)
    lt=set(tokens(" ".join((left.original_name,*left.aliases))));rt=set(tokens(" ".join((right.original_name,*right.aliases))))
    overlap=len(lt&rt)/max(1,len(lt|rt))
    if overlap>=.67:return .72,("strong_token_overlap",)
    if overlap>=.34:return .45,("partial_token_overlap",)
    return 0.0,()
