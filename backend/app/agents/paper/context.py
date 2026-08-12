"""Cost-bounded context selection over the stable PaperDocument IR."""
from __future__ import annotations
import json, re
from backend.app.domain import ContentBlockType, PaperDocument
from backend.app.llm import LLMRole, LLMRouter
from .prompt_registry import PromptRegistry
from .schemas import ContextClassificationResult, ContextItem, ExtractionContext

_KEYWORDS=(
    "experiment","experimental","evaluation","result","implementation","training","dataset","baseline","ablation","checkpoint","early stopping","best epoch","seed","mean","standard deviation",
    "robust","sensitivity","efficiency","accuracy","precision","recall","f1","auc","性能","实验","结果","训练",
    "数据集","消融","鲁棒","敏感性","效率","评估","实现细节",
)
_FIGURE_TERMS=("curve","plot","sensitivity","training","performance","ablation","qualitative","曲线","敏感","性能","消融")

class ContextBuilder:
    def __init__(self,router:LLMRouter|None=None,*,classification_threshold:int=18,max_items:int=40,prompts:PromptRegistry|None=None):
        self.router=router; self.classification_threshold=classification_threshold; self.max_items=max_items; self.prompts=prompts or PromptRegistry()
    def build(self,document:PaperDocument)->ExtractionContext:
        candidates:list[ContextItem]=[]
        selected_sections=[]; selected_tables=[]; selected_figures=[]
        for section in document.sections:
            text=f"{section.title}\n{section.text}".strip(); score=self._score(text)+3
            if score>3:
                locator=document.section_locator(section.section_id); candidates.append(ContextItem(locator=locator,kind="section",text=text,score=score)); selected_sections.append(section.section_id)
        for table in document.tables:
            text=f"{table.caption}\n{table.raw_text}".strip(); score=self._score(text)+4
            locator=document.table_locator(table.table_id); candidates.append(ContextItem(locator=locator,kind="table",text=text,score=score)); selected_tables.append(table.table_id)
        for page in document.pages:
            for block in page.content_blocks:
                if block.block_type in {ContentBlockType.HEADING,ContentBlockType.TABLE,ContentBlockType.FIGURE}: continue
                score=self._score(block.text)
                if score:
                    candidates.append(ContextItem(locator=document.block_locator(block.block_id),kind="block",text=block.text,score=score))
        for figure in document.figures:
            score=self._score(figure.caption)
            if any(term in figure.caption.casefold() for term in _FIGURE_TERMS): score+=3
            if score>=3 and figure.image_reference:
                candidates.append(ContextItem(locator=document.figure_locator(figure.figure_id),kind="figure",text=figure.caption,score=score)); selected_figures.append(figure.figure_id)
        candidates=self._deduplicate(candidates)
        metadata=[]
        if len(candidates)>self.classification_threshold and self.router:
            prompt=self.prompts.get("context_classification")
            payload=json.dumps([x.model_dump() for x in candidates],ensure_ascii=False)
            result=self.router.for_role(LLMRole.FAST).generate_structured(role=LLMRole.FAST,system_prompt=prompt.system,content=f"{prompt.task}\nUNTRUSTED CONTENT:\n{payload}",output_schema=ContextClassificationResult,prompt_name=prompt.name,prompt_version=prompt.version)
            metadata.append(result.metadata)
            known={x.locator for x in candidates}; returned={x.locator for x in result.value.decisions}
            if returned==known:
                allowed={x.locator for x in result.value.decisions if x.relevant}
                candidates=[x for x in candidates if x.locator in allowed]
        candidates=sorted(candidates,key=lambda x:(-x.score,x.locator))[:self.max_items]
        selected={x.locator for x in candidates}
        return ExtractionContext(document_id=document.document_id,items=tuple(candidates),selected_sections=tuple(x for x in selected_sections if f"section:{x}" in selected),selected_tables=tuple(x for x in selected_tables if f"table:{x}" in selected),selected_figures=tuple(x for x in selected_figures if f"figure:{x}" in selected),llm_metadata=tuple(metadata))
    @staticmethod
    def _score(text:str)->int:
        lowered=text.casefold(); return sum(1 for keyword in _KEYWORDS if keyword in lowered)
    @staticmethod
    def _deduplicate(items:list[ContextItem])->list[ContextItem]:
        best={}
        for item in items:
            current=best.get(item.locator)
            if current is None or item.score>current.score: best[item.locator]=item
        return list(best.values())

class DeterministicTableExtractor:
    """Transcribe numeric cells; leave experiment semantics to PRIMARY."""
    _number=re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*(%)?\s*$")
    def extract(self,document:PaperDocument):
        from .schemas import TableFact
        facts=[]
        for table in document.tables:
            data=table.structured_data
            if not data or not data.headers: continue
            for row_index,row in enumerate(data.rows):
                if not row: continue
                label=row[0].strip() or f"row-{row_index+1}"
                for column,value in zip(data.headers[1:],row[1:]):
                    match=self._number.fullmatch(value)
                    if not match: continue
                    numeric=float(match.group(1)); numeric=numeric/100 if match.group(2) else numeric
                    facts.append(TableFact(table_id=table.table_id,row_label=label,metric=column.strip(),value=numeric,raw_value=value,locator=document.table_locator(table.table_id,row=label,column=column.strip())))
        return tuple(facts)
