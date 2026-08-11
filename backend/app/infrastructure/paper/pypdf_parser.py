"""Lightweight page-text fallback parser."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from backend.app.domain import (
    ContentBlock,
    ContentBlockType,
    PageBlock,
    PaperDocument,
    PaperReference,
    ParseMetadata,
    ParseStatus,
    SectionBlock,
)
from backend.app.services import PaperIngestionSettings, PaperParser, PaperParsingError, ResolvedPaperSource

_HEADING = re.compile(r"^(?:(\d+(?:\.\d+)*)[.)]?\s+)?([A-Z][^\n]{2,100})$")


class PypdfPaperParser(PaperParser):
    """Fallback that preserves page text without claiming layout fidelity."""

    def __init__(self, settings: PaperIngestionSettings | None = None) -> None:
        self.settings = settings or PaperIngestionSettings()

    def parse(self, paper: PaperReference, source: ResolvedPaperSource) -> PaperDocument:
        started = time.monotonic()
        texts = self._extract_with_deadline(source.data)
        pages: list[PageBlock] = []
        for number, text in enumerate(texts, start=1):
            blocks = ()
            if text:
                blocks = (
                    ContentBlock(
                        block_id=f"p{number}-b1",
                        page_number=number,
                        block_type=ContentBlockType.TEXT,
                        text=text,
                    ),
                )
            pages.append(PageBlock(page_number=number, text=text, content_blocks=blocks))

        if not any(page.text for page in pages):
            raise PaperParsingError("pypdf found no usable text; OCR is required")

        sections = self._heuristic_sections(pages)
        warnings = [
            "pypdf fallback preserves page text but cannot recover authoritative layout, tables, or figures",
        ]
        if sections:
            warnings.append("section hierarchy was inferred with a fallback heading heuristic")
        else:
            warnings.append("section hierarchy could not be recovered")
        elapsed = time.monotonic() - started
        metadata = ParseMetadata(
            parser_name="pypdf",
            parser_version=importlib.metadata.version("pypdf"),
            parse_status=ParseStatus.PARTIAL_SUCCESS,
            warnings=tuple(warnings),
            processing_time=elapsed,
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
            parse_metadata=metadata,
        )

    def _heuristic_sections(self, pages: list[PageBlock]) -> list[SectionBlock]:
        found: list[tuple[str, str, int, int]] = []
        for page in pages:
            for line in page.text.splitlines():
                match = _HEADING.fullmatch(line.strip())
                if not match:
                    continue
                number, title = match.groups()
                if number:
                    level = number.count(".") + 1
                    section_id = number
                elif title.upper() == title and len(title.split()) <= 10:
                    level = 1
                    section_id = str(len(found) + 1)
                else:
                    continue
                found.append((section_id, title.strip(), level, page.page_number))
        sections: list[SectionBlock] = []
        for index, (section_id, title, level, start_page) in enumerate(found):
            end_page = found[index + 1][3] if index + 1 < len(found) else len(pages)
            sections.append(
                SectionBlock(
                    section_id=section_id,
                    title=title,
                    level=level,
                    start_page=start_page,
                    end_page=max(start_page, end_page),
                )
            )
        return sections

    def _extract_with_deadline(self, data: bytes) -> tuple[str, ...]:
        with tempfile.TemporaryDirectory(prefix="paperrepro-pypdf-") as directory:
            input_path = Path(directory) / "input.pdf"
            output_path = Path(directory) / "result.json"
            input_path.write_bytes(data)
            command = [
                sys.executable,
                "-m",
                "backend.app.infrastructure.paper.pypdf_parser",
                "--worker",
                str(input_path),
                str(output_path),
                str(self.settings.max_page_count),
            ]
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creation_flags,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            try:
                _stdout, stderr = process.communicate(timeout=self.settings.parse_timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise PaperParsingError("pypdf parsing timed out") from None
            if process.returncode != 0 or not output_path.is_file():
                detail = stderr.decode("utf-8", errors="replace").strip()[-500:]
                raise PaperParsingError(f"pypdf worker failed: {detail or 'no result'}")
            result = json.loads(output_path.read_text(encoding="utf-8"))
        if not result["ok"]:
            raise PaperParsingError(result["error"])
        return tuple(result["pages"])


def _extract_pages_worker(input_path: Path, output_path: Path, max_page_count: int) -> None:
    """Run untrusted pypdf work in a parent-terminable subprocess."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(input_path), strict=False)
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception as exc:
                raise ValueError("encrypted PDF cannot be opened") from exc
            if not unlocked:
                raise ValueError("encrypted PDF requires a password")
        page_count = len(reader.pages)
        if page_count == 0:
            raise ValueError("PDF has no pages")
        if page_count > max_page_count:
            raise ValueError("PDF exceeds maximum page count")
        result = {"ok": True, "pages": [(page.extract_text() or "").strip() for page in reader.pages]}
    except Exception as exc:
        result = {"ok": False, "error": f"pypdf could not parse PDF: {exc}"}
    output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--worker":
        _extract_pages_worker(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))
    else:
        raise SystemExit("this module is an internal pypdf worker")
