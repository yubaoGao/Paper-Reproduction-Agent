"""Deterministic static analysis. Never imports or executes repository code."""
from __future__ import annotations
import ast,configparser,hashlib,json,re,shlex,tomllib,unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import yaml
from backend.app.domain import *
from backend.app.services import RepositoryStaticAnalysisError

def repo_evidence(snapshot,locator,text=None,confidence=1.0):
    return EvidenceReference(source_type=EvidenceSourceType.REPOSITORY,source_id=snapshot.snapshot_id,locator=locator,text=text,confidence=confidence)
def line_locator(path,start,end=None): return f"file:{path}#L{start}"+(f"-L{end}" if end and end!=start else "")
def symbol_locator(path,name): return f"symbol:{path}::{name}"

@dataclass(frozen=True)
class StaticRepositoryAnalysis:
    code_index:CodeIndex; configurations:tuple[RepositoryConfigRecord,...]; dependencies:tuple[DependencyRecord,...]
    entrypoints:tuple[EntrypointCandidate,...]; commands:tuple[RepositoryCommand,...]
    datasets:tuple[RepositoryComponentRecord,...]; models:tuple[RepositoryComponentRecord,...]
    ablations:tuple[RepositoryComponentRecord,...]; metrics:tuple[RepositoryComponentRecord,...]
    checkpoints:tuple[RepositoryComponentRecord,...]; artifacts:tuple[RepositoryComponentRecord,...]
    conflicts:tuple[RepositoryConflict,...]; documentation:tuple[RepositoryComponentRecord,...]
    environment_definitions:tuple[RepositoryComponentRecord,...]; warnings:tuple[str,...]

class SnapshotReader:
    def __init__(self,snapshot): self.snapshot=snapshot; self.root=Path(snapshot.root).resolve(); self.files={x.path:x for x in snapshot.files}
    def text(self,path):
        record=self.files.get(path)
        if not record or not record.is_text or not record.analysis_eligible: raise RepositoryStaticAnalysisError(f"file is not eligible for static text analysis: {path}")
        target=(self.root/path).resolve()
        if self.root not in target.parents: raise RepositoryStaticAnalysisError("repository path escaped snapshot root")
        return target.read_text(encoding="utf-8",errors="replace")

