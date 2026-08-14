"""Docling-backed production parser mapped into the stable paper IR."""

from __future__ import annotations

import importlib.metadata
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from backend.app.domain import (
    BoundingBox,
    ContentBlock,
    ContentBlockType,
    FigureBlock,
    PageBlock,
    PaperDocument,
    PaperReference,
    ParseMetadata,
    ParseStatus,
    SectionBlock,
    TableBlock,
    TableData,
)
from backend.app.services import PaperIngestionSettings, PaperParser, PaperParsingError, ResolvedPaperSource


class DoclingPaperParser(PaperParser):
    """Primary parser for reading order, hierarchy, OCR, tables and figures."""

    def __init__(self, settings: PaperIngestionSettings | None = None) -> None:
        self.settings = settings or PaperIngestionSettings()

    def parse(self, paper: PaperReference, source: ResolvedPaperSource) -> PaperDocument:
        started = time.monotonic()
        ocr_used = self._should_use_ocr(source.data)
        try:
            result = self._convert(source, ocr_used)
        except PaperParsingError:
            raise
        except Exception as exc:
            raise PaperParsingError(f"Docling conversion failed: {exc}") from exc

        status_text = str(getattr(result, "status", "success")).lower()
        if "fail" in status_text:
            raise PaperParsingError(f"Docling conversion status is {result.status}")
        doc = getattr(result, "document", None)
        if doc is None:
            raise PaperParsingError("Docling returned no document")

        warnings: list[str] = []
        if "partial" in status_text:
            warnings.append("Docling reported a partial conversion")
        for error in getattr(result, "errors", ()) or ():
            detail = str(getattr(error, "error_message", error)).strip()
            if detail:
                warnings.append(f"Docling conversion warning: {detail[:500]}")
        pages, blocks_by_item = self._pages_and_blocks(doc, warnings)
        if not pages or len(pages) > self.settings.max_page_count:
            raise PaperParsingError("PDF has no pages or exceeds maximum page count")
        tables = self._tables(doc, blocks_by_item, warnings)
        figures = self._figures(doc, source, blocks_by_item, warnings)
        sections = self._sections(doc, blocks_by_item, len(pages), warnings)

        # Rebuild pages after table/figure extraction has assigned stable references.
        pages = self._rebuild_pages(pages, blocks_by_item)
        parse_status = ParseStatus.PARTIAL_SUCCESS if warnings else ParseStatus.SUCCESS
        metadata = ParseMetadata(
            parser_name="docling",
            parser_version=importlib.metadata.version("docling"),
            ocr_used=ocr_used,
            parse_status=parse_status,
            warnings=tuple(dict.fromkeys(warnings)),
            processing_time=time.monotonic() - started,
            content_hash=source.content_hash,
        )
        return PaperDocument(
            document_id=f"{paper.id}:{source.content_hash[:16]}",
            paper=paper,
            source=source.source_uri,
            content_hash=source.content_hash,
            page_count=len(pages),
            pages=tuple(pages),
            sections=tuple(sections),
            tables=tuple(tables),
            figures=tuple(figures),
            parse_metadata=metadata,
        )

    def _convert(self, source: ResolvedPaperSource, ocr_used: bool) -> Any:
        try:
            from docling.datamodel.base_models import DocumentStream, InputFormat
            from docling.datamodel.pipeline_options import HeadingHierarchyOptions, OcrAutoOptions, PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as exc:
            raise PaperParsingError(
                "Docling is not installed; install the 'paper' dependency extra"
            ) from exc

        options = PdfPipelineOptions(
            do_ocr=ocr_used,
            do_table_structure=True,
            generate_picture_images=True,
            generate_parsed_pages=True,
            document_timeout=self.settings.parse_timeout_seconds,
            enable_remote_services=False,
            allow_external_plugins=False,
            heading_hierarchy_options=HeadingHierarchyOptions(enabled=True),
            ocr_options=OcrAutoOptions(lang=list(self.settings.ocr_languages)),
        )
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
        )
        stream = DocumentStream(name=source.filename, stream=BytesIO(source.data))
        return converter.convert(
            stream,
            max_num_pages=self.settings.max_page_count,
            max_file_size=self.settings.max_file_size_bytes,
        )

    def _pages_and_blocks(self, doc: Any, warnings: list[str]) -> tuple[list[PageBlock], dict[int, ContentBlock]]:
        page_count = int(doc.num_pages())
        per_page: dict[int, list[ContentBlock]] = {number: [] for number in range(1, page_count + 1)}
        blocks_by_item: dict[int, ContentBlock] = {}
        counters = {number: 0 for number in per_page}
        for item, _level in doc.iterate_items():
            page_number, bbox = self._provenance(item)
            if page_number not in per_page:
                continue
            kind = self._block_type(item)
            text = str(getattr(item, "text", "") or "").strip()
            counters[page_number] += 1
            block = ContentBlock(
                block_id=f"p{page_number}-b{counters[page_number]}",
                page_number=page_number,
                block_type=kind,
                text=text,
                bbox=bbox,
                reference_id=("pending" if kind in {ContentBlockType.TABLE, ContentBlockType.FIGURE} else None),
            )
            per_page[page_number].append(block)
            blocks_by_item[id(item)] = block
        pages = []
        for number in range(1, page_count + 1):
            try:
                text = doc.export_to_text(page_no=number, traverse_pictures=True).strip()
            except Exception:
                text = "\n".join(block.text for block in per_page[number] if block.text).strip()
                warnings.append(f"page {number} text export failed; reconstructed from content blocks")
            pages.append(PageBlock(page_number=number, text=text, content_blocks=tuple(per_page[number])))
        return pages, blocks_by_item

    def _tables(self, doc: Any, blocks: dict[int, ContentBlock], warnings: list[str]) -> list[TableBlock]:
        tables: list[TableBlock] = []
        for index, item in enumerate(getattr(doc, "tables", ()), start=1):
            table_id = str(index)
            page, bbox = self._provenance(item)
            provenance = getattr(item, "prov", ()) or ()
            end_page = max((max(1, int(getattr(prov, "page_no", page))) for prov in provenance), default=page)
            caption = self._caption(item, doc)
            structured: TableData | None = None
            raw_text = ""
            try:
                frame = item.export_to_dataframe(doc=doc)
                headers = tuple(self._cell_text(value) for value in frame.columns)
                rows = tuple(tuple(self._cell_text(value) for value in row) for row in frame.itertuples(index=False, name=None))
                structured = TableData(headers=headers, rows=rows)
                raw_text = frame.to_csv(index=False).strip()
            except Exception as exc:
                try:
                    raw_text = item.export_to_text(doc=doc).strip()
                except Exception:
                    raw_text = str(getattr(item, "text", "") or "").strip()
                warnings.append(f"table {table_id} structure recovery failed: {exc}")
            tables.append(
                TableBlock(
                    table_id=table_id,
                    label=f"Table {table_id}",
                    caption=caption,
                    start_page=page,
                    end_page=end_page,
                    raw_text=raw_text,
                    structured_data=structured,
                    bbox=bbox,
                )
            )
            if id(item) in blocks:
                blocks[id(item)] = blocks[id(item)].model_copy(update={"reference_id": table_id})
        return tables

    def _figures(self, doc: Any, source: ResolvedPaperSource, blocks: dict[int, ContentBlock], warnings: list[str]) -> list[FigureBlock]:
        figures: list[FigureBlock] = []
        asset_root = self.settings.figure_artifact_directory / source.content_hash
        for index, item in enumerate(getattr(doc, "pictures", ()), start=1):
            figure_id = str(index)
            page, bbox = self._provenance(item)
            relative_reference = Path(source.content_hash) / f"figure-{index}.png"
            image_reference: str | None = relative_reference.as_posix()
            try:
                image = item.get_image(doc)
                if image is None:
                    raise ValueError("Docling returned no picture image")
                asset_root.mkdir(parents=True, exist_ok=True)
                image.save(asset_root / f"figure-{index}.png", "PNG")
            except Exception as exc:
                warnings.append(f"figure {figure_id} image extraction failed: {exc}")
                image_reference = None
            figures.append(
                FigureBlock(
                    figure_id=figure_id,
                    label=f"Figure {figure_id}",
                    caption=self._caption(item, doc),
                    page_number=page,
                    bbox=bbox,
                    image_reference=image_reference,
                )
            )
            if id(item) in blocks:
                blocks[id(item)] = blocks[id(item)].model_copy(update={"reference_id": figure_id})
        return figures

    def _sections(self, doc: Any, blocks: dict[int, ContentBlock], page_count: int, warnings: list[str]) -> list[SectionBlock]:
        ordered_blocks: list[tuple[Any, ContentBlock]] = []
        for item, _depth in doc.iterate_items():
            block = blocks.get(id(item))
            if block:
                ordered_blocks.append((item, block))
        heading_positions = [index for index, (_item, block) in enumerate(ordered_blocks) if block.block_type is ContentBlockType.HEADING]
        headings = [ordered_blocks[index] for index in heading_positions]
        if not headings:
            warnings.append("Docling did not recover a section hierarchy")
            return []
        sections: list[SectionBlock] = []
        for index, (item, block) in enumerate(headings, start=1):
            next_page = headings[index][1].page_number if index < len(headings) else page_count
            start_position = heading_positions[index - 1]
            end_position = heading_positions[index] if index < len(heading_positions) else len(ordered_blocks)
            section_blocks = [candidate for _candidate_item, candidate in ordered_blocks[start_position:end_position]]
            sections.append(
                SectionBlock(
                    section_id=str(index),
                    title=block.text,
                    level=max(1, int(getattr(item, "level", 1) or 1)),
                    text="\n".join(candidate.text for candidate in section_blocks if candidate.text),
                    block_ids=tuple(candidate.block_id for candidate in section_blocks),
                    start_page=block.page_number,
                    end_page=max(block.page_number, next_page),
                )
            )
        return sections

    @staticmethod
    def _rebuild_pages(pages: list[PageBlock], blocks: dict[int, ContentBlock]) -> list[PageBlock]:
        replacements = {block.block_id: block for block in blocks.values()}
        return [page.model_copy(update={"content_blocks": tuple(replacements.get(block.block_id, block) for block in page.content_blocks)}) for page in pages]

    def _should_use_ocr(self, data: bytes) -> bool:
        if self.settings.ocr_mode == "always":
            return True
        if self.settings.ocr_mode == "never":
            return False
        try:
            from .pypdf_parser import PypdfPaperParser

            texts = PypdfPaperParser(self.settings)._extract_with_deadline(data)
            return sum(len(text) for text in texts[:3]) < 40
        except Exception:
            return True

    @staticmethod
    def _block_type(item: Any) -> ContentBlockType:
        label = str(getattr(getattr(item, "label", ""), "value", getattr(item, "label", ""))).lower()
        if "section_header" in label or "title" in label:
            return ContentBlockType.HEADING
        if "table" in label:
            return ContentBlockType.TABLE
        if "picture" in label or "figure" in label:
            return ContentBlockType.FIGURE
        if "list" in label:
            return ContentBlockType.LIST
        if "formula" in label or "equation" in label:
            return ContentBlockType.EQUATION
        if "text" in label or "paragraph" in label:
            return ContentBlockType.TEXT
        return ContentBlockType.OTHER

    @staticmethod
    def _provenance(item: Any) -> tuple[int, BoundingBox | None]:
        provenance = getattr(item, "prov", ()) or ()
        if not provenance:
            return 0, None
        prov = provenance[0]
        page = max(1, int(getattr(prov, "page_no", 1)))
        raw = getattr(prov, "bbox", None)
        if raw is None:
            return page, None
        left, right = float(raw.l), float(raw.r)
        top, bottom = float(raw.t), float(raw.b)
        return page, BoundingBox(left=min(left, right), top=min(top, bottom), right=max(left, right), bottom=max(top, bottom))

    @staticmethod
    def _caption(item: Any, doc: Any) -> str:
        try:
            return str(item.caption_text(doc) or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _cell_text(value: Any) -> str:
        if isinstance(value, tuple):
            return " / ".join(str(part).strip() for part in value if str(part).strip())
        return str(value).strip()
