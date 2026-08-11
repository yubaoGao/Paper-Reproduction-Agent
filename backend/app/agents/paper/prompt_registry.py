"""Strict loader for versioned prompt resources."""
from pathlib import Path
from .schemas import PromptSpec

class PromptRegistry:
    _names=("context_classification","stage_extraction","figure_observation","catalog_review","repair","goal_resolution")
    def __init__(self): self.root=Path(__file__).with_name("prompts")
    def get(self,name:str,version:str="v1")->PromptSpec:
        if name not in self._names or version!="v1": raise KeyError(f"unknown prompt: {name}.{version}")
        text=(self.root/f"{name}.{version}.txt").read_text(encoding="utf-8")
        header,body=text.split("SYSTEM:\n",1); system,task=body.split("TASK:\n",1)
        values=dict(line.split(": ",1) for line in header.strip().splitlines())
        return PromptSpec(name=values["NAME"],version=values["VERSION"],system=system.strip(),task=task.strip())
