"""
Extractor — one Sonnet call plus output parsing. Prompt building
(`build_extraction_prompt`) lives in extraction_prompts.py; parsing the
model's JSON into extracted fields/confidences lives here.
"""

from __future__ import annotations

import json
import logging
import re

from ..config import settings
from .extraction_prompts import build_extraction_prompt, drop_empty_optional_fields
from .. import telemetry

logger = logging.getLogger("classifier_pipeline.langgraph.extract")


def _split_prompt_for_caching(document_type: str, raw_text: str) -> tuple[str, str]:
    """
    build_extraction_prompt always ends with the schema/instructions
    followed by "Document text:\n{raw_text}". Building the same prompt with
    an empty raw_text gives a byte-stable prefix (same document_type -> same
    prefix across every document and run) to put cache_control on, per
    LANGGRAPH_MIGRATION_PLAN.md section 5 ("schema first, raw_text last").
    """
    prefix = build_extraction_prompt(document_type, "")
    return prefix, raw_text


async def run_extraction(document_type: str, raw_text: str) -> dict:
    from ._anthropic import build_chat_anthropic
    from .cache_gate import cache_gate

    prefix, text = _split_prompt_for_caching(document_type, raw_text)
    model = build_chat_anthropic(settings.LANGGRAPH_SONNET_MODEL, 4096)
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": text},
        ],
    }
    with telemetry.get_tracer().start_as_current_span("extract.run_extraction") as span:
        span.set_attribute("document_type", document_type)
        # Gated per document_type (the actual cache key — see
        # _split_prompt_for_caching): the first document of a given type in a
        # batch pays for the cache write, the rest of that type wait for it
        # rather than racing it. Different types never block each other.
        response = await cache_gate.run(f"extract:{document_type}", lambda: model.ainvoke([message]))
        telemetry.record_usage(span, response)
        llm_output = response.content if isinstance(response.content, str) else str(response.content)
        result = _drop_unprinted_numbers(parse_extraction_output(llm_output), raw_text)
        result = _drop_empty_fields(result, document_type)
        span.set_attribute("dropped_unprinted_fields", len(result.get("dropped_unprinted_fields", [])))
        return result


def parse_extraction_output(llm_output: str) -> dict:
    cleaned = llm_output.strip()

    # Strip reasoning-model <think>...</think> blocks (some models prepend
    # chain-of-thought before the actual JSON answer)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("Extraction LLM returned non-JSON output: %r", llm_output[:500])
        # The model's prose often still contains the real finding (e.g. "no
        # individual_name present on this document") even though it failed
        # to wrap that in the required JSON — surfacing an excerpt here
        # (rather than a fixed, contentless message) is what actually
        # reaches the reclassify pass and the eventual FAILED reason.
        excerpt = (cleaned or llm_output).strip()[:500]
        return {
            "extracted_fields": {},
            "field_confidences": {},
            "field_reasons": {},
            "confidence": 0.0,
            "escalation": {
                "reason": f"Extraction did not return valid JSON. Its response was: {excerpt}"
            },
        }

    raw_fields = parsed.get("extracted_fields", {})
    extracted_fields, field_confidences, field_reasons = _split_field_confidences(
        raw_fields if isinstance(raw_fields, dict) else {}
    )

    return {
        "extracted_fields": extracted_fields,
        "field_confidences": field_confidences,
        # Only for fields the agent itself scored low — see _FIELD_WRAPPER_INSTRUCTIONS.
        # Not persisted anywhere; surfaced only in this run's langgraph trace (nodes.py's extract_node).
        "field_reasons": field_reasons,
        # The document-level confidence is derived from the individual
        # fields' own confidences (averaged), not the LLM's separate
        # top-level self-report — a single overall number is easy for the
        # model to phone in without actually reasoning about each field,
        # whereas averaging the per-field numbers it already committed to
        # ties the document score to the same judgements it made per field.
        "confidence": _average_field_confidence(field_confidences),
        "escalation": parsed.get("escalation"),
    }


# A standalone figure in the document text: digits with optional thousands
# separators and decimals ("12,688", "1.00", "109,129.65").
_TEXT_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# A field value that is purely a figure, optionally with a currency prefix,
# a percent sign, or accounting-style parentheses ("SGD 12,688", "(35.00)").
_VALUE_NUMBER_RE = re.compile(
    r"^\(?-?\s*(?:SGD|USD|S\$|US\$|\$)?\s*(-?[\d,]*\d(?:\.\d+)?)\s*%?\)?$", re.IGNORECASE
)


