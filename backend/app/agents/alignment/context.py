"""Bounded per-stage alignment context construction."""
from __future__ import annotations
import json
from backend.app.llm import LLMRole
from .schemas import AlignmentContext,AlignmentContextItem,CandidateClassification

class AlignmentContextBuilder:
    def __init__(self,router,prompts,*,max_items=30,max_chars=30000,fast_threshold=40):self.router=router;self.prompts=prompts;self.max_items=max_items;self.max_chars=max_chars;self.fast_threshold=fast_threshold
    def build(self,stage,paper,repository,candidates,deterministic,reproduction_specification=None):
        relevant=[x for x in candidates if self._category(stage,x.category)];metadata=[]
        priorities=set()
        if reproduction_specification is not None:
            target_text=" ".join(" ".join(str(v or "") for v in (x.experiment_name,x.dataset,x.model,x.variant)) for x in reproduction_specification.targets).casefold()
            priorities.update(x.experiment_id for x in paper.experiments if any(v and v.casefold() in target_text for v in (x.name,x.dataset,x.model,x.variant)))
            priorities.update(x.canonical_name for x in (*paper.datasets,*paper.model_variants) if x.canonical_name.casefold() in target_text)
        if len(relevant)>self.fast_threshold:
            prompt=self.prompts.get("candidate_classification")
            response=self.router.for_role(LLMRole.FAST).generate_structured(role=LLMRole.FAST,system_prompt=prompt.system,content=f"{prompt.task}\nSTAGE: {stage}\nUNTRUSTED CANDIDATES:\n"+json.dumps([x.model_dump(mode="json") for x in relevant],ensure_ascii=False),output_schema=CandidateClassification,prompt_name=prompt.name,prompt_version=prompt.version);metadata.append(response.metadata);keep={x.candidate_id for x in response.value.decisions if x.relevant};relevant=[x for x in relevant if x.candidate_id in keep]
        relevant=sorted(relevant,key=lambda x:(x.paper_item_id not in priorities,-x.score,x.candidate_id))[:self.max_items];paper_ids={x.paper_item_id for x in relevant};repo_ids={y for x in relevant for y in x.repository_item_ids};items=[]
        items.append(AlignmentContextItem(locator=f"stage:{stage}:candidates",kind="candidates",text=json.dumps([x.model_dump(mode="json") for x in relevant],ensure_ascii=False),score=100))
        paper_records=[x.model_dump(mode="json") for x in paper.experiments if x.experiment_id in paper_ids]
        if stage=="dataset_model":paper_records.extend(x.model_dump(mode="json") for x in (*paper.datasets,*paper.model_variants) if x.canonical_name in paper_ids)
        repo_records=[x.model_dump(mode="json") for x in (*repository.experiment_implementations,*repository.datasets,*repository.models,*repository.ablation_mechanisms,*repository.metrics) if getattr(x,"implementation_id",getattr(x,"component_id","")) in repo_ids]
        draft=getattr(deterministic,self._draft_field(stage),())
        for locator,kind,value in ((f"stage:{stage}:paper","paper",paper_records),(f"stage:{stage}:repository","repository",repo_records),(f"stage:{stage}:deterministic","draft",[x.model_dump(mode="json") for x in draft])):
            text=json.dumps(value,ensure_ascii=False)
            if text!="[]":items.append(AlignmentContextItem(locator=locator,kind=kind,text=text,score=90))
        bounded=[];used=0
        for item in items:
            remaining=self.max_chars-used
            if remaining<=0:break
            text=item.text[:remaining];bounded.append(item.model_copy(update={"text":text}));used+=len(text)
        return AlignmentContext(items=tuple(bounded),selected_contexts=tuple(x.locator for x in bounded),llm_metadata=tuple(metadata))
    @staticmethod
    def _category(stage,category):return category in {"dataset","model"} if stage=="dataset_model" else category==stage.rstrip("s") or stage in {"parameters","metrics","ablations"} and category==stage[:-1]
    @staticmethod
    def _draft_field(stage):return {"dataset_model":"datasets","experiment":"experiments","association":"experiments","parameters":"parameters","ablations":"ablations","metrics":"metrics","conflicts":"conflicts"}[stage]
