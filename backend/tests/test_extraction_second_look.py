from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pymupdf
from langchain_core.messages import AIMessage, ToolMessage

from app.langgraph import extract, extract_graph, extraction_tools
from app.langgraph.extract import _merge_second_look, select_second_look_fields
from app.langgraph.extract_graph import _refusal, _vision_call_refusal
from app.langgraph.extraction_tools import (
    _layout_lookup_sync, _page_clips, _render_page, layout_evidence, layout_lookup, vision_read,
)


def _pdf(*lines: tuple[float, float, str]) -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for x, y, text in lines:
        page.insert_text((x, y), text)
    return document.tobytes()


def _lines_of(*lines: tuple[str, list[float]], page: int = 1, size=(595, 842)) -> list[dict]:
    return [{"page": page, "units": "pt", "size": list(size), "lines": [{"text": t, "bbox": b} for t, b in lines]}]


def _result(fields: dict, confidences: dict | None = None, **extra) -> dict:
    return {
        "extracted_fields": fields,
        "field_confidences": confidences or {},
        "field_reasons": {},
        "field_provenance": {},
        **extra,
    }


# --- 1.1: which fields get a second look ------------------------------------------------------------


def test_second_look_selects_low_confidence_missing_required_and_unprinted_fields() -> None:
    result = {
        "extracted_fields": {"bank_name": "DBS", "account_number": None, "opening_balance": "1,000.00"},
        "field_confidences": {"bank_name": 0.99, "account_number": 0.7, "opening_balance": 0.98},
        "field_reasons": {"account_number": "The number is faint."},
        "dropped_unprinted_fields": ["available_balance"],
        "dropped_empty_fields": [],
    }

    selected = select_second_look_fields("bank_statement", result)

    by_name = {item["name"]: item for item in selected}
    assert set(by_name) == {"account_number", "available_balance", "transactions"}
    assert by_name["account_number"]["triggers"] == ["low_confidence", "missing_required"]
    assert by_name["available_balance"]["triggers"] == ["guard_dropped"]
    assert by_name["transactions"]["triggers"] == ["missing_required"]
    assert by_name["account_number"]["reason"] == "The number is faint."


def test_second_look_skips_clean_fields_without_extra_cost() -> None:
    result = {
        "extracted_fields": {"bank_name": "DBS", "account_number": "123456", "transactions": []},
        "field_confidences": {"bank_name": 0.99, "account_number": 0.98, "transactions": 0.99},
        "field_reasons": {},
    }
    assert select_second_look_fields("bank_statement", result) == []


def _clean_statement(**dropped) -> dict:
    return {
        "extracted_fields": {"bank_name": "DBS", "account_number": "123456", "transactions": []},
        "field_confidences": {"bank_name": 0.99, "account_number": 0.98, "transactions": 0.99},
        "field_reasons": {},
        **dropped,
    }


def test_an_optional_field_that_is_not_printed_does_not_trigger_a_look() -> None:
    assert select_second_look_fields("bank_statement", _clean_statement(dropped_empty_fields=["available_balance"])) == []


def test_deliberately_dropped_values_are_not_looked_at_again() -> None:
    result = _clean_statement(dropped_period_totals=["total_credits"], dropped_example_values=["credit_score"])
    assert select_second_look_fields("bank_statement", result) == []


def test_confidence_missing_fires_only_for_a_field_without_a_confidence() -> None:
    result = _clean_statement()
    result["field_confidences"]["bank_name"] = None

    [selected] = select_second_look_fields("bank_statement", result)

    assert (selected["name"], selected["triggers"]) == ("bank_name", ["confidence_missing"])


# --- tool order -------------------------------------------------------------------------------------


def test_pdf_layout_must_precede_vision_but_image_can_go_straight_to_vision() -> None:
    assert _refusal(vision_read.name, [], is_image=False)
    assert _refusal(layout_lookup.name, [], is_image=True)
    layout_result = ToolMessage(content="{}", tool_call_id="layout-call", name=layout_lookup.name)
    assert _refusal(vision_read.name, [layout_result], is_image=False) is None
    vision_result = ToolMessage(content="{}", tool_call_id="vision-call", name=vision_read.name)
    assert _refusal(vision_read.name, [layout_result, vision_result], is_image=False)


