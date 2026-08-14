"""Parser-independent paper intermediate representation."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from .experiment import DomainModel, NonEmptyStr
from .reproduction import PaperReference

NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


def _duplicate_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return tuple(value for value, count in counts.items() if count > 1)


class ContentBlockType(str, Enum):
    TEXT = "text"
    HEADING = "heading"
    TABLE = "table"
    FIGURE = "figure"
    LIST = "list"
    EQUATION = "equation"
    OTHER = "other"


class ParseStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class BoundingBox(DomainModel):
    left: NonNegativeFloat
    top: NonNegativeFloat
    right: NonNegativeFloat
    bottom: NonNegativeFloat

    @model_validator(mode="after")
    def validate_edges(self) -> BoundingBox:
        if self.right < self.left or self.bottom < self.top:
            raise ValueError("bounding-box edges are inverted")
        return self


class ContentBlock(DomainModel):
    block_id: NonEmptyStr
    page_number: int = Field(ge=1)
    block_type: ContentBlockType
    text: str = ""
    bbox: BoundingBox | None = None
    reference_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> ContentBlock:
        if self.block_type in {ContentBlockType.TABLE, ContentBlockType.FIGURE} and not self.reference_id:
            raise ValueError("table and figure blocks require reference_id")
        return self


class PageBlock(DomainModel):
    page_number: int = Field(ge=1)
    text: str = ""
    content_blocks: tuple[ContentBlock, ...] = ()

    @model_validator(mode="after")
    def validate_blocks(self) -> PageBlock:
        if any(block.page_number != self.page_number for block in self.content_blocks):
            raise ValueError("page content block belongs to a different page")
        if len({block.block_id for block in self.content_blocks}) != len(self.content_blocks):
            raise ValueError("block ids must be unique within a page")
        return self


class SectionBlock(DomainModel):
    section_id: NonEmptyStr
    title: NonEmptyStr
    level: int = Field(ge=1)
    text: str = ""
    block_ids: tuple[NonEmptyStr, ...] = ()
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_page_range(self) -> SectionBlock:
        if self.end_page < self.start_page:
            raise ValueError("section page range is inverted")
        return self


class TableData(DomainModel):
    """Stable rectangular view; missing/merged cells remain empty strings."""

    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()

    @model_validator(mode="after")
    def validate_width(self) -> TableData:
        widths = {len(row) for row in self.rows}
        if len(widths) > 1 or (self.headers and widths and next(iter(widths)) != len(self.headers)):
            raise ValueError("table rows and headers must have a consistent width")
        return self


class TableBlock(DomainModel):
    table_id: NonEmptyStr
    label: NonEmptyStr | None = None
    caption: str = ""
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    raw_text: str = ""
    structured_data: TableData | None = None
    bbox: BoundingBox | None = None

    @model_validator(mode="after")
    def validate_page_range(self) -> TableBlock:
        if self.end_page < self.start_page:
            raise ValueError("table page range is inverted")
        return self


class FigureBlock(DomainModel):
    figure_id: NonEmptyStr
    label: NonEmptyStr | None = None
    caption: str = ""
    page_number: int = Field(ge=1)
    bbox: BoundingBox | None = None
    image_reference: NonEmptyStr | None = None
    figure_type: NonEmptyStr | None = None


class ParseMetadata(DomainModel):
    parser_name: NonEmptyStr
    parser_version: NonEmptyStr
    used_fallback: bool = False
    ocr_used: bool = False
    parse_status: ParseStatus
    warnings: tuple[NonEmptyStr, ...] = ()
    processing_time: NonNegativeFloat
    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def validate_status(self) -> ParseMetadata:
        if self.parse_status is ParseStatus.FAILED:
            raise ValueError("failed parses must be represented by PaperParsingError")
        if self.parse_status is ParseStatus.SUCCESS and self.warnings:
            raise ValueError("successful parses cannot contain warnings")
        if self.parse_status is ParseStatus.PARTIAL_SUCCESS and not self.warnings:
            raise ValueError("partial success requires at least one warning")
        return self


class PaperDocument(DomainModel):
    document_id: NonEmptyStr
    paper: PaperReference
    source: NonEmptyStr
    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    page_count: int = Field(ge=1)
    pages: tuple[PageBlock, ...]
    sections: tuple[SectionBlock, ...] = ()
    tables: tuple[TableBlock, ...] = ()
    figures: tuple[FigureBlock, ...] = ()
    parse_metadata: ParseMetadata

    @model_validator(mode="after")
    def validate_document(self) -> PaperDocument:
        if len(self.pages) != self.page_count:
            raise ValueError("page_count must equal the number of pages")
        if [page.page_number for page in self.pages] != list(range(1, self.page_count + 1)):
            raise ValueError("pages must be contiguous and one-based")
        if self.parse_metadata.content_hash != self.content_hash:
            raise ValueError("metadata content hash must match document content hash")
        block_ids = {block.block_id for page in self.pages for block in page.content_blocks}
        if len(block_ids) != sum(len(page.content_blocks) for page in self.pages):
            raise ValueError("block ids must be document-unique")
        table_ids = {table.table_id for table in self.tables}
        figure_ids = {figure.figure_id for figure in self.figures}
        duplicate_sections = _duplicate_ids(tuple(section.section_id for section in self.sections))
        duplicate_tables = _duplicate_ids(tuple(table.table_id for table in self.tables))
        duplicate_figures = _duplicate_ids(tuple(figure.figure_id for figure in self.figures))
        if duplicate_sections or duplicate_tables or duplicate_figures:
            parts = []
            if duplicate_sections:
                parts.append(f"duplicate section ids: {list(duplicate_sections)}")
            if duplicate_tables:
                parts.append(f"duplicate table ids: {list(duplicate_tables)}")
            if duplicate_figures:
                parts.append(f"duplicate figure ids: {list(duplicate_figures)}")
            raise ValueError("; ".join(parts))
        if any(table.end_page > self.page_count for table in self.tables):
            raise ValueError("table page range exceeds the document")
        if any(figure.page_number > self.page_count for figure in self.figures):
            raise ValueError("figure page exceeds the document")
        if any(section.end_page > self.page_count for section in self.sections):
            raise ValueError("section page range exceeds the document")
        for section in self.sections:
            if not set(section.block_ids).issubset(block_ids):
                raise ValueError("section references an unknown content block")
        for page in self.pages:
            for block in page.content_blocks:
                if block.block_type is ContentBlockType.TABLE and block.reference_id not in table_ids:
                    raise ValueError("table block references an unknown table")
                if block.block_type is ContentBlockType.FIGURE and block.reference_id not in figure_ids:
                    raise ValueError("figure block references an unknown figure")
        return self

    def page_locator(self, page_number: int) -> str:
        self._require_page(page_number)
        return f"page:{page_number}"

    def block_locator(self, block_id: str) -> str:
        for page in self.pages:
            if any(block.block_id == block_id for block in page.content_blocks):
                return f"page:{page.page_number}/block:{block_id}"
        raise ValueError(f"unknown block id: {block_id}")

    def section_locator(self, section_id: str) -> str:
        if not any(section.section_id == section_id for section in self.sections):
            raise ValueError(f"unknown section id: {section_id}")
        return f"section:{section_id}"

    def table_locator(self, table_id: str, *, row: str | None = None, column: str | None = None) -> str:
        if not any(table.table_id == table_id for table in self.tables):
            raise ValueError(f"unknown table id: {table_id}")
        locator = f"table:{table_id}"
        if row is not None:
            locator += f"/row:{row}"
        if column is not None:
            locator += f"/column:{column}"
        return locator

    def figure_locator(self, figure_id: str) -> str:
        if not any(figure.figure_id == figure_id for figure in self.figures):
            raise ValueError(f"unknown figure id: {figure_id}")
        return f"figure:{figure_id}"

    def _require_page(self, page_number: int) -> None:
        if not 1 <= page_number <= self.page_count:
            raise ValueError(f"unknown page number: {page_number}")
