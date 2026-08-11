"""Bounded repository snapshot and file classification."""
from __future__ import annotations
import configparser,hashlib,re
from datetime import datetime,timezone
from pathlib import Path
import pathspec
from backend.app.domain import *
from backend.app.services import RepositoryAnalysisSettings,RepositorySnapshotBuilder,ResolvedRepositorySource,RepositoryStaticAnalysisError

class RepositoryIgnorePolicy:
    ignored_dirs={".git","node_modules","venv",".venv","__pycache__","build","dist","checkpoints","outputs","logs","wandb",".idea",".vscode"}
    generated_parts={"vendor","vendors","third_party","generated","migrations"}
    binary_suffixes={".pt",".pth",".ckpt",".onnx",".h5",".pkl",".npy",".npz",".zip",".tar",".gz",".jpg",".jpeg",".png",".gif",".pdf",".so",".dll",".exe"}
    def __init__(self,root:Path,max_file_bytes:int):
        self.root=root; self.max_file_bytes=max_file_bytes; self.spec=None
        ignore=root/".gitignore"
        if ignore.is_file():
            try: self.spec=pathspec.PathSpec.from_lines("gitwildmatch",ignore.read_text(encoding="utf-8",errors="replace").splitlines())
            except Exception: self.spec=None
    def ignored(self,path:Path,size:int)->bool:
        relative=path.relative_to(self.root).as_posix()
        return any(part.casefold() in self.ignored_dirs for part in path.relative_to(self.root).parts) or size>self.max_file_bytes or bool(self.spec and self.spec.match_file(relative))
    def vendor_generated(self,path):
        parts={x.casefold() for x in path.relative_to(self.root).parts}; return bool(parts&self.generated_parts)

class DefaultRepositorySnapshotBuilder(RepositorySnapshotBuilder):
    _lfs=re.compile(rb"^version https://git-lfs.github.com/spec/v1\r?\noid sha256:([0-9a-f]{64})\r?\nsize (\d+)",re.M)
    def __init__(self,settings:RepositoryAnalysisSettings|None=None): self.settings=settings or RepositoryAnalysisSettings()
    def build(self,source:ResolvedRepositorySource)->RepositorySnapshot:
        root=source.root.resolve(); policy=RepositoryIgnorePolicy(root,self.settings.max_file_bytes); files=[]; ignored=0; total=0; lfs=[]
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file(): continue
            resolved=path.resolve()
            if root not in resolved.parents:ignored+=1;continue
            try: size=path.stat().st_size
            except OSError: ignored+=1; continue
            if policy.ignored(path,size): ignored+=1; continue
            total+=size
            if len(files)>=self.settings.max_files or total>self.settings.max_repository_bytes: raise RepositoryStaticAnalysisError("repository exceeds snapshot limits")
            data=path.read_bytes(); relative=path.relative_to(root).as_posix(); match=self._lfs.match(data)
            if match: lfs.append(GitLfsPointer(path=relative,oid=match.group(1).decode(),size=int(match.group(2)),object_type=Path(relative).suffix.lstrip(".") or None))
            text=self._is_text(data); language=self._language(relative); role=self._role(relative,language,text); generated=policy.vendor_generated(path)
            files.append(RepositoryFile(path=relative,file_type=role,language=language,size=size,content_hash=hashlib.sha256(data).hexdigest(),is_text=text,generated=generated,vendor=generated,analysis_eligible=text and not generated and not match))
        submodules=self._submodules(root)
        digest=hashlib.sha256("\n".join(f"{x.path}:{x.content_hash}" for x in files).encode()).hexdigest()
        languages=tuple(sorted({x.language for x in files if x.language}))
        manifests=tuple(x.path for x in files if x.file_type is RepositoryFileType.MANIFEST)
        configs=tuple(x.path for x in files if x.file_type is RepositoryFileType.CONFIG)
        docs=tuple(x.path for x in files if x.file_type is RepositoryFileType.DOCUMENTATION)
        return RepositorySnapshot(snapshot_id=f"{source.repository.repository_id}:{source.resolved_commit_sha}:{digest[:12]}",repository=source.repository,resolved_commit_sha=source.resolved_commit_sha,root=str(root),content_hash=digest,files=tuple(files),languages=languages,manifests=manifests,configs=configs,documentation_files=docs,submodules=submodules,lfs_pointers=tuple(lfs),metadata=RepositorySnapshotMetadata(created_at=datetime.now(timezone.utc),file_count=len(files),total_bytes=total,ignored_files=ignored))
    @staticmethod
    def _is_text(data):
        if b"\0" in data[:8192]: return False
        try: data[:8192].decode("utf-8"); return True
        except UnicodeDecodeError: return False
    @staticmethod
    def _language(path):
        name=Path(path).name.casefold(); suffix=Path(path).suffix.casefold()
        if name=="dockerfile": return "Dockerfile"
        if name in {"makefile","gnumakefile"}: return "Makefile"
        if name.startswith("requirements") and suffix==".txt": return "requirements"
        if name in {"environment.yml","environment.yaml"}: return "Conda"
        return {".py":"Python",".sh":"Shell",".bash":"Shell",".yaml":"YAML",".yml":"YAML",".json":"JSON",".toml":"TOML",".md":"Markdown",".java":"Java",".go":"Go",".c":"C",".h":"C",".cc":"C++",".cpp":"C++",".hpp":"C++",".js":"JavaScript",".jsx":"JavaScript",".ts":"TypeScript",".tsx":"TypeScript",".cfg":"INI",".ini":"INI"}.get(suffix)
    @staticmethod
    def _role(path,language,text):
        name=Path(path).name.casefold()
        if not text:return RepositoryFileType.BINARY
        if language in {"YAML","JSON","TOML","INI"}:return RepositoryFileType.CONFIG
        if language in {"requirements","Conda"} or name in {"setup.py","setup.cfg","pyproject.toml","package.json","go.mod","pom.xml"}:return RepositoryFileType.MANIFEST
        if language=="Markdown" or name.startswith("readme"):return RepositoryFileType.DOCUMENTATION
        if language=="Shell":return RepositoryFileType.SCRIPT
        if language in {"Dockerfile","Makefile"}:return RepositoryFileType.BUILD
        if language=="Dockerfile" or name in {"makefile"}:return RepositoryFileType.BUILD
        if language in {"Python","Java","Go","C","C++","JavaScript","TypeScript"}:return RepositoryFileType.SOURCE
        return RepositoryFileType.OTHER
    @staticmethod
    def _submodules(root):
        path=root/".gitmodules"
        if not path.is_file(): return ()
        parser=configparser.ConfigParser(interpolation=None); parser.read(path,encoding="utf-8")
        return tuple(SubmoduleRecord(path=parser.get(section,"path"),url=parser.get(section,"url"),materialized=False) for section in parser.sections() if parser.has_option(section,"path") and parser.has_option(section,"url"))