class PythonAstAnalyzer:
    def analyze(self,snapshot,reader,path):
        source=reader.text(path)
        try: tree=ast.parse(source,filename=path)
        except SyntaxError as exc: return (),(),(),(f"Python parse failed for {path}: {exc}",)
        symbols=[]; imports=[]; cli=[]; decorators={}
        class Visitor(ast.NodeVisitor):
            stack=[]
            def visit_Import(self,node): imports.extend(alias.name for alias in node.names)
            def visit_ImportFrom(self,node): imports.append(node.module or "")
            def _symbol(self,node,kind):
                name=node.name; qualified="::".join((*self.stack,name)); dec=tuple(ast.unparse(x) for x in getattr(node,"decorator_list",()))
                calls=tuple(dict.fromkeys(ast.unparse(x.func) for x in ast.walk(node) if isinstance(x,ast.Call)))
                symbols.append(CodeSymbol(symbol_id=f"{path}::{qualified}",path=path,name=name,qualified_name=qualified,kind=kind,language="Python",start_line=node.lineno,end_line=getattr(node,"end_lineno",node.lineno),references=calls,decorators=dec))
                for decorator in getattr(node,"decorator_list",()):
                    if isinstance(decorator,ast.Call) and ast.unparse(decorator.func).endswith(("click.option",".option")) and decorator.args:
                        argument=self._literal(decorator.args[0]);kwargs={x.arg:self._literal(x.value) for x in decorator.keywords if x.arg}
                        cli.append(CliArgument(name=str(argument),value_type=str(kwargs.get("type")) if kwargs.get("type") else None,default=kwargs.get("default"),required=bool(kwargs.get("required",False)),source=line_locator(path,decorator.lineno,getattr(decorator,"end_lineno",decorator.lineno))))
                defaults=getattr(getattr(node,"args",None),"defaults",())
                arguments=getattr(getattr(node,"args",None),"args",())
                for argument,default in (zip(arguments[-len(defaults):],defaults) if defaults else ()):
                    if isinstance(default,ast.Call) and ast.unparse(default.func).endswith(("typer.Option","typer.Argument")):
                        kwargs={x.arg:self._literal(x.value) for x in default.keywords if x.arg};cli.append(CliArgument(name=f"--{argument.arg.replace('_','-')}",default=kwargs.get("default"),required=False,source=line_locator(path,default.lineno,getattr(default,"end_lineno",default.lineno))))
                self.stack.append(name); self.generic_visit(node); self.stack.pop()
            def visit_ClassDef(self,node): self._symbol(node,SymbolKind.CLASS)
            def visit_FunctionDef(self,node): self._symbol(node,SymbolKind.METHOD if self.stack else SymbolKind.FUNCTION)
            visit_AsyncFunctionDef=visit_FunctionDef
            def visit_Call(self,node):
                name=ast.unparse(node.func)
                if name.endswith(".add_argument") and node.args:
                    arg=self._literal(node.args[0]); kwargs={x.arg:self._literal(x.value) for x in node.keywords if x.arg}
                    choices=kwargs.get("choices") if isinstance(kwargs.get("choices"),list) else []
                    cli.append(CliArgument(name=str(arg),value_type=str(kwargs.get("type")) if kwargs.get("type") is not None else None,default=kwargs.get("default"),required=bool(kwargs.get("required",False)),choices=tuple(choices),source=line_locator(path,node.lineno,getattr(node,"end_lineno",node.lineno))))
                self.generic_visit(node)
            @staticmethod
            def _literal(node):
                try:return ast.literal_eval(node)
                except Exception:
                    try:return ast.unparse(node)
                    except Exception:return None
        Visitor().visit(tree)
        line_count=max(1,len(source.splitlines()));symbols.insert(0,CodeSymbol(symbol_id=f"{path}::<module>",path=path,name=Path(path).stem,qualified_name="<module>",kind=SymbolKind.MODULE,language="Python",start_line=1,end_line=line_count,references=tuple(dict.fromkeys(imports))))
        for node in tree.body:
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                target=node.targets[0] if isinstance(node,ast.Assign) else node.target
                if isinstance(target,ast.Name):symbols.append(CodeSymbol(symbol_id=f"{path}::{target.id}",path=path,name=target.id,qualified_name=target.id,kind=SymbolKind.GLOBAL,language="Python",start_line=node.lineno,end_line=getattr(node,"end_lineno",node.lineno)))
        main=False
        for node in ast.walk(tree):
            if isinstance(node,ast.If):
                try:
                    if ast.unparse(node.test).replace('"',"'")=="__name__ == '__main__'": main=True
                except Exception: pass
        hydra=[]
        for symbol in symbols:
            for decorator in symbol.decorators:
                if "hydra.main" in decorator or "click.command" in decorator or "typer" in decorator.casefold(): hydra.append(decorator)
        entry=()
        if main or hydra:
            kind=self._entry_type(path,source); evidence=repo_evidence(snapshot,line_locator(path,1,min(len(source.splitlines()),200)),"__main__" if main else hydra[0])
            config_paths=[]
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and "hydra.main" in ast.unparse(node.func):
                    values={x.arg:Visitor._literal(x.value) for x in node.keywords if x.arg};directory=values.get("config_path");name=values.get("config_name")
                    if isinstance(directory,str) and isinstance(name,str):
                        for suffix in (".yaml",".yml"):
                            candidate=f"{directory.strip('./')}/{name}{suffix}"
                            if candidate in {x.path for x in snapshot.files}:config_paths.append(candidate)
            entry=(EntrypointCandidate(entrypoint_id=f"entry:{path}",entrypoint_type=kind,path=path,symbol_id=next((x.symbol_id for x in symbols if x.name in {"main","train","evaluate"}),None),interpreter="python",arguments=tuple(cli),config_paths=tuple(config_paths),confidence=.9 if main else .8,evidence=(evidence,)),)
        return tuple(symbols),tuple(dict.fromkeys(imports)),entry,()
    @staticmethod
    def _entry_type(path,source):
        value=(path+" "+source[:2000]).casefold()
        if "train" in value:return EntrypointType.TRAINING
        if "eval" in value or "test" in value:return EntrypointType.EVALUATION
        if "infer" in value or "predict" in value:return EntrypointType.INFERENCE
        if "preprocess" in value:return EntrypointType.PREPROCESSING
        return EntrypointType.GENERIC

