"""
Where a document's text sits on its pages (app/text_lines.py), for boxing a cited value in the officer's
side-by-side compare view: read from a text PDF directly, with OCR for an image, and cached in storage per document.
"""
from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf
from PIL import Image

from app import text_lines
from app.ingestion import ocr_extractor
from app.models import DocumentRecord, DocumentStatus


def _pdf(*lines: tuple[float, float, str]) -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for x, y, text in lines:
        page.insert_text((x, y), text, fontsize=11)
    return document.tobytes()


def _png(width: int = 400, height: int = 200) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _document(storage_path: str = "case-1/doc-1_cert.pdf") -> DocumentRecord:
    return DocumentRecord(
        id="doc-1", case_id="case-1", filename="cert.pdf", status=DocumentStatus.DETECTED,
        uploaded_at="2026-10-01T00:00:00Z", storage_path=storage_path,
    )


def test_a_text_pdf_gives_each_line_with_its_box_in_points() -> None:
    pages = text_lines.extract_text_lines(_pdf((72, 100, "UEN: 201812345A"), (72, 140, "Incorporated 2018")), "application/pdf")

    assert len(pages) == 1
    page = pages[0]
    assert (page["page"], page["units"], page["size"]) == (1, "pt", [595, 842])
    assert [line["text"] for line in page["lines"]] == ["UEN: 201812345A", "Incorporated 2018"]
    x0, y0, x1, y1 = page["lines"][0]["bbox"]
    # From the top-left, around where it was written (the baseline at y=100).
    assert 70 <= x0 < x1 and y0 < 100 <= y1 + 3 and y1 < 140


def test_an_image_is_read_with_ocr_and_each_line_keeps_its_box_in_pixels(monkeypatch) -> None:
    ocr = SimpleNamespace(
        boxes=[[[10, 20], [150, 20], [150, 40], [10, 40]]], txts=["UEN 201812345A"], scores=[0.98],
    )
    monkeypatch.setattr(ocr_extractor, "reader", lambda image: ocr)

    pages = text_lines.extract_text_lines(_png(), "image/png")

    assert pages == [{"page": 1, "units": "px", "size": [400, 200], "lines": [{"text": "UEN 201812345A", "bbox": [10.0, 20.0, 150.0, 40.0]}]}]


def test_the_lines_are_worked_out_once_and_then_read_from_the_cache() -> None:
    saved: dict[str, bytes] = {}
    pdf = _pdf((72, 100, "UEN: 201812345A"))

    def download(path: str) -> bytes:
        if path in saved:
            return saved[path]
        if path == "case-1/doc-1_cert.pdf":
            return pdf
        raise FileNotFoundError(path)

    with patch.object(text_lines, "storage_service") as storage:
        storage.download_file.side_effect = download
        storage.save_json.side_effect = lambda path, data: saved.__setitem__(path, json.dumps(data).encode())

        first = text_lines.get_or_build(_document())
        second = text_lines.get_or_build(_document())

    assert first == second and first["pages"][0]["lines"][0]["text"] == "UEN: 201812345A"
    assert list(saved) == ["text-lines/doc-1.json"]
    # The file was read once: the second answer came from the cache.
    assert [c.args[0] for c in storage.download_file.call_args_list].count("case-1/doc-1_cert.pdf") == 1


def test_a_cache_made_for_another_file_is_rebuilt() -> None:
    stale = {"version": text_lines.VERSION, "storage_path": "case-1/old.pdf", "pages": [{"page": 1, "lines": []}]}

    with patch.object(text_lines, "storage_service") as storage:
        storage.download_file.side_effect = lambda path: (
            json.dumps(stale).encode() if path.startswith("text-lines/") else _pdf((72, 100, "New text"))
        )
        result = text_lines.get_or_build(_document())

    assert result["pages"][0]["lines"][0]["text"] == "New text"
    storage.save_json.assert_called_once()


def test_a_failed_cache_write_still_answers() -> None:
    with patch.object(text_lines, "storage_service") as storage:
        storage.download_file.side_effect = lambda path: (_ for _ in ()).throw(FileNotFoundError(path)) if path.startswith("text-lines/") else _pdf((72, 100, "Text"))
        storage.save_json.side_effect = RuntimeError("bucket unreachable")
        result = text_lines.get_or_build(_document())

    assert result["pages"][0]["lines"][0]["text"] == "Text"