def test_pdf_vision_is_limited_to_layout_fields_and_pages() -> None:
    layout_result = ToolMessage(
        content='{"fields":{"account_number":[{"page":2,"text":"Account number"}]}}',
        tool_call_id="layout-call",
        name=layout_lookup.name,
    )
    call = {"args": {"fields": ["account_number"], "pages": [2]}}
    assert _vision_call_refusal(call, [layout_result]) is None
    assert _vision_call_refusal({"args": {"fields": ["account_number"], "pages": [1]}}, [layout_result])
    assert _vision_call_refusal({"args": {"fields": ["bank_name"], "pages": [2]}}, [layout_result])


def test_a_refused_second_layout_call_does_not_hide_the_first() -> None:
    first = ToolMessage(content='{"fields":{"uen":[]}}', tool_call_id="a", name=layout_lookup.name)
    refused = ToolMessage(content="Not run: layout_lookup has already run once.", tool_call_id="b", name=layout_lookup.name)
    assert layout_evidence([first, refused]) == {"fields": {"uen": []}}


# --- 1.2: the safeguards stay ----------------------------------------------------------------------


def _layout_review(name: str, value, confidence: float = 0.97) -> dict:
    return {
        "_tools_used": {"layout_lookup": True, "vision_read": False},
        "fields": {name: {"value": value, "confidence": confidence, "source": "layout_lookup"}},
    }


def test_a_period_total_or_sample_value_is_never_put_back() -> None:
    for key in ("dropped_period_totals", "dropped_example_values"):
        result = _result({}, **{key: ["total_credits"]})
        _merge_second_look(
            result, _layout_review("total_credits", "1,000.00"), [{"name": "total_credits"}], "Total 1,000.00", native_text_pdf=True,
        )
        assert "total_credits" not in result["extracted_fields"], key


def test_a_layout_value_for_an_unprinted_field_must_be_printed() -> None:
    result = _result({}, dropped_unprinted_fields=["net_pay"])
    _merge_second_look(result, _layout_review("net_pay", "4,000.00"), [{"name": "net_pay"}], "Net Pay 3,500.00", native_text_pdf=False)
    assert "net_pay" not in result["extracted_fields"]
    assert result["second_look_rejected_fields"][0]["field"] == "net_pay"

    result = _result({}, dropped_unprinted_fields=["net_pay"])
    _merge_second_look(result, _layout_review("net_pay", "3,500.00"), [{"name": "net_pay"}], "Net Pay 3,500.00", native_text_pdf=False)
    assert result["extracted_fields"]["net_pay"] == "3,500.00"


# --- 1.3: a disagreement goes to the officer -------------------------------------------------------


def test_a_visual_disagreement_keeps_the_text_and_asks_the_officer() -> None:
    result = _result({"account_number": "123456"}, {"account_number": 0.81})
    review = {
        "_tools_used": {"layout_lookup": True, "vision_read": True},
        "_vision_pages": [2],
        "fields": {
            "account_number": {"value": "123458", "confidence": 0.95, "reason": "The image reads 123458.",
                               "source": "vision_read"},
        },
    }

    _merge_second_look(result, review, [{"name": "account_number"}], "Account number 123456", native_text_pdf=True)

    assert result["extracted_fields"]["account_number"] == "123456"
    assert result["extraction_disagreements"] == [{
        "field": "account_number", "text_value": "123456", "second_look_value": "123458", "source": "vision_read",
        "second_look_confidence": 0.95, "pages": [2], "reason": "The image reads 123458.",
    }]
    assert result["field_confidences"]["account_number"] == extract._DISAGREEMENT_CONFIDENCE
    assert result["field_confidences"]["account_number"] < extract.settings.EXTRACTION_CONFIDENCE_THRESHOLD
    assert result["field_reasons"]["account_number"] == (
        "The text reads 123456 but the page image reads 123458. Check the original page."
    )


def test_a_layout_disagreement_and_a_scan_are_treated_the_same() -> None:
    result = _result({"net_pay": "900.00"}, {"net_pay": 0.95})
    review = _layout_review("net_pay", "990.00")
    review["_layout_fields"] = {"net_pay": [{"page": 3, "text": "Net Pay 990.00"}]}

    _merge_second_look(result, review, [{"name": "net_pay"}], "Net Pay 900.00", native_text_pdf=False)

    assert result["extracted_fields"]["net_pay"] == "900.00"
    assert result["field_confidences"]["net_pay"] == extract._DISAGREEMENT_CONFIDENCE
    assert "the page layout reads 990.00" in result["field_reasons"]["net_pay"]
    assert result["extraction_disagreements"][0]["pages"] == [3]


