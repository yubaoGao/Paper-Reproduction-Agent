import hashlib
import io
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from backend.app.domain import (
    ContentBlock,
    ContentBlockType,
    FigureBlock,
    PageBlock,
    PaperDocument,
    PaperReference,
    PaperSourceType,
    ParseMetadata,
    ParseStatus,
    TableBlock,
    TableData,
)
from backend.app.infrastructure.paper import DoclingPaperParser, PypdfPaperParser, SecurePaperSourceResolver
from backend.app.infrastructure.paper.source_resolver import HttpResponse
from backend.app.services import (
    CompositePaperParser,
    InvalidPaperSourceError,
    PaperDownloadError,
    PaperIngestionSettings,
    PaperParser,
    PaperParsingError,
    ResolvedPaperSource,
    UnsafePaperSourceError,
)


def paper(source_type=PaperSourceType.PDF_UPLOAD, source_uri="upload.pdf", arxiv_id=None):
    return PaperReference(id="paper-1", title="A Paper", source_type=source_type, source_uri=source_uri, arxiv_id=arxiv_id)


def minimal_pdf(texts):
    """Build a small offline, native-text PDF without a fixture dependency."""
    objects = []
    page_ids = []
    font_id = 3 + len(texts) * 2
    for index, text in enumerate(texts):
        page_id = 3 + index * 2
        content_id = page_id + 1
        page_ids.append(page_id)
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
        objects.extend([
            (page_id, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode()),
            (content_id, b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"),
        ])
    all_objects = [
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (2, f"<< /Type /Pages /Kids [{' '.join(f'{pid} 0 R' for pid in page_ids)}] /Count {len(texts)} >>".encode()),
        *objects,
        (font_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = {0: 0}
    for object_id, body in sorted(all_objects):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    max_id = max(offsets)
    output.extend(f"xref\n0 {max_id + 1}\n0000000000 65535 f \n".encode())
    for object_id in range(1, max_id + 1):
        output.extend(f"{offsets[object_id]:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def resolved(data=None):
    data = data or minimal_pdf(["1 Introduction", "2 Results"])
    return ResolvedPaperSource(data, "upload.pdf", "upload.pdf", hashlib.sha256(data).hexdigest())


class StaticParser(PaperParser):
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def parse(self, paper, source):
        if self.error:
            raise self.error
        return self.result


def document(status=ParseStatus.SUCCESS, warnings=()):
    source = resolved()
    metadata = ParseMetadata(parser_name="test", parser_version="1", parse_status=status, warnings=warnings, processing_time=0, content_hash=source.content_hash)
    return PaperDocument(document_id="doc", paper=paper(), source="upload.pdf", content_hash=source.content_hash, page_count=1, pages=(PageBlock(page_number=1, text="body"),), parse_metadata=metadata)


class PaperDomainTests(unittest.TestCase):
    def test_document_blocks_tables_figures_and_locators(self):
        source = resolved()
        table = TableBlock(table_id="2", label="Table 2", start_page=1, end_page=1, raw_text="Model,Accuracy", structured_data=TableData(headers=("Model", "Accuracy"), rows=(("DMSF", "75.3"),)))
        figure = FigureBlock(figure_id="3", label="Figure 3", page_number=1, image_reference="hash/figure-3.png")
        blocks = (
            ContentBlock(block_id="1", page_number=1, block_type=ContentBlockType.TABLE, reference_id="2"),
            ContentBlock(block_id="2", page_number=1, block_type=ContentBlockType.FIGURE, reference_id="3"),
        )
        metadata = ParseMetadata(parser_name="docling", parser_version="2", parse_status=ParseStatus.SUCCESS, processing_time=1, content_hash=source.content_hash)
        doc = PaperDocument(document_id="doc", paper=paper(), source="upload.pdf", content_hash=source.content_hash, page_count=1, pages=(PageBlock(page_number=1, text="text", content_blocks=blocks),), tables=(table,), figures=(figure,), parse_metadata=metadata)
        self.assertEqual(doc.block_locator("1"), "page:1/block:1")
        self.assertEqual(doc.table_locator("2", row="DMSF", column="Accuracy"), "table:2/row:DMSF/column:Accuracy")
        self.assertEqual(doc.figure_locator("3"), "figure:3")

    def test_domain_rejects_inconsistent_references_and_failed_metadata(self):
        source = resolved()
        with self.assertRaises(ValidationError):
            ParseMetadata(parser_name="x", parser_version="1", parse_status=ParseStatus.FAILED, processing_time=0, content_hash=source.content_hash)
        with self.assertRaises(ValidationError):
            TableData(headers=("a", "b"), rows=(("one",),))


class ParserTests(unittest.TestCase):
    def test_pypdf_native_text_multi_page(self):
        doc = PypdfPaperParser().parse(paper(), resolved())
        self.assertEqual(doc.page_count, 2)
        self.assertIn("Introduction", doc.pages[0].text)
        self.assertEqual(doc.parse_metadata.parse_status, ParseStatus.PARTIAL_SUCCESS)

    def test_corrupted_pdf_is_rejected(self):
        data = b"%PDF-corrupt"
        with self.assertRaises(PaperParsingError):
            PypdfPaperParser().parse(paper(), resolved(data))

    def test_password_encrypted_pdf_is_rejected(self):
        from pypdf import PdfWriter

        output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("secret")
        writer.write(output)
        with self.assertRaises(PaperParsingError):
            PypdfPaperParser().parse(paper(), resolved(output.getvalue()))

    def test_pypdf_timeout_terminates_worker(self):
        process = MagicMock()
        process.communicate.side_effect = [
            __import__("subprocess").TimeoutExpired("worker", 0.01),
            (b"", b""),
        ]
        with patch("backend.app.infrastructure.paper.pypdf_parser.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(PaperParsingError, "timed out"):
                PypdfPaperParser(PaperIngestionSettings(parse_timeout_seconds=0.01)).parse(paper(), resolved())
        process.kill.assert_called_once()

    def test_primary_failure_triggers_fallback(self):
        fallback_doc = document(ParseStatus.PARTIAL_SUCCESS, ("fallback limitation",))
        parser = CompositePaperParser(StaticParser(error=PaperParsingError("boom")), StaticParser(result=fallback_doc))
        result = parser.parse(paper(), resolved())
        self.assertTrue(result.parse_metadata.used_fallback)
        self.assertIn("hard failure", result.parse_metadata.warnings[0])

    def test_partial_primary_does_not_trigger_fallback(self):
        primary_doc = document(ParseStatus.PARTIAL_SUCCESS, ("one table failed",))
        fallback = StaticParser(error=AssertionError("fallback must not run"))
        self.assertIs(CompositePaperParser(StaticParser(result=primary_doc), fallback).parse(paper(), resolved()), primary_doc)


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def open(self, url, timeout, resolved_ip):
        self.urls.append(url)
        status, headers, body = self.responses.pop(0)
        return HttpResponse(status, headers, io.BytesIO(body))


def public_dns(host, port, type=None):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class SourceResolverTests(unittest.TestCase):
    def test_upload_bytes_stream_and_local_file(self):
        data = minimal_pdf(["hello"])
        resolver = SecurePaperSourceResolver()
        self.assertEqual(resolver.resolve(paper(), upload=data).data, data)
        self.assertEqual(resolver.resolve(paper(), upload=io.BytesIO(data)).data, data)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.pdf"
            path.write_bytes(data)
            local = paper(PaperSourceType.LOCAL_FILE, str(path))
            self.assertEqual(resolver.resolve(local).content_hash, hashlib.sha256(data).hexdigest())

    def test_url_and_arxiv_download(self):
        data = minimal_pdf(["hello"])
        http = FakeHttp([(200, {"Content-Type": "application/pdf"}, data), (200, {"Content-Type": "application/pdf"}, data)])
        resolver = SecurePaperSourceResolver(http=http, dns_resolver=public_dns)
        resolver.resolve(paper(PaperSourceType.URL, "https://example.com/a.pdf"))
        resolver.resolve(paper(PaperSourceType.ARXIV, "https://arxiv.org/abs/2401.01234", "2401.01234"))
        self.assertEqual(http.urls[1], "https://arxiv.org/pdf/2401.01234.pdf")

    def test_legacy_arxiv_id_is_canonicalized(self):
        data = minimal_pdf(["hello"])
        http = FakeHttp([(200, {"Content-Type": "application/pdf"}, data)])
        resolver = SecurePaperSourceResolver(http=http, dns_resolver=public_dns)
        resolver.resolve(paper(PaperSourceType.ARXIV, "https://arxiv.org/abs/hep-th/9901001", "hep-th/9901001"))
        self.assertEqual(http.urls, ["https://arxiv.org/pdf/hep-th/9901001.pdf"])

    def test_unsafe_protocol_localhost_and_private_ip(self):
        for url in ("ftp://example.com/a.pdf", "https://localhost/a.pdf", "https://127.0.0.1/a.pdf"):
            with self.subTest(url=url), self.assertRaises((UnsafePaperSourceError, ValidationError)):
                SecurePaperSourceResolver(http=FakeHttp([])).resolve(paper(PaperSourceType.URL, url))

    def test_redirect_is_revalidated(self):
        http = FakeHttp([(302, {"Location": "https://10.0.0.1/internal.pdf"}, b"")])
        with self.assertRaises(UnsafePaperSourceError):
            SecurePaperSourceResolver(http=http, dns_resolver=public_dns).resolve(paper(PaperSourceType.URL, "https://example.com/a.pdf"))

    def test_download_size_content_type_and_magic(self):
        cases = [
            ((200, {"Content-Type": "text/html"}, b"%PDF-x"), PaperDownloadError),
            ((200, {"Content-Type": "application/pdf"}, b"not-pdf"), InvalidPaperSourceError),
            ((200, {"Content-Type": "application/pdf"}, b"%PDF-" + b"x" * 2000), PaperDownloadError),
        ]
        settings = PaperIngestionSettings(max_file_size_bytes=1024)
        for response, error in cases:
            with self.subTest(error=error), self.assertRaises(error):
                SecurePaperSourceResolver(settings, http=FakeHttp([response]), dns_resolver=public_dns).resolve(paper(PaperSourceType.URL, "https://example.com/a.pdf"))


class _FakeFrame:
    columns = ("Model", "Dataset", "Accuracy", "F1")
    def itertuples(self, index=False, name=None):
        return iter((("DMSF", "MVSA-S", "75.3", "75.1"), ("w/o loss", "MVSA-S", "73.0", "72.8")))
    def to_csv(self, index=False):
        return "Model,Dataset,Accuracy,F1\nDMSF,MVSA-S,75.3,75.1"


class _FakeItem:
    def __init__(self, label, text="", page=1):
        self.label, self.text, self.level = SimpleNamespace(value=label), text, 1
        self.prov = [SimpleNamespace(page_no=page, bbox=SimpleNamespace(l=1, t=9, r=8, b=2))]
    def caption_text(self, doc):
        return "Synthetic caption"


class _FakeTable(_FakeItem):
    def export_to_dataframe(self, doc): return _FakeFrame()


class _FakeImage:
    def save(self, path, format): Path(path).write_bytes(b"png")


class _FakePicture(_FakeItem):
    def get_image(self, doc): return _FakeImage()


class _FakeDoc:
    def __init__(self):
        self.heading = _FakeItem("section_header", "1 Results")
        self.table = _FakeTable("table")
        self.picture = _FakePicture("picture")
        self.tables, self.pictures = [self.table], [self.picture]
    def num_pages(self): return 1
    def iterate_items(self): return iter(((self.heading, 1), (self.table, 1), (self.picture, 1)))
    def export_to_text(self, **kwargs): return "Results and extracted content"


class _TestDoclingParser(DoclingPaperParser):
    def _convert(self, source, ocr_used):
        return SimpleNamespace(status="success", document=_FakeDoc())


class DoclingMappingTests(unittest.TestCase):
    def test_structure_table_figure_and_ocr_mapping(self):
        with tempfile.TemporaryDirectory() as directory, patch("importlib.metadata.version", return_value="2.test"):
            settings = PaperIngestionSettings(ocr_mode="always", figure_artifact_directory=Path(directory))
            doc = _TestDoclingParser(settings).parse(paper(), resolved())
            self.assertTrue(doc.parse_metadata.ocr_used)
            self.assertEqual(doc.tables[0].structured_data.headers, ("Model", "Dataset", "Accuracy", "F1"))
            self.assertEqual(len(doc.tables[0].structured_data.rows), 2)
            self.assertEqual(doc.figures[0].caption, "Synthetic caption")
            self.assertTrue((Path(directory) / doc.figures[0].image_reference).is_file())
            self.assertEqual(doc.sections[0].section_id, "1")


if __name__ == "__main__":
    unittest.main()