class TreeSitterStructuralAnalyzer:
    supported={"Java":"tree_sitter_java","Go":"tree_sitter_go","C":"tree_sitter_c","C++":"tree_sitter_cpp","JavaScript":"tree_sitter_javascript","TypeScript":"tree_sitter_typescript"}
    node_kinds={"function_declaration":SymbolKind.FUNCTION,"function_definition":SymbolKind.FUNCTION,"method_declaration":SymbolKind.METHOD,"method_definition":SymbolKind.METHOD,"class_declaration":SymbolKind.CLASS,"class_specifier":SymbolKind.CLASS,"struct_specifier":SymbolKind.CLASS,"type_declaration":SymbolKind.CLASS}
    def analyze(self,snapshot,reader,path,language):
        try:
            import importlib
            from tree_sitter import Language,Parser
            module=importlib.import_module(self.supported[language]); grammar="tsx" if language=="TypeScript" and hasattr(module,"language_tsx") else "language"
            parser=Parser(Language(getattr(module,grammar)())); source=reader.text(path).encode(); tree=parser.parse(source)
        except Exception as exc: return (),(f"Tree-sitter parse unavailable for {path}: {exc}",)
        symbols=[]
        def walk(node,parent=""):
            kind=self.node_kinds.get(node.type)
            next_parent=parent
            if kind:
                name_node=node.child_by_field_name("name"); name=source[name_node.start_byte:name_node.end_byte].decode(errors="replace") if name_node else f"anonymous@{node.start_point.row+1}"
                qualified=f"{parent}::{name}" if parent else name; symbols.append(CodeSymbol(symbol_id=f"{path}::{qualified}",path=path,name=name,qualified_name=qualified,kind=kind,language=language,start_line=node.start_point.row+1,end_line=node.end_point.row+1))
                if kind is SymbolKind.CLASS: next_parent=qualified
            for child in node.children: walk(child,next_parent)
        walk(tree.root_node)
        return tuple(symbols),()

