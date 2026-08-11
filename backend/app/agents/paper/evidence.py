"""Deterministic locator and evidence-text validation."""
from __future__ import annotations
import re
from backend.app.domain import EvidenceReference, PaperDocument
from backend.app.domain import EvidenceSourceType,InformationStatus

class EvidenceValidationError(ValueError): pass

class EvidenceValidator:
    _block=re.compile(r"^page:(\d+)/block:(.+)$")
    _table=re.compile(r"^table:([^/]+)(?:/row:([^/]+))?(?:/column:(.+))?$")
    def validate(self,evidence:EvidenceReference,document:PaperDocument)->None:
        if evidence.source_id not in {None,document.document_id,document.paper.id}: raise EvidenceValidationError("evidence source_id does not identify this document")
        if not evidence.locator: raise EvidenceValidationError("paper evidence requires a stable locator")
        content=self.resolve(evidence.locator,document)
        if evidence.text and not self._text_matches(evidence.text,content): raise EvidenceValidationError(f"evidence text does not match {evidence.locator}")
    def resolve(self,locator:str,document:PaperDocument)->str:
        if locator.startswith("page:") and "/block:" not in locator:
            try: return document.pages[int(locator[5:])-1].text
            except (ValueError,IndexError): raise EvidenceValidationError(f"invalid page locator: {locator}") from None
        match=self._block.fullmatch(locator)
        if match:
            page_no=int(match.group(1)); block_id=match.group(2)
            if not 1<=page_no<=document.page_count: raise EvidenceValidationError(f"invalid page locator: {locator}")
            for block in document.pages[page_no-1].content_blocks:
                if block.block_id==block_id: return block.text
            raise EvidenceValidationError(f"invalid block locator: {locator}")
        if locator.startswith("section:"):
            key=locator[8:]
            for section in document.sections:
                if section.section_id==key: return f"{section.title}\n{section.text}"
            raise EvidenceValidationError(f"invalid section locator: {locator}")
        match=self._table.fullmatch(locator)
        if match:
            table_id,row_name,column=match.groups(); table=next((x for x in document.tables if x.table_id==table_id),None)
            if not table: raise EvidenceValidationError(f"invalid table locator: {locator}")
            if row_name is None:
                structured=""
                if table.structured_data:
                    structured="\n".join((" | ".join(table.structured_data.headers),*(" | ".join(row) for row in table.structured_data.rows)))
                return f"{table.caption}\n{table.raw_text}\n{structured}"
            data=table.structured_data
            if not data: raise EvidenceValidationError("table row locator requires structured data")
            if column and column not in data.headers: raise EvidenceValidationError(f"unknown table column: {column}")
            row=next((x for x in data.rows if x and self._norm(x[0])==self._norm(row_name)),None)
            if row is None: raise EvidenceValidationError(f"unknown table row: {row_name}")
            if column: return row[data.headers.index(column)]
            return " | ".join(row)
        if locator.startswith("figure:"):
            key=locator[7:]; figure=next((x for x in document.figures if x.figure_id==key),None)
            if figure: return figure.caption
            raise EvidenceValidationError(f"invalid figure locator: {locator}")
        raise EvidenceValidationError(f"unsupported evidence locator: {locator}")
    def validate_all(self,evidence_items,document):
        for evidence in evidence_items: self.validate(evidence,document)
    def validate_claim(self,claim,document):
        if any(item.source_type is not EvidenceSourceType.PAPER for item in claim.evidence): raise EvidenceValidationError("paper claims require PAPER evidence")
        values=[]
        for evidence in claim.evidence:
            self.validate(evidence,document); content=self.resolve(evidence.locator,document)
            values.extend(float(x) for x in re.findall(r"(?<![\w.])[-+]?\d+(?:\.\d+)?",content))
        if not any(abs(value-claim.value)<=1e-8 or abs(value/100-claim.value)<=1e-8 for value in values):
            raise EvidenceValidationError(f"claim value {claim.value} is not present at its evidence")
    def validate_parameter(self,parameter,document):
        self.validate_all(parameter.evidence,document)
        if parameter.status is not InformationStatus.EXPLICIT or parameter.value is None: return
        content=" ".join(self.resolve(evidence.locator,document) for evidence in parameter.evidence)
        expected=str(parameter.value).casefold()
        numeric_match=False
        if isinstance(parameter.value,(int,float)) and not isinstance(parameter.value,bool):
            numbers=[float(x) for x in re.findall(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?",content.casefold())]
            numeric_match=any(abs(value-float(parameter.value))<=max(1e-12,abs(float(parameter.value))*1e-8) for value in numbers)
        if expected not in content.casefold() and not numeric_match: raise EvidenceValidationError(f"explicit parameter {parameter.name} value is not present at its evidence")
    @classmethod
    def _text_matches(cls,needle:str,haystack:str)->bool:
        left,right=cls._norm(needle),cls._norm(haystack)
        if left in right: return True
        tokens=set(left.split()); target=set(right.split())
        return bool(tokens) and len(tokens&target)/len(tokens)>=0.6
    @staticmethod
    def _norm(value:str)->str: return " ".join(re.findall(r"[\w.+%-]+",value.casefold()))
