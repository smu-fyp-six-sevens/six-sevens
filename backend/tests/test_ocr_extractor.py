"""OCR's lookalike characters in a passport's machine-readable zone are put back to plain ASCII (PENDING_TESTS row 73).

Seen live: RapidOCR read the MRZ's "U" as "∪" and its "<" fillers as lookalikes, so the MRZ checks (ID.MRZ_FORMAT,
ID.MRZ_CHECKDIGITS) failed a genuine passport.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.ingestion import ocr_extractor
from app.ingestion.ocr_extractor import _fix_mrz, extract_image_with_ocr

# Two genuine 44-character TD3 lines (a synthetic passport's).
LINE1 = "PASGPSIM<<TECK<HUAT<<<<<<<<<<<<<<<<<<<<<<<<<"
LINE2 = "E2496105E9SGP8601230F3012246S8651228I<<<<<22"


def test_the_lines_are_44_characters() -> None:
    assert len(LINE1) == 44 and len(LINE2) == 44


@pytest.mark.parametrize("lookalike", ["∪", "ᴜ"])
def test_a_lookalike_u_inside_an_mrz_line_is_read_as_u(lookalike: str) -> None:
    assert _fix_mrz(LINE1.replace("HUAT", f"H{lookalike}AT")) == LINE1


@pytest.mark.parametrize("lookalike", ["Λ", "‹", "«"])
def test_lookalike_fillers_inside_an_mrz_line_are_read_as_filler(lookalike: str) -> None:
    misread = LINE1[:20] + lookalike * 10 + LINE1[30:]
    assert _fix_mrz(misread) == LINE1


def test_both_lines_are_fixed_in_one_string() -> None:
    assert _fix_mrz(f"{LINE1.replace('U', '∪')} {LINE2.replace('<', '‹')}") == f"{LINE1} {LINE2}"


def test_a_run_with_fewer_than_two_fillers_is_left_alone() -> None:
    # MRZ-length and MRZ characters, but only one filler: not an MRZ line, so nothing is changed.
    run = "ABCDEFGHIJ∪KLMNOPQRSTUVWXYZ0123456789<"
    assert len(run) >= 28 and run.count("<") == 1
    assert _fix_mrz(run) == run


def test_a_short_run_is_left_alone() -> None:
    short = "P<SGP∪<<"
    assert _fix_mrz(short) == short


@pytest.mark.parametrize("text", [
    "Sets A ∪ B and A ∩ B",
    "« Quoted » text and ‹single› quotes",
    "Λ is lambda; ᴜ is a small capital",
])
def test_ordinary_text_with_those_symbols_is_left_alone(text: str) -> None:
    assert _fix_mrz(text) == text


def test_ocr_output_carries_the_fixed_mrz_in_the_element_and_the_page_text(monkeypatch: pytest.MonkeyPatch) -> None:
    misread = LINE1.replace("HUAT", "H∪AT")
    stub = SimpleNamespace(
        boxes=[[[0, 0], [400, 0], [400, 20], [0, 20]], [[0, 30], [400, 30], [400, 50], [0, 50]]],
        txts=[misread, LINE2], scores=[0.98, 0.97],
    )
    monkeypatch.setattr(ocr_extractor, "reader", lambda image: stub)

    page = extract_image_with_ocr(np.zeros((60, 420, 3), dtype=np.uint8))

    assert [e["text"] for e in page["elements"]] == [LINE1, LINE2]
    assert page["text"] == f"{LINE1} {LINE2}"
    assert "∪" not in page["text"]