def _as_figure(value) -> float | None:
    """The number a scalar field value represents, or None if it isn't a bare figure
    (dates, names, text, arrays, booleans)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return abs(float(value))
    if isinstance(value, str):
        match = _VALUE_NUMBER_RE.match(value.strip())
        if match:
            return abs(float(match.group(1).replace(",", "")))
    return None


def _drop_unprinted_numbers(result: dict, raw_text: str) -> dict:
    """
    Extraction is transcription only (see _FIELD_WRAPPER_INSTRUCTIONS), but the
    LLM will still happily compute a figure the document never prints — e.g.
    summing a three-month statement's per-month totals into total_credits.
    Enforced here rather than trusted to the prompt: any top-level numeric
    field whose figure doesn't appear anywhere in the document text is dropped.
    Sign is ignored, since debits are legitimately extracted as negatives.

    Arrays/objects (transactions, line items) are left alone — this only
    checks scalar figures. Skipped entirely when the text has no figures at
    all, so an empty/failed OCR doesn't wipe out every numeric field.
    """
    printed = {float(token.replace(",", "")) for token in _TEXT_NUMBER_RE.findall(raw_text or "")}
    if not printed:
        return result

    fields = result.get("extracted_fields") or {}
    dropped = []
    for name, value in list(fields.items()):
        figure = _as_figure(value)
        if figure is None:
            continue
        if not any(abs(figure - p) < 0.005 for p in printed):
            dropped.append(name)
            del fields[name]
            result.get("field_confidences", {}).pop(name, None)
            result.get("field_reasons", {}).pop(name, None)

    if dropped:
        logger.info("Dropped extracted fields not printed in the document: %s", dropped)
        result["confidence"] = _average_field_confidence(result.get("field_confidences", {}))
    result["dropped_unprinted_fields"] = dropped
    return result


def _drop_empty_fields(result: dict, document_type: str) -> dict:
    """
    An optional field the LLM returned as null ("not printed on this
    document") is left out rather than stored empty, where manual review
    would ask for a value that doesn't exist. See
    extraction_prompts.drop_empty_optional_fields.
    """
    dropped = drop_empty_optional_fields(document_type, result.get("extracted_fields") or {})
    confidences = result.get("field_confidences", {})
    for name in dropped:
        confidences.pop(name, None)
        result.get("field_reasons", {}).pop(name, None)
    if dropped:
        logger.info("Dropped empty optional fields: %s", dropped)
        # An empty field is usually one the LLM flagged as uncertain, so it
        # no longer drags the document's confidence down.
        if any(isinstance(v, (int, float)) for v in confidences.values()):
            result["confidence"] = _average_field_confidence(confidences)
    result["dropped_empty_fields"] = dropped
    return result


def _average_field_confidence(field_confidences: dict) -> float:
    values = [v for v in field_confidences.values() if isinstance(v, (int, float))]
    if not values:
        return 0.0
    return sum(values) / len(values)


def _split_field_confidences(raw_fields: dict) -> tuple[dict, dict, dict]:
    """
    The extraction prompt asks the LLM to wrap each field as
    {"value": ..., "confidence": <float>, "low_confidence_reason": <str, only when
    confidence is low>} rather than a bare value (see build_extraction_prompt). Splits
    that into a flat name->value dict — what documents.extracted_fields and the
    frontend expect — a parallel name->confidence dict for extracted_fields_repository,
    and a name->reason dict (only entries where the model actually gave one).

    Falls back to treating the whole entry as a bare value with an unknown
    (None) confidence if the LLM didn't wrap a particular field, since real
    LLM output doesn't always perfectly follow the requested shape.
    """
    values: dict = {}
    confidences: dict = {}
    reasons: dict = {}
    for name, entry in raw_fields.items():
        if isinstance(entry, dict) and "value" in entry:
            values[name] = entry["value"]
            confidence = entry.get("confidence")
            confidences[name] = float(confidence) if isinstance(confidence, (int, float)) else None
            reason = entry.get("low_confidence_reason")
            if isinstance(reason, str) and reason.strip():
                reasons[name] = reason.strip()
        else:
            values[name] = entry
            confidences[name] = None
    return values, confidences, reasons
