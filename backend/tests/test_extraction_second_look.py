from __future__ import annotations

from langchain_core.messages import ToolMessage

from app.langgraph.extract import _merge_second_look, select_second_look_fields
from app.langgraph.extract_graph import _refusal, _vision_call_refusal
from app.langgraph.extraction_tools import layout_lookup, vision_read


def test_second_look_selects_low_confidence_missing_required_and_guard_dropped_fields() -> None:
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


def test_second_look_fills_missing_field_and_only_raises_confidence_when_same() -> None:
    result = {
        "extracted_fields": {"name": "JANE TAN", "account_number": None},
        "field_confidences": {"name": 0.5, "account_number": 0.0},
        "field_reasons": {},
        "field_provenance": {"name": {"source": "text", "confidence": 0.5}},
    }
    selected = [{"name": "name"}, {"name": "account_number"}]
    review = {
        "_tools_used": {"layout_lookup": True, "vision_read": False},
        "fields": {
            "name": {"value": "Jane Tan", "confidence": 0.98, "reason": "Label and value are adjacent.",
                     "source": "layout_lookup"},
            "account_number": {"value": "123456", "confidence": 0.94, "reason": "Visible next to label.",
                               "source": "layout_lookup"},
        },
    }

    _merge_second_look(
        result, review, selected, "Account number 123456", native_text_pdf=True,
    )

    assert result["extracted_fields"] == {"name": "JANE TAN", "account_number": "123456"}
    assert result["field_confidences"]["name"] == 0.98
    assert result["field_confidences"]["account_number"] == 0.94
    assert result["field_provenance"]["name"]["source"] == "text"
    assert result["field_provenance"]["name"]["corroborated_by"] == ["layout_lookup"]
    assert result["field_provenance"]["account_number"]["source"] == "layout"


def test_native_pdf_visual_disagreement_is_kept_and_reported() -> None:
    result = {
        "extracted_fields": {"account_number": "123456"},
        "field_confidences": {"account_number": 0.81},
        "field_reasons": {},
        "field_provenance": {"account_number": {"source": "text", "confidence": 0.81}},
    }
    review = {
        "_tools_used": {"layout_lookup": True, "vision_read": True},
        "_vision_pages": [2],
        "fields": {
            "account_number": {"value": "123458", "confidence": 0.95, "reason": "The image reads 123458.",
                               "source": "vision_read"},
        },
    }

    _merge_second_look(
        result, review, [{"name": "account_number"}], "Account number 123456", native_text_pdf=True,
    )

    assert result["extracted_fields"]["account_number"] == "123456"
    assert result["extraction_disagreements"] == [{
        "field": "account_number", "text_value": "123456", "visual_value": "123458",
        "visual_confidence": 0.95, "pages": [2], "reason": "The image reads 123458.",
    }]
    assert result["field_confidences"]["account_number"] == 0.81


def test_native_pdf_vision_cannot_add_a_number_missing_from_text() -> None:
    result = {
        "extracted_fields": {}, "field_confidences": {}, "field_reasons": {}, "field_provenance": {},
    }
    review = {
        "_tools_used": {"layout_lookup": True, "vision_read": True},
        "fields": {"available_balance": {"value": "SGD 999.00", "confidence": 0.97,
                                            "source": "vision_read"}},
    }
    _merge_second_look(
        result, review, [{"name": "available_balance"}], "Available balance: not legible", native_text_pdf=True,
    )
    assert "available_balance" not in result["extracted_fields"]
    assert result["second_look_rejected_fields"][0]["field"] == "available_balance"


def test_image_upload_can_fill_missing_field_from_visual_read() -> None:
    result = {
        "extracted_fields": {}, "field_confidences": {}, "field_reasons": {}, "field_provenance": {},
    }
    review = {
        "_tools_used": {"layout_lookup": False, "vision_read": True},
        "_vision_pages": [1],
        "fields": {"nric_number": {"value": "S1234567D", "confidence": 0.91,
                                    "source": "vision_read", "reason": "Visible on the card."}},
    }

    _merge_second_look(
        result, review, [{"name": "nric_number"}], "", native_text_pdf=False,
    )

    assert result["extracted_fields"]["nric_number"] == "S1234567D"
    assert result["field_provenance"]["nric_number"]["source"] == "vision"
