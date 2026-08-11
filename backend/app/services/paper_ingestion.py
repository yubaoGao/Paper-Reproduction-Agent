"""Application contracts and orchestration for paper ingestion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain import PaperDocument, PaperReference, ParseStatus


class PaperIngestionError(Exception):
    """Base error visible at the paper-ingestion boundary."""


class InvalidPaperSourceError(PaperIngestionError):
    pass


class UnsafePaperSourceError(InvalidPaperSourceError):
    pass


class PaperDownloadError(PaperIngestionError):
    pass


class PaperParsingError(PaperIngestionError):
    pass


class PaperIngestionSettings(BaseModel):
    """Centralized resource and network policy, independent of app config."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    max_file_size_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    max_page_count: int = Field(default=500, ge=1)
    parse_timeout_seconds: float = Field(default=300.0, gt=0)
    download_timeout_seconds: float = Field(default=20.0, gt=0)
    max_redirects: int = Field(default=5, ge=0, le=20)
    allow_http: bool = False
    ocr_mode: str = Field(default="auto", pattern=r"^(auto|always|never)$")
    ocr_languages: tuple[str, ...] = ("en",)
    figure_artifact_directory: Path = Path("workspace/paper-assets")


@dataclass(frozen=True)
class ResolvedPaperSource:
    """Validated PDF bytes passed to parsers without network responsibilities."""

    data: bytes
    source_uri: str
    filename: str
    content_hash: str

    def open(self) -> BinaryIO:
        from io import BytesIO

        return BytesIO(self.data)


class PaperSourceResolver(ABC):
    @abstractmethod
    def resolve(
        self,
        paper: PaperReference,
        *,
        upload: bytes | bytearray | memoryview | BinaryIO | None = None,
    ) -> ResolvedPaperSource:
        """Resolve and validate a source; parsers never download it."""


class PaperParser(ABC):
    @abstractmethod
    def parse(self, paper: PaperReference, source: ResolvedPaperSource) -> PaperDocument:
        """Parse a resolved source into the stable paper IR."""


class CompositePaperParser(PaperParser):
    """Use fallback only for hard failure or an unusable primary document."""

    def __init__(self, primary: PaperParser, fallback: PaperParser) -> None:
        self.primary = primary
        self.fallback = fallback

    def parse(self, paper: PaperReference, source: ResolvedPaperSource) -> PaperDocument:
        fallback_reason: str | None = None
        try:
            primary_document = self.primary.parse(paper, source)
            if self._is_usable(primary_document):
                return primary_document
            fallback_reason = "primary parser produced zero usable text/content"
        except PaperParsingError as exc:
            fallback_reason = f"primary parser hard failure: {exc}"

        try:
            document = self.fallback.parse(paper, source)
        except PaperParsingError as exc:
            raise PaperParsingError(
                f"primary and fallback parsers failed; primary={fallback_reason}; fallback={exc}"
            ) from exc

        warnings = (fallback_reason or "primary parser was unusable",) + document.parse_metadata.warnings
        metadata = document.parse_metadata.model_copy(
            update={
                "used_fallback": True,
                "parse_status": ParseStatus.PARTIAL_SUCCESS,
                "warnings": warnings,
            }
        )
        return document.model_copy(update={"parse_metadata": metadata})

    @staticmethod
    def _is_usable(document: PaperDocument) -> bool:
        return any(page.text.strip() or page.content_blocks for page in document.pages)


class PaperIngestionService:
    def __init__(self, resolver: PaperSourceResolver, parser: PaperParser) -> None:
        self.resolver = resolver
        self.parser = parser

    def ingest(
        self,
        paper: PaperReference,
        *,
        upload: bytes | bytearray | memoryview | BinaryIO | None = None,
    ) -> PaperDocument:
        source = self.resolver.resolve(paper, upload=upload)
        return self.parser.parse(paper, source)
