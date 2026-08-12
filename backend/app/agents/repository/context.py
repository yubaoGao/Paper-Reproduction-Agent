"""Hierarchical repository-map, symbol and bounded source context selection."""
from __future__ import annotations
import json,re
from backend.app.llm import LLMRole
from .schemas import FileClassification,RepositoryAnalysisContext,RepositoryContextItem

class RepositoryContextBuilder:
    KEYWORDS=("train","eval","test","dataset","model","loss","metric","config","checkpoint","best","early_stop","seed","aggregate","result","ablation","experiment","readme","requirements","environment")
    def __init__(self,router,prompts,*,max_files=48,max_chars_per_file=6000,classify_threshold=80): self.router=router;self.prompts=prompts;self.max_files=max_files;self.max_chars_per_file=max_chars_per_file;self.classify_threshold=classify_threshold
    def build(self,snapshot,static,paper_catalog=None,reproduction_specification=None):
        scores={f.path:self._score(f.path,f.file_type.value) for f in snapshot.files if f.analysis_eligible and f.is_text}
        target_terms=self._target_terms(paper_catalog,reproduction_specification)
        scores={path:score+sum(2 for term in target_terms if term in path.casefold()) for path,score in scores.items()}
        metadata=[]
        if len(scores)>self.classify_threshold:
            prompt=self.prompts.get("context_classification")
            payload=json.dumps({"files":[{"path":p,"score":s} for p,s in sorted(scores.items())]},ensure_ascii=False)
            try:
                response=self.router.for_role(LLMRole.FAST).generate_structured(role=LLMRole.FAST,system_prompt=prompt.system,content=f"{prompt.task}\nUNTRUSTED FILE MAP:\n{payload}",output_schema=FileClassification,prompt_name=prompt.name,prompt_version=prompt.version);metadata.append(response.metadata)
                decisions={x.path:x.relevant for x in response.value.decisions};scores={p:s+(20 if decisions.get(p) else 0) for p,s in scores.items()}
            except Exception:pass
        chosen=tuple(p for p,_ in sorted(scores.items(),key=lambda x:(-x[1],x[0]))[:self.max_files]);items=[]
        records={x.path:x for x in snapshot.files};symbol_map={p:[] for p in chosen};config_map={p:[] for p in chosen};dependency_map={p:[] for p in chosen}
        for symbol in static.code_index.symbols:
            if symbol.path in symbol_map:symbol_map[symbol.path].append(symbol.qualified_name)
        for config in static.configurations:
            if config.path in config_map:config_map[config.path].append(config.key_path)
        for dependency in static.dependencies:
            if dependency.source_path in dependency_map:dependency_map[dependency.source_path].append(dependency.name)
        repository_map=[{"path":p,"language":records[p].language,"role":records[p].file_type.value,"symbols":symbol_map[p],"config_keys":config_map[p],"dependencies":dependency_map[p]} for p in chosen]
        items.append(RepositoryContextItem(locator="repository-map",kind="map",text=json.dumps(repository_map,ensure_ascii=False),score=100))
        symbols=[x for x in static.code_index.symbols if x.path in chosen]
        if symbols:items.append(RepositoryContextItem(locator="code-index",kind="symbols",text=json.dumps([x.model_dump(mode="json") for x in symbols],ensure_ascii=False),score=90))
        root=__import__("pathlib").Path(snapshot.root)
        for path in chosen:
            source=(root/path).read_text(encoding="utf-8",errors="replace");path_symbols=[x for x in symbols if x.path==path]
            if path_symbols:
                lines=source.splitlines();chunks=[]
                for symbol in path_symbols:
                    start=max(0,symbol.start_line-3);end=min(len(lines),symbol.end_line+2);chunks.append(f"# {symbol.qualified_name} L{start+1}-L{end}\n"+"\n".join(lines[start:end]))
                text="\n\n".join(chunks)[:self.max_chars_per_file]
            else:text=source[:self.max_chars_per_file]
            items.append(RepositoryContextItem(locator=f"file:{path}",kind="source",text=text,score=scores[path]))
        return RepositoryAnalysisContext(items=tuple(items),selected_files=chosen,selected_symbols=tuple(x.symbol_id for x in symbols),llm_metadata=tuple(metadata))
    def _score(self,path,kind):
        value=path.casefold();return (15 if kind in {"manifest","config","documentation","script"} else 0)+sum(4 for x in self.KEYWORDS if x in value)
    @staticmethod
    def _target_terms(*targets):
        text=" ".join(x.model_dump_json() for x in targets if x is not None).casefold()
        return {x for x in re.findall(r"[a-z][a-z0-9_-]{2,}",text) if x not in {"null","true","false","status","evidence","confidence"}}