def test_formatting_alone_is_not_a_disagreement() -> None:
    for current, value in [(1000, "1,000.00"), ("SGD 1,000.00", "1000"), ("Jane  Tan", "JANE TAN"), ("S1234567-D", "S1234567D")]:
        result = _result({"x": current}, {"x": 0.95})
        _merge_second_look(result, _layout_review("x", value), [{"name": "x"}], "", native_text_pdf=False)
        assert "extraction_disagreements" not in result, (current, value)


def test_a_different_figure_is_a_disagreement_even_with_the_same_digits_nearby() -> None:
    result = _result({"x": "1,000.00"}, {"x": 0.95})
    _merge_second_look(result, _layout_review("x", "100.00"), [{"name": "x"}], "", native_text_pdf=False)
    assert result["extraction_disagreements"][0]["field"] == "x"


# --- 1.4: corroboration needs something code can see -----------------------------------------------


def test_second_look_fills_a_missing_field() -> None:
    result = _result({"account_number": None}, {"account_number": 0.0})
    _merge_second_look(
        result, _layout_review("account_number", "123456", 0.94), [{"name": "account_number"}], "Account number 123456",
        native_text_pdf=True,
    )
    assert result["extracted_fields"]["account_number"] == "123456"
    assert result["field_confidences"]["account_number"] == 0.94
    assert result["field_provenance"]["account_number"]["source"] == "layout"


def test_a_corroborated_value_seen_in_the_layout_lines_raises_confidence() -> None:
    result = _result({"name": "JANE TAN"}, {"name": 0.5})
    review = _layout_review("name", "Jane Tan", 0.98)
    review["_layout_fields"] = {"name": [{"page": 1, "text": "Name: Jane Tan"}]}

    _merge_second_look(result, review, [{"name": "name"}], "", native_text_pdf=True)

    assert result["field_confidences"]["name"] == 0.98
    assert result["field_provenance"]["name"] == {"source": "text", "confidence": 0.98, "corroborated_by": ["layout_lookup"]}


def test_the_models_word_alone_does_not_raise_confidence() -> None:
    result = _result({"name": "JANE TAN"}, {"name": 0.5})
    result["field_reasons"]["name"] = "Faint."
    review = _layout_review("name", "Jane Tan", 0.98)
    review["fields"]["name"]["reason"] = "Looks right."
    review["_layout_fields"] = {"name": [{"page": 1, "text": "Applicant details"}]}

    _merge_second_look(result, review, [{"name": "name"}], "", native_text_pdf=True)

    assert result["field_confidences"]["name"] == 0.5
    assert result["field_reasons"]["name"] == "Faint."
    assert result["field_provenance"]["name"]["corroborated_by"] == ["layout_lookup"]


def test_corroboration_matches_whole_values_and_formatted_figures() -> None:
    assert extract._seen_in_lines("1000.00", [{"text": "Net pay S$1,000.00"}])
    assert not extract._seen_in_lines("1", [{"text": "Page 21 of 30"}])
    assert not extract._seen_in_lines("TAN", [{"text": "Tanjong Pagar"}])
    assert extract._seen_in_lines("Jane Tan", [{"text": "Name: JANE"}, {"text": "TAN"}])


def test_native_pdf_vision_cannot_add_a_number_missing_from_text() -> None:
    result = _result({})
    review = {
        "_tools_used": {"layout_lookup": True, "vision_read": True},
        "fields": {"available_balance": {"value": "SGD 999.00", "confidence": 0.97, "source": "vision_read"}},
    }
    _merge_second_look(result, review, [{"name": "available_balance"}], "Available balance: not legible", native_text_pdf=True)
    assert "available_balance" not in result["extracted_fields"]
    assert result["second_look_rejected_fields"][0]["field"] == "available_balance"


def test_image_upload_can_fill_missing_field_from_visual_read() -> None:
    result = _result({})
    review = {
        "_tools_used": {"layout_lookup": False, "vision_read": True},
        "_vision_pages": [1],
        "fields": {"nric_number": {"value": "S1234567D", "confidence": 0.91, "source": "vision_read",
                                   "reason": "Visible on the card."}},
    }

    _merge_second_look(result, review, [{"name": "nric_number"}], "", native_text_pdf=False)

    assert result["extracted_fields"]["nric_number"] == "S1234567D"
    assert result["field_provenance"]["nric_number"]["source"] == "vision"


