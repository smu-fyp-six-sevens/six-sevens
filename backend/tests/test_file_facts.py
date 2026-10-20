"""The facts about a file that the lead and the specialists read before they choose checks."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

import pymupdf
from PIL import Image

from app.fraud_detection.shared.file_facts import describe_file, render_file_facts
from app.langgraph.detection import specialists, tools


def _png(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf(*, images: list[tuple[int, int]] = (), text: str = "Hello world, this is a statement.", pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((50, 80), text)
    for index, (width, height) in enumerate(images):
        doc[0].insert_image(pymupdf.Rect(50, 120 + index * 80, 130, 190 + index * 80), stream=_png(width, height))
    data = doc.tobytes()
    doc.close()
    return data


def test_a_text_only_pdf_has_no_images() -> None:
    facts = describe_file(_pdf())

    assert facts == {
        "kind": "pdf", "pages": 1, "selectable_text": True, "image_placements": 0, "distinct_images": 0, "images": [],
    }
    assert render_file_facts(facts) == "a PDF of 1 page(s) that has selectable text. It has no embedded images."


def test_embedded_images_are_listed_with_their_size() -> None:
    facts = describe_file(_pdf(images=[(1280, 382)]))

    assert facts is not None and facts["images"] == [{"width": 1280, "height": 382}]
    assert "1 distinct embedded image(s)" in render_file_facts(facts) and "1280x382 px" in render_file_facts(facts)


def test_the_same_image_placed_twice_is_one_distinct_image() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    image = _png(200, 100)
    xref = page.insert_image(pymupdf.Rect(50, 50, 150, 100), stream=image)
    page.insert_image(pymupdf.Rect(50, 200, 150, 250), xref=xref)
    facts = describe_file(doc.tobytes())
    doc.close()

    assert facts is not None and facts["distinct_images"] == 1 and facts["image_placements"] == 2


def test_a_pdf_without_a_text_layer_says_so() -> None:
    facts = describe_file(_pdf(text="", images=[(600, 800)]))

    assert facts is not None and facts["selectable_text"] is False
    assert "has no selectable text" in render_file_facts(facts)


def test_an_image_file_is_described_as_an_image() -> None:
    facts = describe_file(_png(318, 403))

    assert facts == {"kind": "image", "format": "PNG", "width": 318, "height": 403}
    assert render_file_facts(facts) == "an image file (PNG, 318x403 px) with no selectable text."


def test_a_file_that_is_neither_gives_no_facts() -> None:
    assert describe_file(b"not a file") is None
    assert render_file_facts(None) == "not available"


async def test_the_facts_are_read_once_per_run_and_a_failure_gives_none() -> None:
    tools.clear_context("doc-facts")
    with patch.object(tools, "_file_bytes", AsyncMock(return_value=_pdf())) as fetch:
        first = await tools.file_facts("doc-facts")
        second = await tools.file_facts("doc-facts")
    tools.clear_context("doc-facts")

    assert first == second and first is not None and fetch.await_count == 1

    with patch.object(tools, "_file_bytes", AsyncMock(side_effect=RuntimeError("storage down"))):
        assert await tools.file_facts("doc-facts-2") is None
    tools.clear_context("doc-facts-2")


def test_the_specialist_prompt_gives_the_contents_and_leaves_the_choice_to_the_agent() -> None:
    prompt = specialists._SPECIALIST_STATIC_PROMPT

    assert "File contents" in prompt and "at least two images" in prompt and "128 px" in prompt
    assert "your judgement, not a fixed checklist" in prompt
    assert "File contents: {file_contents}" in specialists._SPECIALIST_DYNAMIC_PROMPT
