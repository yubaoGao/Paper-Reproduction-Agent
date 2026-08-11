"""Real Docling OCR smoke test; opt in after installing models/runtime."""

import hashlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path

from backend.app.domain import PaperReference, PaperSourceType
from backend.app.infrastructure.paper import DoclingPaperParser
from backend.app.services import PaperIngestionSettings, ResolvedPaperSource


DOCLING_INTEGRATION = os.environ.get("PAPER_INGESTION_INTEGRATION") == "1"
HAS_DOCLING = importlib.util.find_spec("docling") is not None


@unittest.skipUnless(
    DOCLING_INTEGRATION and HAS_DOCLING,
    "set PAPER_INGESTION_INTEGRATION=1 after installing Docling and its model artifacts",
)
class DoclingOcrIntegrationTests(unittest.TestCase):
    def test_scanned_pdf_uses_real_ocr(self) -> None:
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (1800, 1000), "white")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("arial.ttf", 56)
        except OSError:
            font = ImageFont.load_default(size=56)
        draw.text((100, 180), "PaperReproAgent OCR fixture", fill="black", font=font)
        draw.text((100, 320), "Accuracy 91.7 F1 90.4", fill="black", font=font)
        buffer = io.BytesIO()
        image.save(buffer, format="PDF", resolution=200)
        data = buffer.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        source = ResolvedPaperSource(data, "upload:ocr", "scanned.pdf", digest)
        paper = PaperReference(
            id="ocr-fixture",
            title="OCR fixture",
            source_type=PaperSourceType.PDF_UPLOAD,
            source_uri="upload:ocr",
        )
        with tempfile.TemporaryDirectory() as directory:
            parser = DoclingPaperParser(
                PaperIngestionSettings(
                    ocr_mode="always",
                    figure_artifact_directory=Path(directory),
                )
            )
            document = parser.parse(paper, source)
        text = "\n".join(page.text for page in document.pages)
        self.assertTrue(document.parse_metadata.ocr_used)
        self.assertIn("PaperReproAgent", text)
        self.assertIn("Accuracy", text)


if __name__ == "__main__":
    unittest.main()