# --- 1.6 and 1.7: the layout lookup and the crop ----------------------------------------------------


def test_layout_lookup_returns_each_table_once_however_many_fields_ask() -> None:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for x in (72, 200, 328):
        page.draw_line((x, 72), (x, 172))
    for y in (72, 122, 172):
        page.draw_line((72, y), (328, y))
    page.insert_text((80, 100), "Net Pay")
    page.insert_text((210, 100), "3,500.00")
    page.insert_text((80, 150), "Bank")
    page.insert_text((210, 150), "DBS")
    file_bytes = document.tobytes()
    pages = _lines_of(("Net Pay", [80, 90, 120, 104]), ("3,500.00", [210, 90, 250, 104]), ("Bank", [80, 140, 110, 154]))

    result = _layout_lookup_sync(file_bytes, pages, ["net_pay", "bank_name"])

    assert len(result["table_rows"]) == 1
    assert {line["text"] for line in result["fields"]["net_pay"]} >= {"Net Pay", "3,500.00"}
    assert result["fields"]["bank_name"]


def test_layout_lookup_reads_a_value_in_the_same_row_far_from_its_label() -> None:
    lines = [(f"Row {i}", [72.0, 300.0 + i * 20, 120.0, 312.0 + i * 20]) for i in range(8)]
    pages = _lines_of(("Net Pay", [72, 100, 120, 112]), *lines, ("3,500.00", [400, 101, 450, 111]))

    result = _layout_lookup_sync(_pdf((72, 100, "Net Pay")), pages, ["net_pay"])

    assert "3,500.00" in {line["text"] for line in result["fields"]["net_pay"]}


def test_a_scans_pixel_boxes_are_converted_to_points() -> None:
    pages = [{"page": 1, "units": "px", "size": [1190, 1684], "lines": [{"text": "Net Pay 1", "bbox": [200.0, 400.0, 400.0, 440.0]}]}]

    result = _layout_lookup_sync(_pdf((72, 100, "x")), pages, ["net_pay"])

    assert result["fields"]["net_pay"][0]["bbox"] == [100.0, 200.0, 200.0, 220.0]


def test_a_field_with_no_label_falls_back_and_is_marked() -> None:
    pages = _lines_of(("Statement of account", [72, 60, 300, 74]))

    result = _layout_lookup_sync(_pdf((72, 70, "Statement of account")), pages, ["net_pay"])

    assert result["fields"]["net_pay"][0]["fallback"] is True


def test_clips_cover_the_fields_lines_and_leave_unplaced_pages_whole() -> None:
    evidence = {"fields": {
        "a": [{"page": 1, "bbox": [10, 20, 30, 40]}, {"page": 1, "bbox": [50, 60, 70, 80]}],
        "b": [{"page": 2, "text": "header", "bbox": [1, 2, 3, 4], "fallback": True}],
        "c": [{"page": 3, "text": "no box"}],
    }}

    assert _page_clips(evidence, ["a", "b", "c"]) == {1: [10, 20, 70, 80]}
    assert _page_clips(None, ["a"]) == {}


def _rendered_size(block: dict) -> tuple[int, int]:
    import base64
    import io

    from PIL import Image

    return Image.open(io.BytesIO(base64.b64decode(block["source"]["data"]))).size


def test_a_crop_is_smaller_than_the_page_and_falls_back_when_outside_it() -> None:
    document = pymupdf.open(stream=_pdf((72, 100, "Net Pay")), filetype="pdf")
    page = document[0]
    whole = _rendered_size(_render_page(page))
    crop = _rendered_size(_render_page(page, [72, 90, 200, 110]))
    outside = _rendered_size(_render_page(page, [2000, 2000, 2100, 2100]))

    assert crop[0] * crop[1] < whole[0] * whole[1] / 4
    assert outside == whole