class ConfigDependencyAnalyzer:
    def analyze(self,snapshot,reader):
        configs=[]; dependencies=[]; warnings=[]
        for record in snapshot.files:
            if not record.analysis_eligible:continue
            path=record.path; name=Path(path).name.casefold()
            try:text=reader.text(path)
            except RepositoryStaticAnalysisError:continue
            try:
                if record.file_type is RepositoryFileType.CONFIG:
                    data=self._parse_config(path,text)
                    for key,value in self._flatten(data): configs.append(RepositoryConfigRecord(config_id=f"config:{path}::{key}",path=path,key_path=key,value=value,source=record.language or "config",evidence=(repo_evidence(snapshot,f"config:{path}::{key}",str(value)),)))
                elif record.language=="Python" and ("config" in name or "setting" in name):
                    configs.extend(self._python_config(snapshot,path,text))
                if record.language=="Python":configs.extend(self._environment_config(snapshot,path,text))
                if name.startswith("requirements") and name.endswith(".txt"): dependencies.extend(self._requirements(snapshot,path,text))
                elif name=="pyproject.toml": dependencies.extend(self._pyproject(snapshot,path,text))
                elif name in {"environment.yml","environment.yaml"}: dependencies.extend(self._environment(snapshot,path,text))
                elif name=="setup.cfg": dependencies.extend(self._setup_cfg(snapshot,path,text))
                elif name=="setup.py": dependencies.extend(self._setup_py(snapshot,path,text))
                elif name=="dockerfile": dependencies.extend(self._docker(snapshot,path,text))
            except Exception as exc:warnings.append(f"manifest/config parse failed for {path}: {exc}")
        return tuple(configs),tuple(dependencies),tuple(warnings)
    def _python_config(self,snapshot,path,text):
        tree=ast.parse(text);result=[]
        for node in tree.body:
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                target=node.targets[0] if isinstance(node,ast.Assign) else node.target
                if isinstance(target,ast.Name):
                    try:value=ast.literal_eval(node.value)
                    except Exception:continue
                    result.append(RepositoryConfigRecord(config_id=f"config:{path}::{target.id}",path=path,key_path=target.id,value=value,source="Python AST",evidence=(repo_evidence(snapshot,line_locator(path,node.lineno,getattr(node,"end_lineno",node.lineno)),ast.get_source_segment(text,node)),)))
        return result
    def _environment_config(self,snapshot,path,text):
        tree=ast.parse(text);result=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and ast.unparse(node.func) in {"os.getenv","os.environ.get"} and node.args:
                try:name=ast.literal_eval(node.args[0])
                except Exception:continue
                if not isinstance(name,str):continue
                try:value=ast.literal_eval(node.args[1]) if len(node.args)>1 else None
                except Exception:value=None
                result.append(RepositoryConfigRecord(config_id=f"config:{path}::env.{name}",path=path,key_path=f"env.{name}",value=value,source="environment",dynamic_override=True,evidence=(repo_evidence(snapshot,line_locator(path,node.lineno,getattr(node,"end_lineno",node.lineno)),ast.get_source_segment(text,node)),)))
        return result
    @staticmethod
    def _parse_config(path,text):
        suffix=Path(path).suffix.casefold()
        if suffix==".json":return json.loads(text)
        if suffix==".toml":return tomllib.loads(text)
        if suffix in {".yml",".yaml"}:return yaml.safe_load(text) or {}
        parser=configparser.ConfigParser(interpolation=None);parser.read_string(text);return {section:dict(parser.items(section)) for section in parser.sections()}
    @classmethod
    def _flatten(cls,value,prefix=""):
        if isinstance(value,dict):
            for key,item in value.items():yield from cls._flatten(item,f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(value,list):yield prefix,value
        else:yield prefix,value
    @staticmethod
    def _dep(snapshot,path,name,spec=None,ecosystem="python",optional=False):
        clean=name.strip();return DependencyRecord(dependency_id=f"dep:{path}::{clean.casefold()}",name=clean,version_spec=spec,ecosystem=ecosystem,optional=optional,source_path=path,evidence=(repo_evidence(snapshot,f"manifest:{path}::{clean}",clean),))
    def _requirements(self,snapshot,path,text):
        result=[]
        for line in text.splitlines():
            line=line.split("#",1)[0].strip()
            if not line or line.startswith(("-r","--")):continue
            match=re.match(r"([A-Za-z0-9_.-]+)(.*)",line)
            if match:result.append(self._dep(snapshot,path,match.group(1),match.group(2).strip() or None))
        return result
    def _pyproject(self,snapshot,path,text):
        data=tomllib.loads(text);values=[]
        for item in data.get("project",{}).get("dependencies",[]):values.extend(self._requirements(snapshot,path,item))
        for group,items in data.get("project",{}).get("optional-dependencies",{}).items():
            for item in items:
                for dep in self._requirements(snapshot,path,item):values.append(dep.model_copy(update={"optional":True}))
        poetry=data.get("tool",{}).get("poetry",{}).get("dependencies",{})
        for name,spec in poetry.items():
            if name.casefold()!="python":values.append(self._dep(snapshot,path,name,str(spec)))
        return values
    def _environment(self,snapshot,path,text):
        data=yaml.safe_load(text) or {};result=[]
        for item in data.get("dependencies",[]):
            if isinstance(item,str):
                match=re.match(r"([^=<>!]+)(.*)",item);result.append(self._dep(snapshot,path,match.group(1),match.group(2) or None,"conda"))
            elif isinstance(item,dict):
                for pip in item.get("pip",[]):result.extend(self._requirements(snapshot,path,pip))
        return result
    def _setup_cfg(self,snapshot,path,text):
        parser=configparser.ConfigParser(interpolation=None);parser.read_string(text);return self._requirements(snapshot,path,parser.get("options","install_requires",fallback=""))
    def _setup_py(self,snapshot,path,text):
        tree=ast.parse(text);result=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Call):
                for keyword in node.keywords:
                    if keyword.arg=="install_requires":
                        try:
                            for item in ast.literal_eval(keyword.value):result.extend(self._requirements(snapshot,path,item))
                        except Exception:pass
        return result
    def _docker(self,snapshot,path,text):
        result=[]
        for line in text.splitlines():
            if re.match(r"\s*FROM\s+",line,re.I):
                image=re.split(r"\s+",line.strip(),maxsplit=1)[1];result.append(self._dep(snapshot,path,image,None,"container"))
            if "apt-get install" in line:
                tail=line.split("apt-get install",1)[1]
                for name in re.findall(r"\b[a-z][a-z0-9+.-]+\b",tail):
                    if name not in {"and","rm","var","lib","apt","lists"}:result.append(self._dep(snapshot,path,name,None,"system"))
        return result

