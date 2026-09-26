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
from ..extraction_prompts import build_extraction_prompt

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
    from langchain_anthropic import ChatAnthropic  # local import: optional dep until phase 0 is installed

    from .cache_gate import cache_gate

    prefix, text = _split_prompt_for_caching(document_type, raw_text)
    model = ChatAnthropic(
        model=settings.LANGGRAPH_SONNET_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        temperature=0,
        max_tokens=4096,
    )
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": text},
        ],
    }
    # Gated per document_type (the actual cache key — see
    # _split_prompt_for_caching): the first document of a given type in a
    # batch pays for the cache write, the rest of that type wait for it
    # rather than racing it. Different types never block each other.
    response = await cache_gate.run(f"extract:{document_type}", lambda: model.ainvoke([message]))
    llm_output = response.content if isinstance(response.content, str) else str(response.content)
    return parse_extraction_output(llm_output)


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
            "confidence": 0.0,
            "escalation": {
                "reason": f"Extraction did not return valid JSON. Its response was: {excerpt}"
            },
        }

    raw_fields = parsed.get("extracted_fields", {})
    extracted_fields, field_confidences = _split_field_confidences(
        raw_fields if isinstance(raw_fields, dict) else {}
    )

    return {
        "extracted_fields": extracted_fields,
        "field_confidences": field_confidences,
        # The document-level confidence is derived from the individual
        # fields' own confidences (averaged), not the LLM's separate
        # top-level self-report — a single overall number is easy for the
        # model to phone in without actually reasoning about each field,
        # whereas averaging the per-field numbers it already committed to
        # ties the document score to the same judgements it made per field.
        "confidence": _average_field_confidence(field_confidences),
        "escalation": parsed.get("escalation"),
    }


def _average_field_confidence(field_confidences: dict) -> float:
    values = [v for v in field_confidences.values() if isinstance(v, (int, float))]
    if not values:
        return 0.0
    return sum(values) / len(values)


def _split_field_confidences(raw_fields: dict) -> tuple[dict, dict]:
    """
    The extraction prompt asks the LLM to wrap each field as
    {"value": ..., "confidence": <float>} rather than a bare value (see
    build_extraction_prompt). Splits that into a flat name->value dict —
    what documents.extracted_fields and the frontend expect — and a
    parallel name->confidence dict for extracted_fields_repository.

    Falls back to treating the whole entry as a bare value with an unknown
    (None) confidence if the LLM didn't wrap a particular field, since real
    LLM output doesn't always perfectly follow the requested shape.
    """
    values: dict = {}
    confidences: dict = {}
    for name, entry in raw_fields.items():
        if isinstance(entry, dict) and "value" in entry:
            values[name] = entry["value"]
            confidence = entry.get("confidence")
            confidences[name] = float(confidence) if isinstance(confidence, (int, float)) else None
        else:
            values[name] = entry
            confidences[name] = None
    return values, confidences