async def test_the_graph_injects_state_and_crops_vision_to_the_layout_lines() -> None:
    file_bytes = _pdf((72, 100, "Net Pay 3,500.00"))
    pages = _lines_of(("Net Pay 3,500.00", [72, 90, 200, 104]))

    async def direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    final = '{"fields":{"net_pay":{"value":"3,500.00","confidence":0.97,"reason":"seen","source":"layout_lookup"}}}'
    model = MagicMock()
    model.bind_tools.return_value = model
    model.ainvoke = AsyncMock(side_effect=[
        AIMessage(content="", tool_calls=[{"id": "l1", "name": "layout_lookup", "args": {"fields": ["net_pay"]}}]),
        AIMessage(content="", tool_calls=[{"id": "v1", "name": "vision_read", "args": {"fields": ["net_pay"], "pages": [1]}}]),
        AIMessage(content='{"fields":{"net_pay":{"value":"3,500.00","confidence":0.9,"reason":"seen"}}}'),
        AIMessage(content=final),
    ])
    recorded: list[tuple[str, dict]] = []
    with (
        patch.object(extraction_tools.text_lines, "pages_for", AsyncMock(return_value=pages)),
        patch.object(extraction_tools, "run_cpu", direct),
        patch.object(extraction_tools.trace, "record", side_effect=lambda _d, step, **kw: recorded.append((step, kw))),
        patch.object(extraction_tools, "spend_model_call"),
        patch.object(extract_graph, "spend_model_call"),
        patch("app.langgraph._anthropic.build_chat_anthropic", return_value=model),
    ):
        review = await extract_graph.run_second_look(
            document_id="d", filename="a.pdf", storage_path="c/a.pdf", file_bytes=file_bytes, raw_text="Net Pay 3,500.00",
            selected_fields=[{"name": "net_pay"}], is_pdf=True, native_text_pdf=True,
        )

    assert review["_tools_used"] == {"layout_lookup": True, "vision_read": True}
    assert review["_vision_pages"] == [1]
    assert review["_layout_fields"]["net_pay"][0]["text"] == "Net Pay 3,500.00"
    vision_step = next(kw for step, kw in recorded if step == "extraction_vision_read")
    assert vision_step["cropped_pages"] == [1]


# --- 1.8: the time limit ---------------------------------------------------------------------------


async def test_a_second_look_past_its_time_limit_leaves_the_text_pass() -> None:
    async def hangs(**_kwargs):
        await asyncio.sleep(60)

    async def direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    result = _result({"net_pay": "900.00"}, {"net_pay": 0.5}, second_look={"fields": [], "tools_used": []})
    with (
        patch.object(extract.settings, "EXTRACTION_SECOND_LOOK_TIMEOUT_SECONDS", 0.05),
        patch("app.storage_service.download_file", return_value=b"%PDF"),
        patch("app.cpu_pool.run_cpu", direct),
        patch("app.ingestion.pdf_extractor.pdf_contains_text", return_value=True),
        patch("app.langgraph.extract_graph.run_second_look", hangs),
    ):
        await extract._run_second_look(result, [{"name": "net_pay"}], "Net Pay 900.00", "d", "a.pdf", "c/a.pdf")

    assert result["extracted_fields"] == {"net_pay": "900.00"}
    assert result["field_confidences"] == {"net_pay": 0.5}
    assert result["second_look_error"].startswith("TimeoutError")


async def test_the_pdf_decision_is_made_once_and_passed_through() -> None:
    seen: dict = {}

    async def review(**kwargs):
        seen.update(kwargs)
        return {"fields": {}, "_tools_used": {"layout_lookup": False, "vision_read": True}}

    async def direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    result = _result({}, second_look={"fields": [], "tools_used": []})
    with (
        patch("app.storage_service.download_file", return_value=b"png-bytes"),
        patch("app.cpu_pool.run_cpu", direct),
        patch("app.ingestion.pdf_extractor.pdf_contains_text", side_effect=AssertionError("not a PDF")),
        patch("app.langgraph.extract_graph.run_second_look", review),
    ):
        await extract._run_second_look(result, [{"name": "x"}], "", "d", "photo.png", "c/photo.png")

    assert (seen["is_pdf"], seen["native_text_pdf"]) == (False, False)
    assert result["second_look"]["tools_used"] == ["vision_read"]


# --- section 6: the statement table read in code --------------------------------------------------


def _statement(*rows: tuple, header=("Date", "Details", "Debit", "Credit", "Balance")) -> bytes:
    """A made-up layout: columns at positions no generator uses."""
    x = (60, 130, 330, 410, 490)
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for left, title in zip(x, header):
        page.insert_text((left, 100), title, fontsize=9)
    y = 120
    for row in rows:
        for left, cell in zip(x, row):
            if cell:
                page.insert_text((left, y), cell, fontsize=9)
        y += 11
    return document.tobytes()


