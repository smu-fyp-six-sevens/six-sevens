# tests/test_extraction_parsing.py
from app.orchestrator import _parse_extraction_output


def test_clean_json():
    output = '{"extracted_fields": {"a": 1}, "confidence": 0.9, "escalation": null}'
    result = _parse_extraction_output(output)
    assert result["extracted_fields"] == {"a": 1}
    assert result["confidence"] == 0.9
    assert result["escalation"] is None


def test_json_wrapped_in_markdown_fences():
    output = '```json\n{"extracted_fields": {"a": 1}, "confidence": 0.8, "escalation": null}\n```'
    result = _parse_extraction_output(output)
    assert result["extracted_fields"] == {"a": 1}


def test_malformed_json_returns_escalation():
    output = "Sorry, I cannot process this document."
    result = _parse_extraction_output(output)
    assert result["extracted_fields"] == {}
    assert result["escalation"] is not None


def test_escalation_present():
    output = '{"extracted_fields": {}, "confidence": 0.0, "escalation": {"reason": "looks like a bank statement"}}'
    result = _parse_extraction_output(output)
    assert result["escalation"]["reason"] == "looks like a bank statement"

def test_reasoning_model_think_block_is_stripped():
    output = '<think>some reasoning</think>{"extracted_fields": {"individual_name": "Jane Tan"}, "confidence": 0.98, "escalation": null}'
    result = _parse_extraction_output(output)
    assert result["extracted_fields"] == {"individual_name": "Jane Tan"}