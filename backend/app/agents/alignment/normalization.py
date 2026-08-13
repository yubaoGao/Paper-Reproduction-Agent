"""Conservative entity normalization used only to generate candidates."""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass

_SEMANTIC={"lr":"learningrate","learning_rate":"learningrate","batchsize":"batchsize","batch_size":"batchsize","wd":"weightdecay","weight_decay":"weightdecay","centre":"center","mvsa_single":"mvsas","mvsa_s":"mvsas"}
def canonical_name(value:str)->str:
    folded=unicodedata.normalize("NFKC",value).casefold()
    raw="".join(character for character in folded if character.isalnum())
    return _SEMANTIC.get(folded.replace("-","_"),_SEMANTIC.get(raw,raw))
def tokens(value:str)->tuple[str,...]:return tuple(x for x in re.findall(r"[^\W_]+",unicodedata.normalize("NFKC",value).casefold(),flags=re.UNICODE) if x not in {"the","model","experiment","config","dataset"})
@dataclass(frozen=True)
class NormalizedEntity:
    original_name:str;canonical_name:str;aliases:tuple[str,...]
    @property
    def keys(self):return {key for x in (self.original_name,*self.aliases) if x for key in (canonical_name(x),) if key}
def normalize_entity(name,aliases=()):return NormalizedEntity(name,canonical_name(name),tuple(aliases))
def name_strength(left:NormalizedEntity,right:NormalizedEntity)->tuple[float,tuple[str,...]]:
    if left.keys&right.keys:return 1.0,("canonical_or_alias_exact",)
    lt=set(tokens(" ".join((left.original_name,*left.aliases))));rt=set(tokens(" ".join((right.original_name,*right.aliases))))
    overlap=len(lt&rt)/max(1,len(lt|rt))
    if overlap>=.67:return .72,("strong_token_overlap",)
    if overlap>=.34:return .45,("partial_token_overlap",)
    return 0.0,()