def test_a_statement_is_read_from_its_own_header() -> None:
    from app.langgraph.statement_table import read_transactions

    parsed = read_transactions(_statement(
        ("", "Opening balance", "", "", "100.00"),
        ("01/06/2026", "Cheque deposit", "", "50.00", "150.00"),
        ("02/06/2026", "Payment to", "20.00", "", "130.00"),
        ("", "ACME PTE LTD", "", "", ""),
        ("03/06/2026", "Overdrawn", "1,130.00", "", "-1,000.00"),
    ))

    assert parsed["rows"] == [
        {"date": None, "amount": None, "balance": 100.0, "description": "Opening balance", "counterparty": None},
        {"date": "01/06/2026", "amount": 50.0, "balance": 150.0, "description": "Cheque deposit", "counterparty": None},
        {"date": "02/06/2026", "amount": -20.0, "balance": 130.0, "description": "Payment to ACME PTE LTD", "counterparty": None},
        {"date": "03/06/2026", "amount": -1130.0, "balance": -1000.0, "description": "Overdrawn", "counterparty": None},
    ]


def test_a_table_that_does_not_add_up_is_still_accepted() -> None:
    from app.langgraph.statement_table import read_transactions

    parsed = read_transactions(_statement(
        ("01/06/2026", "One", "", "50.00", "150.00"), ("02/06/2026", "Two", "", "50.00", "999.00"),
    ))

    assert [row["balance"] for row in parsed["rows"]] == [150.0, 999.0]


def test_an_unclear_table_is_rejected_whole() -> None:
    from app.langgraph.statement_table import read_transactions

    no_date = _statement(("", "Fee", "5.00", "", "95.00"))
    both = _statement(("01/06/2026", "Odd", "5.00", "6.00", "95.00"))
    marker = _statement(("01/06/2026", "Odd", "5.00", "", "95.00 DR"))
    no_header = _statement(("01/06/2026", "One", "", "50.00", "150.00"), header=("Date", "Details", "Out", "In", "Total"))

    for pdf in (no_date, both, marker, no_header):
        assert "rejected" in read_transactions(pdf)


async def test_a_scan_or_an_image_keeps_the_usual_extraction() -> None:
    async def direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    assert (await extract._read_statement_table("bank_statement", "a.png", "c/a.png"))["trace"]["reason"] == "not a PDF"
    with (
        patch("app.storage_service.download_file", return_value=b"%PDF"),
        patch("app.cpu_pool.run_cpu", direct),
        patch("app.ingestion.pdf_extractor.pdf_contains_text", return_value=False),
    ):
        scanned = await extract._read_statement_table("bank_statement", "a.pdf", "c/a.pdf")
    assert "rows" not in scanned and scanned["trace"]["reason"] == "no text layer"
    assert await extract._read_statement_table("payslip", "a.pdf", "c/a.pdf") == {}


def test_the_prompt_leaves_the_table_to_code_and_still_asks_for_the_rest() -> None:
    from app.langgraph.extraction_prompts import build_extraction_prompt

    usual = build_extraction_prompt("bank_statement", "")
    read = build_extraction_prompt("bank_statement", "", transactions_read=True)

    assert "- transactions" in usual and "- transactions" not in read
    assert "- account_number" in read and "- statement_period_start" in read
    assert "transaction_counterparties" in read and "transaction_counterparties" not in usual
    assert "without reference numbers" in usual and "without reference numbers" in read


def test_the_rows_take_the_counterparties_the_model_named() -> None:
    rows = [
        {"date": None, "amount": None, "balance": 1.0, "description": "Brought forward", "counterparty": None},
        {"date": "d", "amount": -1.0, "balance": 0.0, "description": "PAY ACME", "counterparty": None},
        {"date": "d", "amount": -1.0, "balance": -1.0, "description": "PAY ACME", "counterparty": None},
        {"date": "d", "amount": -1.0, "balance": -2.0, "description": "BANK FEE", "counterparty": None},
    ]
    result = _result({"bank_name": "DBS", "transaction_counterparties": {"1": "ACME"}},
                     {"bank_name": 0.99, "transaction_counterparties": 0.9})

    extract._apply_statement_table(result, rows, ["PAY ACME", "BANK FEE"])

    assert [row["counterparty"] for row in result["extracted_fields"]["transactions"]] == [None, "ACME", "ACME", None]
    assert "transaction_counterparties" not in result["extracted_fields"]
    assert "transaction_counterparties" not in result["field_confidences"]
    assert result["field_confidences"]["transactions"] == extract._TABLE_CONFIDENCE
