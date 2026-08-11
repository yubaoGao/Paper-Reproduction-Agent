"""Validation for auditable repository evidence locators."""
from __future__ import annotations
import re
from backend.app.domain import EvidenceSourceType,RepositorySnapshot
from backend.app.infrastructure.repository.static_analysis import SnapshotReader

class RepositoryEvidenceValidationError(ValueError): pass

class RepositoryEvidenceValidator:
    LINE=re.compile(r"^file:(.+)#L([1-9]\d*)(?:-L([1-9]\d*))?$")
    def validate_all(self,evidence,snapshot:RepositorySnapshot,static=None):
        for item in evidence:self.validate(item,snapshot,static)
    def validate(self,item,snapshot,static=None):
        if item.source_type is not EvidenceSourceType.REPOSITORY: raise RepositoryEvidenceValidationError("repository facts require repository evidence")
        if item.source_id!=snapshot.snapshot_id: raise RepositoryEvidenceValidationError("evidence snapshot id mismatch")
        locator=item.locator or "";paths={x.path for x in snapshot.files};reader=SnapshotReader(snapshot)
        match=self.LINE.match(locator)
        if match:
            path,start,end=match.group(1),int(match.group(2)),int(match.group(3) or match.group(2));self._path(path,paths)
            lines=reader.text(path).splitlines()
            if end<start or end>len(lines):raise RepositoryEvidenceValidationError(f"invalid evidence lines: {locator}")
            self._text(item.text,"\n".join(lines[start-1:end]));return
        if locator.startswith("file:"):
            path=locator[5:];self._path(path,paths)
            if item.text:self._text(item.text,reader.text(path))
            return
        for prefix,records,identifier in (("symbol:",getattr(static,"code_index",None),"symbol_id"),("config:",getattr(static,"configurations",()),"config_id"),("manifest:",getattr(static,"dependencies",()),"dependency_id")):
            if locator.startswith(prefix):
                if prefix=="symbol:": pool=records.symbols if records else ()
                else:pool=records
                normalized=locator if prefix!="symbol:" else locator[len(prefix):]
                valid={getattr(x,identifier) for x in pool}
                if prefix=="symbol:": valid|={f"{x.path}::{x.qualified_name}" for x in pool}
                if prefix=="manifest:": valid|={f"manifest:{x.source_path}::{x.name}" for x in pool}
                if normalized not in valid and locator not in valid:raise RepositoryEvidenceValidationError(f"unknown evidence locator: {locator}")
                return
        if locator.startswith("script:"):
            self._path(locator[7:],paths);return
        raise RepositoryEvidenceValidationError(f"unsupported evidence locator: {locator}")
    @staticmethod
    def _path(path,paths):
        if path not in paths:raise RepositoryEvidenceValidationError(f"unknown repository path: {path}")
    @staticmethod
    def _text(claim,source):
        if claim and " ".join(claim.split()).casefold() not in " ".join(source.split()).casefold():raise RepositoryEvidenceValidationError("evidence text is not present at locator")
