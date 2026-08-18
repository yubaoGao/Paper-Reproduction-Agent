from pathlib import Path
from .schemas import PromptSpec

class RepositoryPromptRegistry:
    _names=("context_classification","stage_analysis","repair","catalog_review")
    _versions={"context_classification":"v1","stage_analysis":"v2","repair":"v1","catalog_review":"v1"}
    def __init__(self): self.root=Path(__file__).with_name("prompts")
    def get(self,name:str,version:str|None=None):
        version=version or self._versions.get(name)
        if name not in self._names or version not in {"v1","v2"} or version=="v2" and name!="stage_analysis": raise KeyError(f"unknown repository prompt: {name}.{version}")
        raw=(self.root/f"{name}.{version}.txt").read_text(encoding="utf-8")
        header,body=raw.split("SYSTEM:\n",1);system,task=body.split("TASK:\n",1)
        values=dict(line.split(": ",1) for line in header.strip().splitlines())
        return PromptSpec(name=values["NAME"],version=values["VERSION"],system=system.strip(),task=task.strip())