class RepositoryStaticAnalyzer:
    def __init__(self): self.python=PythonAstAnalyzer();self.tree=TreeSitterStructuralAnalyzer();self.configs=ConfigDependencyAnalyzer()
    def analyze(self,snapshot,*,metric_names=()):
        reader=SnapshotReader(snapshot);symbols=[];imports={};entrypoints=[];warnings=[]
        for record in snapshot.files:
            if not record.analysis_eligible:continue
            if record.language=="Python":
                found,imps,entries,problems=self.python.analyze(snapshot,reader,record.path);symbols.extend(found);imports[record.path]=imps;entrypoints.extend(entries);warnings.extend(problems)
            elif record.language in self.tree.supported:
                found,problems=self.tree.analyze(snapshot,reader,record.path,record.language);symbols.extend(found);warnings.extend(problems)
        configurations,dependencies,problems=self.configs.analyze(snapshot,reader);warnings.extend(problems)
        commands,shell_entries,docs,doc_conflicts=self._commands(snapshot,reader);entrypoints.extend(shell_entries)
        metadata_entries=self._metadata_entrypoints(snapshot,reader);entrypoints.extend(metadata_entries)
        datasets,models,ablations,metrics,checkpoints,artifacts=self._components(snapshot,reader,symbols,configurations,metric_names=metric_names)
        conflicts=(*doc_conflicts,*self._dependency_conflicts(dependencies),*self._entrypoint_conflicts(snapshot,entrypoints),*self._config_cli_conflicts(snapshot,configurations,entrypoints),*self._dataset_name_conflicts(configurations))
        environments=tuple(RepositoryComponentRecord(component_id=f"env:{path}",name=Path(path).name,kind="environment",paths=(path,),evidence=(repo_evidence(snapshot,f"file:{path}"),)) for path in snapshot.manifests)
        return StaticRepositoryAnalysis(CodeIndex(symbols=tuple(symbols),imports=imports,parse_warnings=tuple(warnings)),configurations,dependencies,tuple(entrypoints),commands,datasets,models,ablations,metrics,checkpoints,artifacts,tuple(conflicts),docs,environments,tuple(warnings))
    def _commands(self,snapshot,reader):
        commands=[];entries=[];docs=[];conflicts=[]
        python_paths={x.path for x in snapshot.files if x.language=="Python"}
        for path in (*snapshot.documentation_files,*(x.path for x in snapshot.files if x.language in {"Shell","Makefile"})):
            try:text=reader.text(path)
            except RepositoryStaticAnalysisError:continue
            kind="documentation" if path in snapshot.documentation_files else "shell_script"
            docs.append(RepositoryComponentRecord(component_id=f"doc:{path}",name=Path(path).name,kind=kind,paths=(path,),evidence=(repo_evidence(snapshot,f"file:{path}"),)))
            for line_no,line in enumerate(text.splitlines(),start=1):
                match=re.search(r"(?:^|\s)(python(?:3)?)\s+([^\s]+\.py)(.*)$",line.strip())
                if not match:continue
                target=match.group(2).lstrip("./").replace("\\","/");args=tuple(shlex.split(match.group(3),posix=True)) if match.group(3).strip() else ()
                evidence=repo_evidence(snapshot,line_locator(path,line_no),line.strip());command_id=f"command:{path}:{line_no}"
                variables=set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)",line));variables.update(re.findall(r"(?:^|\s)([A-Z_][A-Z0-9_]*)=",line))
                commands.append(RepositoryCommand(command_id=command_id,source_path=path,command=line.strip(),entrypoint_path=target if target in python_paths else None,arguments=args,environment_variables=tuple(sorted(variables)),evidence=(evidence,)))
                if target in python_paths:
                    config_paths=tuple(x.lstrip("./") for x in args if x.lstrip("./") in {f.path for f in snapshot.files} and Path(x).suffix.casefold() in {".yaml",".yml",".json",".toml",".ini",".cfg"})
                    entries.append(EntrypointCandidate(entrypoint_id=f"entry:{path}:{line_no}",entrypoint_type=PythonAstAnalyzer._entry_type(target,line),path=target,interpreter=match.group(1),config_paths=config_paths,confidence=.8,evidence=(evidence,)))
                elif path in snapshot.documentation_files:conflicts.append(RepositoryConflict(conflict_id=f"conflict:missing-command:{path}:{line_no}",semantic_key=f"command:{target}",conflict_type=RepositoryConflictType.DOCUMENTATION_CODE,candidates=(RepositoryConflictCandidate(value=line.strip(),evidence=(evidence,)),RepositoryConflictCandidate(value="target file missing",evidence=(repo_evidence(snapshot,f"file:{path}"),)))))
        return tuple(commands),tuple(entries),tuple(docs),tuple(conflicts)
    def _metadata_entrypoints(self,snapshot,reader):
        entries=[];known={x.path for x in snapshot.files}
        for path in (x.path for x in snapshot.files if Path(x.path).name.casefold() in {"pyproject.toml","setup.py","dockerfile"}):
            name=Path(path).name.casefold()
            try:text=reader.text(path)
            except RepositoryStaticAnalysisError:continue
            if name=="pyproject.toml":
                data=tomllib.loads(text)
                for command,target in data.get("project",{}).get("scripts",{}).items():
                    module,_,symbol=str(target).partition(":");candidate=module.replace(".","/")+".py"
                    if candidate in known:entries.append(EntrypointCandidate(entrypoint_id=f"entry:{path}:{command}",entrypoint_type=PythonAstAnalyzer._entry_type(candidate,command),path=candidate,symbol_id=f"{candidate}::{symbol}" if symbol else None,interpreter="python",confidence=.9,evidence=(repo_evidence(snapshot,f"file:{path}",str(target)),)))
            if name=="dockerfile":
                for line_no,line in enumerate(text.splitlines(),1):
                    if re.match(r"\s*(CMD|ENTRYPOINT)\b",line,re.I):
                        found=re.search(r"([^\s\"']+\.py)",line)
                        if found and found.group(1).lstrip("./") in known:
                            target=found.group(1).lstrip("./");entries.append(EntrypointCandidate(entrypoint_id=f"entry:{path}:{line_no}",entrypoint_type=PythonAstAnalyzer._entry_type(target,line),path=target,interpreter="python",confidence=.75,evidence=(repo_evidence(snapshot,line_locator(path,line_no),line.strip()),)))
            if name=="setup.py":
                tree=ast.parse(text)
                for node in ast.walk(tree):
                    if not isinstance(node,ast.Call) or not ast.unparse(node.func).endswith("setup"):continue
                    for keyword in node.keywords:
                        if keyword.arg!="entry_points":continue
                        try:groups=ast.literal_eval(keyword.value)
                        except Exception:continue
                        for item in groups.get("console_scripts",[]) if isinstance(groups,dict) else ():
                            command,_,target=str(item).partition("=");module,_,symbol=target.strip().partition(":");candidate=module.replace(".","/")+".py"
                            if candidate in known:entries.append(EntrypointCandidate(entrypoint_id=f"entry:{path}:{command.strip()}",entrypoint_type=PythonAstAnalyzer._entry_type(candidate,command),path=candidate,symbol_id=f"{candidate}::{symbol}" if symbol else None,interpreter="python",confidence=.9,evidence=(repo_evidence(snapshot,line_locator(path,node.lineno,getattr(node,"end_lineno",node.lineno)),str(item)),)))
        return tuple(entries)
    def _components(self,snapshot,reader,symbols,configs,*,metric_names=()):
        datasets=[];models=[];ablations=[];metrics=[];checkpoints=[];artifacts=[]
        metric_names=tuple(dict.fromkeys(metric_names));source_cache={}
        for symbol in symbols:
            lower=(symbol.name+" "+symbol.path).casefold();evidence=(repo_evidence(snapshot,symbol_locator(symbol.path,symbol.qualified_name)),)
            if symbol.kind not in {SymbolKind.MODULE,SymbolKind.GLOBAL} and any(x in lower for x in ("dataset","dataloader","loader")):datasets.append(RepositoryComponentRecord(component_id=f"dataset:{symbol.symbol_id}",name=symbol.name,kind="dataset_loader",paths=(symbol.path,),symbol_ids=(symbol.symbol_id,),evidence=evidence))
            if symbol.kind is SymbolKind.CLASS and any(x in lower for x in ("model","network","dmsf","classifier","encoder","decoder")):models.append(RepositoryComponentRecord(component_id=f"model:{symbol.symbol_id}",name=symbol.name,kind="model_definition",paths=(symbol.path,),symbol_ids=(symbol.symbol_id,),evidence=evidence))
            if any(x in lower for x in ("loss","criterion")):models.append(RepositoryComponentRecord(component_id=f"loss:{symbol.symbol_id}",name=symbol.name,kind="loss_definition",paths=(symbol.path,),symbol_ids=(symbol.symbol_id,),evidence=evidence))
            if symbol.path not in source_cache:
                try:source_cache[symbol.path]=reader.text(symbol.path)
                except RepositoryStaticAnalysisError:source_cache[symbol.path]=""
            lines=source_cache[symbol.path].splitlines();start=max(0,symbol.start_line-1);end=min(len(lines),symbol.end_line)
            source="\n".join(lines[start:end]);context=(lower+" "+source.casefold())
            compact_context=self._metric_key(context)
            targeted=tuple(name for name in metric_names if self._metric_key(name) and self._metric_key(name) in compact_context)
            monitoring=any(term in lower for term in ("train_loss","training_loss","learning_rate","gpu_memory","memory_usage","vram"))
            common_hint=any(term in lower for term in ("accuracy","f1","bleu","rouge","auc","map"))
            semantic_hint=any(term in lower for term in ("metric","score","measure","quality"))
            evaluation_context=any(term in context for term in ("eval","test","validation","final result","final_result"))
            output_context=any(term in context for term in ("return","report","result","summary","score","metric","output"))
            inferred=not monitoring and (common_hint or semantic_hint or (evaluation_context and output_context))
            for name in targeted:
                digest=hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
                metrics.append(RepositoryComponentRecord(component_id=f"metric:{symbol.symbol_id}:paper:{digest}",name=name,kind="metric_implementation",paths=(symbol.path,),symbol_ids=(symbol.symbol_id,),details={"discovery":"paper_claim_target"},evidence=evidence))
            if inferred and not targeted:
                metrics.append(RepositoryComponentRecord(component_id=f"metric:{symbol.symbol_id}",name=symbol.name,kind="metric_implementation",paths=(symbol.path,),symbol_ids=(symbol.symbol_id,),details={"discovery":"evaluation_output_context"},evidence=evidence))
        for config in configs:
            lower=config.key_path.casefold();evidence=config.evidence
            if any(x in lower for x in ("ablation","use_","disable","lambda_","loss_weight")):ablations.append(RepositoryComponentRecord(component_id=f"ablation:{config.config_id}",name=config.key_path,kind="config_switch",paths=(config.path,),details={"value":config.value},evidence=evidence))
            if any(x in lower for x in ("checkpoint","resume","pretrained","weights")):checkpoints.append(RepositoryComponentRecord(component_id=f"checkpoint:{config.config_id}",name=config.key_path,kind="checkpoint_config",paths=(config.path,),details={"value":config.value},evidence=evidence))
            if any(x in lower for x in ("output","log_dir","save_dir","result")):artifacts.append(RepositoryComponentRecord(component_id=f"artifact:{config.config_id}",name=config.key_path,kind="artifact_path",paths=(config.path,),details={"value":config.value},evidence=evidence))
        return tuple(datasets),tuple(models),tuple(ablations),tuple(metrics),tuple(checkpoints),tuple(artifacts)
    @staticmethod
    def _metric_key(value):
        normalized=unicodedata.normalize("NFKC",value or "").casefold()
        return "".join(character for character in normalized if character.isalnum())
    @staticmethod
    def _dependency_conflicts(dependencies):
        groups={}
        for dep in dependencies:groups.setdefault((dep.ecosystem,dep.name.casefold()),[]).append(dep)
        result=[]
        for index,(key,values) in enumerate(groups.items(),start=1):
            specs={x.version_spec for x in values}
            if len(specs)>1:result.append(RepositoryConflict(conflict_id=f"conflict:dependency:{index}",semantic_key=f"dependency:{key[0]}:{key[1]}",conflict_type=RepositoryConflictType.DEPENDENCY_VERSION,candidates=tuple(RepositoryConflictCandidate(value=x.version_spec,evidence=x.evidence) for x in values)))
        return tuple(result)
    @staticmethod
    def _entrypoint_conflicts(snapshot,entrypoints):
        result=[]
        groups={}
        for item in entrypoints:groups.setdefault(item.entrypoint_type,[]).append(item)
        for kind,items in groups.items():
            paths={x.path for x in items}
            if len(paths)>1:
                unique={}
                for item in items:unique.setdefault(item.path,item)
                result.append(RepositoryConflict(conflict_id=f"conflict:entrypoint:{kind.value}",semantic_key=f"entrypoint:{kind.value}",conflict_type=RepositoryConflictType.ENTRYPOINT,candidates=tuple(RepositoryConflictCandidate(value=x.path,evidence=x.evidence) for x in unique.values())))
        return tuple(result)
    @staticmethod
    def _config_cli_conflicts(snapshot,configs,entrypoints):
        by_name={}
        for config in configs:by_name.setdefault(config.key_path.rsplit(".",1)[-1].replace("-","_").casefold(),[]).append(config)
        result=[]
        for entry in entrypoints:
            for argument in entry.arguments:
                name=argument.name.lstrip("-").replace("-","_").casefold()
                if argument.default is None:continue
                for config in by_name.get(name,()):
                    if config.value!=argument.default:
                        evidence=repo_evidence(snapshot,argument.source,str(argument.default))
                        result.append(RepositoryConflict(conflict_id=f"conflict:config-cli:{entry.entrypoint_id}:{name}:{config.config_id}",semantic_key=f"default:{name}",conflict_type=RepositoryConflictType.CONFIG_CLI,candidates=(RepositoryConflictCandidate(value=argument.default,evidence=(evidence,)),RepositoryConflictCandidate(value=config.value,evidence=config.evidence))))
        return tuple(result)
    @staticmethod
    def _dataset_name_conflicts(configs):
        candidates=[x for x in configs if "dataset" in x.key_path.casefold() and isinstance(x.value,str)]
        values={x.value.casefold() for x in candidates}
        if len(values)<2:return ()
        unique={}
        for item in candidates:unique.setdefault(item.value.casefold(),item)
        return (RepositoryConflict(conflict_id="conflict:dataset-name",semantic_key="dataset:name",conflict_type=RepositoryConflictType.DATASET_NAME,candidates=tuple(RepositoryConflictCandidate(value=x.value,evidence=x.evidence) for x in unique.values())),)
