"""Coverage for PDF/OCR/metadata ingestion (OCR is mocked so this stays fast)."""

from __future__ import annotations

import json
import subprocess
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pymupdf
import pytest
from PIL import Image

from app.ingestion import ingestion_service, metadata_extractor, ocr_extractor
from app.ingestion.ingestion_service import _needs_review, ingest_document
from app.ingestion.metadata_extractor import extract_metadata
from app.ingestion.ocr_extractor import extract_image_with_ocr
from app.ingestion.pdf_extractor import extract_pdf, pdf_contains_text


def _text_pdf_bytes(text: str = "Golden Harvest Trading") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((48, 80), text, fontsize=14, fontname="helv")
    logo = pymupdf.open()
    logo_page = logo.new_page(width=32, height=16)
    logo_page.draw_rect(logo_page.rect, fill=(0.8, 0.1, 0.1))
    pix = logo_page.get_pixmap()
    logo.close()
    page.insert_image(pymupdf.Rect(12, 12, 60, 36), pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data


def _blank_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    page.draw_rect(page.rect, fill=(1, 1, 1))
    data = doc.tobytes()
    doc.close()
    return data


def _png_bytes() -> bytes:
    image = Image.new("RGB", (64, 32), (255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _ocr_result(*pairs: tuple[str, float]):
    texts, scores = zip(*pairs) if pairs else ((), ())
    return SimpleNamespace(boxes=[True] * len(texts) if texts else None, txts=list(texts), scores=list(scores))


@pytest.fixture
def _stub_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ingestion_service, "extract_metadata", lambda *_: {"FileName": "fixture.pdf"}
    )


@pytest.fixture
def _stub_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ocr_extractor,
        "reader",
        lambda image: _ocr_result(("OCBC", 0.92), ("12345678", 0.88)),
    )


# ---------------------------------------------------------------------------
# pdf_extractor
# ---------------------------------------------------------------------------


def test_pdf_contains_text_detects_native_and_blank() -> None:
    assert pdf_contains_text(_text_pdf_bytes()) is True
    assert pdf_contains_text(_blank_pdf_bytes()) is False


def test_extract_pdf_reads_spans_and_skips_image_blocks() -> None:
    result = extract_pdf(_text_pdf_bytes("Hello World"))
    assert "Hello World" in result["text"]
    assert result["pages"][0]["page_number"] == 1
    texts = [element["text"] for element in result["pages"][0]["elements"]]
    assert any("Hello" in text for text in texts)
    assert all(element["extraction_method"] == "native_pdf" for element in result["pages"][0]["elements"])


# ---------------------------------------------------------------------------
# metadata_extractor
# ---------------------------------------------------------------------------


def test_extract_metadata_returns_exiftool_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"FileName": "doc.pdf", "Author": "QA"}]
    monkeypatch.setattr(
        metadata_extractor.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout=json.dumps(payload)),
    )
    assert extract_metadata(b"%PDF", "doc.pdf") == payload[0]


def test_extract_metadata_records_failures_instead_of_returning_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # exiftool always reports basic file details, so every one of these is a
    # failure to run, recorded under EXTRACTION_ERROR_KEY rather than as {}.
    def error_for(run) -> str:
        monkeypatch.setattr(metadata_extractor.subprocess, "run", run)
        result = extract_metadata(b"x", "a.pdf")
        assert list(result) == [metadata_extractor.EXTRACTION_ERROR_KEY]
        return result[metadata_extractor.EXTRACTION_ERROR_KEY]

    assert error_for(lambda *a, **k: SimpleNamespace(stdout="[]")) == "exiftool returned no output"

    def missing(*a, **k):
        raise FileNotFoundError("exiftool")

    assert "not installed" in error_for(missing)

    def failed(*a, **k):
        raise subprocess.CalledProcessError(1, "exiftool", stderr="Error: File format error\n")

    assert error_for(failed) == "exiftool failed (Error: File format error)"

    assert "couldn't be parsed" in error_for(lambda *a, **k: SimpleNamespace(stdout="not-json"))


# ---------------------------------------------------------------------------
# ocr_extractor
# ---------------------------------------------------------------------------


def test_extract_image_with_ocr_from_pil_and_numpy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_extractor, "reader", lambda image: _ocr_result(("Hello", 0.91)))
    pil = extract_image_with_ocr(Image.new("RGB", (20, 10), (255, 255, 255)), page_number=2)
    assert pil["page_number"] == 2
    assert pil["text"] == "Hello"
    assert pil["elements"][0]["confidence"] == pytest.approx(0.91)

    array = np.zeros((8, 8, 3), dtype=np.uint8)
    from_array = extract_image_with_ocr(array)
    assert from_array["text"] == "Hello"


def test_extract_image_with_ocr_when_boxes_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_extractor, "reader", lambda image: _ocr_result())
    result = extract_image_with_ocr(Image.new("L", (8, 8), 255))
    assert result["text"] == ""
    assert result["elements"] == []


# ---------------------------------------------------------------------------
# ingestion_service
# ---------------------------------------------------------------------------


def test_needs_review_flags_empty_and_low_ocr_confidence() -> None:
    assert _needs_review("native_pdf", "   ", []) is True
    assert _needs_review("native_pdf", "Hello", []) is False
    low = [{"elements": [{"confidence": 0.2}, {"confidence": 0.3}]}]
    assert _needs_review("ocr", "Hello", low) is True
    high = [{"elements": [{"confidence": 0.9}, {"confidence": 0.8}]}]
    assert _needs_review("ocr", "Hello", high) is False
    assert _needs_review("ocr", "Hello", [{"elements": []}]) is False


def test_ingest_native_pdf(_stub_metadata) -> None:
    result = ingest_document(_text_pdf_bytes(), "cert.pdf", "application/pdf")
    assert result["extraction_method"] == "native_pdf"
    assert "Golden Harvest" in result["raw_text"]
    assert result["needs_review"] is False
    assert result["status"] == "completed"
    assert result["metadata"]["FileName"] == "fixture.pdf"


def test_ingest_scanned_pdf_uses_ocr(_stub_metadata, _stub_ocr) -> None:
    result = ingest_document(_blank_pdf_bytes(), "scan.pdf", "application/pdf")
    assert result["extraction_method"] == "ocr"
    assert "OCBC" in result["raw_text"]
    assert result["pages"][0]["page_number"] == 1
    assert result["needs_review"] is False


def test_ingest_image(_stub_metadata, _stub_ocr) -> None:
    result = ingest_document(_png_bytes(), "scan.png", "image/png")
    assert result["extraction_method"] == "ocr"
    assert result["file_type"] == "image/png"
    assert result["pages"][0]["elements"]


def test_ingest_low_ocr_confidence_needs_review(
    _stub_metadata, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ocr_extractor, "reader", lambda image: _ocr_result(("blur", 0.2))
    )
    result = ingest_document(_png_bytes(), "blur.png", "image/jpeg")
    assert result["needs_review"] is True


def test_ingest_empty_ocr_text_needs_review(
    _stub_metadata, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocr_extractor, "reader", lambda image: _ocr_result())
    result = ingest_document(_png_bytes(), "blank.png", "image/png")
    assert result["raw_text"] == ""
    assert result["needs_review"] is True


def test_ingest_rejects_unsupported_type(_stub_metadata) -> None:
    with pytest.raises(ValueError, match="Unsupported document type"):
        ingest_document(b"{}", "notes.json", "application/json")
