from pathlib import Path
from .schemas import PromptSpec

class PlannerPromptRegistry:
    _names=("semantic_selection","repair","plan_review")
    def __init__(self): self.root=Path(__file__).with_name("prompts")
    def get(self,name,version="v1"):
        if name not in self._names or version!="v1": raise KeyError(f"unknown planner prompt: {name}.{version}")
        raw=(self.root/f"{name}.{version}.txt").read_text(encoding="utf-8")
        header,body=raw.split("SYSTEM:\n",1); system,task=body.split("TASK:\n",1)
        values=dict(x.split(": ",1) for x in header.strip().splitlines())
        return PromptSpec(name=values["NAME"],version=values["VERSION"],system=system.strip(),task=task.strip())

