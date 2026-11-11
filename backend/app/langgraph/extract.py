"""
Extractor — one Sonnet call plus output parsing. Prompt building
(`build_extraction_prompt`) lives in extraction_prompts.py; parsing the
model's JSON into extracted fields/confidences lives here.
"""

from __future__ import annotations

import asyncio
import logging
import re

from ..config import settings
from ..holder import ENTITY_DOCUMENT_TYPES, ENTITY_TYPES, looks_corporate
from langchain_core.messages import AIMessage, HumanMessage

from ..guardrails.envelope import DOCUMENT_DATA_RULE, fence
from ..llm_contracts import ExtractionReply
from ..llm_io import message_text, parse_json_object, salvage_entries, validate, was_cut_off
from ..run_context import spend_model_call
from .extraction_prompts import build_extraction_prompt, drop_empty_optional_fields
from .extraction_prompts import EXTRACTION_SCHEMAS
from . import correction_hints
from .. import telemetry

logger = logging.getLogger("classifier_pipeline.langgraph.extract")

# Bank statements list every transaction with a per-field confidence wrapper; 4096 truncated the JSON. Room for thinking too.
_EXTRACTION_MAX_TOKENS = 18000


def _split_prompt_for_caching(document_type: str, raw_text: str) -> tuple[str, str]:
    """
    build_extraction_prompt always ends with the schema/instructions
    followed by "Document text:\n{raw_text}". Building the same prompt with
    an empty raw_text gives a byte-stable prefix (same document_type -> same
    prefix across every document and run) to put cache_control on
    (schema first, raw_text last).
    """
    prefix = build_extraction_prompt(document_type, "")
    return prefix, raw_text


def _classifier_guess_hint(classified_as: str) -> str:
    """
    The generic schema doesn't name a type, so on its own it can only suggest a known category; it can't say
    "this is not what the classifier guessed". This tells it the guess, so it can send the document back.
    """
    return (
        f"The classifier guessed \"{classified_as}\" for this document but was not confident. "
        f"Judge that yourself from the text. If the document clearly is not a {classified_as}, answer with the "
        'escalation form: a "reason" describing what it actually appears to be (its heading, who it is from or '
        'to, visible dates or reference numbers), an "evidence" list of lines copied verbatim from the text, and '
        '"suggested_document_type" only if you are confident it is one of the known categories above (otherwise '
        f'omit it). If it plausibly is a {classified_as}, leave escalation null.'
    )


async def run_extraction(
    document_type: str,
    raw_text: str,
    *,
    classified_as: str | None = None,
    document_id: str | None = None,
    filename: str | None = None,
    storage_path: str | None = None,
) -> dict:
    from ._anthropic import build_chat_anthropic
    from .cache_gate import cache_gate

    prefix, text = _split_prompt_for_caching(document_type, raw_text)
    model = build_chat_anthropic(settings.LANGGRAPH_SONNET_MODEL, _EXTRACTION_MAX_TOKENS, effort="low")
    # The hints are their own blocks after the cached prefix, so the prefix stays the same for every document.
    hint = [{"type": "text", "text": _classifier_guess_hint(classified_as)}] if classified_as else []
    fields = correction_hints.field_hint(document_type, await correction_hints.load_field_edits(document_type))
    if fields:
        hint.append({"type": "text", "text": fields})
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": f"{prefix}\n{DOCUMENT_DATA_RULE}\n", "cache_control": {"type": "ephemeral"}},
            *hint,
            {"type": "text", "text": fence(text, "document_text")},
        ],
    }
    with telemetry.get_tracer().start_as_current_span("extract.run_extraction") as span:
        span.set_attribute("document_type", document_type)
        # Gated per document_type (the actual cache key — see
        # _split_prompt_for_caching): the first document of a given type in a
        # batch pays for the cache write, the rest of that type wait for it
        # rather than racing it. Different types never block each other.
        spend_model_call("extraction")
        response = await cache_gate.run(f"extract:{document_type}", lambda: model.ainvoke([message]))
        telemetry.record_usage(span, response)
        reply, cut_off = message_text(response.content), was_cut_off(response)
        result = parse_extraction_output(reply, cut_off=cut_off)
        if result.get("output_error") and not cut_off:
            # One retry: the model sees its own unreadable reply and is told to answer with the JSON only.
            # Not for a cut-off reply, which would only be cut off again.
            spend_model_call("extraction retry")
            retry = await model.ainvoke([
                HumanMessage(content=message["content"]),
                AIMessage(content=reply.strip() or "(no reply)"),
                HumanMessage(content="Your reply was not the JSON object described above. Reply again with ONLY that JSON object."),
            ])
            telemetry.record_usage(span, retry)
            retried = parse_extraction_output(message_text(retry.content), cut_off=was_cut_off(retry))
            result = {**retried, "retried": True}
        result = _apply_entity_fields(result, document_type)
        result = _drop_unprinted_numbers(result, raw_text)
        result = _drop_empty_fields(result, document_type)
        result = _drop_period_totals(result, raw_text)
        result = _drop_example_values(result, raw_text)
        selected: list[dict] = []
        if document_id and filename and storage_path and not result.get("output_error"):
            selected = select_second_look_fields(document_type, result)
            result["field_provenance"] = {
                name: {
                    "source": "text",
                    "confidence": (result.get("field_confidences") or {}).get(name),
                }
                for name in (result.get("extracted_fields") or {})
            }
            if selected:
                from ..storage_service import download_file
                from .extract_graph import run_second_look

                try:
                    file_bytes = await asyncio.to_thread(download_file, storage_path)
                    from ..ingestion.pdf_extractor import pdf_contains_text
                    native_text_pdf = filename.lower().endswith(".pdf") and await asyncio.to_thread(
                        pdf_contains_text, file_bytes,
                    )
                    review = await run_second_look(
                        document_id=document_id,
                        filename=filename,
                        file_bytes=file_bytes,
                        raw_text=raw_text,
                        selected_fields=selected,
                        native_text_pdf=native_text_pdf,
                    )
                    _merge_second_look(result, review, selected, raw_text, native_text_pdf=native_text_pdf)
                except Exception as exc:
                    logger.warning("Document %s: extraction second look failed: %s", document_id, exc)
                    result["second_look_error"] = f"{type(exc).__name__}: {exc}"
        span.set_attribute("dropped_unprinted_fields", len(result.get("dropped_unprinted_fields", [])))
        span.set_attribute("second_look_fields", len(selected) if document_id and filename and storage_path else 0)
        return result


def select_second_look_fields(document_type: str, result: dict) -> list[dict]:
    """Select only low-confidence, missing-required, or extraction-guard-dropped fields."""
    values = result.get("extracted_fields") or {}
    confidences = result.get("field_confidences") or {}
    reasons = result.get("field_reasons") or {}
    schema = EXTRACTION_SCHEMAS.get(document_type, {})
    required = set(schema.get("required", []))
    if values.get("entity_type") == "individual":
        required.update(schema.get("required_when_individual", []))
    dropped = {
        name
        for key in ("dropped_unprinted_fields", "dropped_empty_fields", "dropped_period_totals", "dropped_example_values")
        for name in result.get(key, [])
    }
    names = set(values) | required | dropped
    selected = []
    for name in sorted(names):
        value = values.get(name)
        confidence = confidences.get(name)
        triggers = []
        if isinstance(confidence, (int, float)) and confidence < settings.EXTRACTION_CONFIDENCE_THRESHOLD:
            triggers.append("low_confidence")
        elif confidence is None and name in values and name not in dropped:
            triggers.append("confidence_missing")
        if name in required and (name not in values or value is None or value == ""):
            triggers.append("missing_required")
        if name in dropped:
            triggers.append("guard_dropped")
        if triggers:
            selected.append({
                "name": name,
                "current_value": value,
                "confidence": confidence,
                "reason": reasons.get(name),
                "triggers": triggers,
            })
    return selected


def _printed_number_tokens(text: str) -> set[float]:
    return {float(item.replace(",", "")) for item in _TEXT_NUMBER_RE.findall(text or "")}


def _candidate_numbers(value) -> list[float]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [abs(float(value))]
    if isinstance(value, str):
        return [float(item.replace(",", "")) for item in _TEXT_NUMBER_RE.findall(value)]
    if isinstance(value, dict):
        return [number for child in value.values() for number in _candidate_numbers(child)]
    if isinstance(value, (list, tuple)):
        return [number for child in value for number in _candidate_numbers(child)]
    return []


def _same_value(left, right) -> bool:
    if left == right:
        return True
    if isinstance(left, str) and isinstance(right, str):
        return re.sub(r"\s+", " ", left).strip().casefold() == re.sub(r"\s+", " ", right).strip().casefold()
    return False


def _merge_second_look(
    result: dict,
    review: dict,
    selected: list[dict],
    raw_text: str,
    *,
    native_text_pdf: bool,
) -> None:
    """Merge only corroborated or missing values; never hide a text/image disagreement."""
    fields = result.setdefault("extracted_fields", {})
    confidences = result.setdefault("field_confidences", {})
    reasons = result.setdefault("field_reasons", {})
    provenance = result.setdefault("field_provenance", {})
    conflicts = result.setdefault("extraction_disagreements", [])
    selected_by_name = {item["name"]: item for item in selected}
    allowed_sources = {source for source, used in (review.get("_tools_used") or {}).items() if used}
    native_pdf = native_text_pdf
    printed = _printed_number_tokens(raw_text)
    vision_pages = review.get("_vision_pages") or []

    for name, candidate in (review.get("fields") or {}).items():
        if name not in selected_by_name or not isinstance(candidate, dict):
            continue
        source = candidate.get("source")
        value = candidate.get("value")
        confidence = candidate.get("confidence")
        if source not in allowed_sources or source not in {"layout_lookup", "vision_read"} or value in (None, ""):
            continue
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            continue
        confidence = max(0.0, min(1.0, float(confidence)))
        current = fields.get(name)
        if current not in (None, "") and not _same_value(current, value):
            if source == "vision_read" and native_pdf:
                conflict = {
                    "field": name,
                    "text_value": current,
                    "visual_value": value,
                    "visual_confidence": confidence,
                    "pages": vision_pages,
                    "reason": candidate.get("reason") or "The native text and rendered page appear to differ.",
                }
                conflicts.append(conflict)
            else:
                reasons[name] = (candidate.get("reason") or "Second look disagreed with the text extraction; "
                                 "the original text value was kept.")
            continue
        # A native PDF's image cannot add a number absent from its text layer. If a text value
        # already exists, the disagreement above is retained as a detection finding instead.
        if native_pdf and source == "vision_read":
            if any(not any(abs(number - printed_number) < 0.005 for printed_number in printed)
                   for number in _candidate_numbers(value)):
                result.setdefault("second_look_rejected_fields", []).append({
                    "field": name,
                    "reason": "Vision proposed a number absent from the native PDF text layer.",
                })
                continue
        confidences[name] = max(float(confidences.get(name) or 0.0), confidence)
        provenance[name] = {
            "source": "text" if current not in (None, "") else source.removesuffix("_read").removesuffix("_lookup"),
            "confidence": confidences[name],
            "corroborated_by": [source] if current not in (None, "") else [],
        }
        if current in (None, ""):
            fields[name] = value
        if candidate.get("reason"):
            reasons[name] = candidate["reason"]
    if conflicts:
        result["extraction_disagreements"] = conflicts
    result["confidence"] = _average_field_confidence(confidences)


def _failed_extraction(reason: str, *, salvaged: dict | None = None) -> dict:
    """
    An extraction whose reply could not be used. This is not the model objecting to the document's type:
    `output_error` is what stops the document, `escalation` stays empty so nothing is reclassified.
    `salvaged` holds the complete fields of a reply that was cut off, kept so the officer can see them.
    """
    values, confidences, reasons = _split_field_confidences(salvaged or {})
    return {
        "extracted_fields": values,
        "field_confidences": confidences,
        "field_reasons": reasons,
        "confidence": _average_field_confidence(confidences),
        "escalation": None,
        "output_error": reason,
        "output_status": "failed",
    }


def parse_extraction_output(llm_output: str, *, cut_off: bool = False) -> dict:
    """
    The model's reply as an extraction, never raising. A reply that isn't an object with `extracted_fields`
    comes back with `output_error` set and no fields (or only the complete ones from a cut-off reply).
    """
    parsed = parse_json_object(llm_output)
    checked = validate(ExtractionReply, parsed, site="extraction", raw=llm_output)
    reply = checked.value
    # Valid JSON with neither fields nor an objection says nothing. An objection alone is the model
    # sending the document back to the classifier, which is the point of an escalation.
    if reply is None or (reply.extracted_fields is None and reply.escalation is None):
        logger.error("Extraction reply unusable (cut off: %s): %r", cut_off, (llm_output or "")[:500])
        if cut_off:
            salvaged = salvage_entries(llm_output or "", "extracted_fields")
            return _failed_extraction(
                f"Extraction output was cut off at the token limit; {len(salvaged)} complete field(s) were kept "
                "and the rest are missing.",
                salvaged=salvaged,
            )
        # The model's prose often still holds the real finding (e.g. "no individual_name present on this
        # document"), so an excerpt goes into the reason.
        excerpt = re.sub(r"<think>.*?</think>", "", llm_output or "", flags=re.DOTALL).strip()[:500]
        return _failed_extraction(f"Extraction did not return valid JSON. Its response was: {excerpt}")

    extracted_fields, field_confidences, field_reasons = _split_field_confidences(reply.extracted_fields or {})

    return {
        "extracted_fields": extracted_fields,
        "field_confidences": field_confidences,
        # Only for fields the agent itself scored low — see _FIELD_WRAPPER_INSTRUCTIONS.
        # Not persisted anywhere; surfaced only in this run's langgraph trace (nodes.py's extract_node).
        "field_reasons": field_reasons,
        # The document-level confidence is derived from the individual fields' own confidences (averaged),
        # not the model's separate top-level self-report, which is easy to phone in.
        "confidence": _average_field_confidence(field_confidences),
        "escalation": reply.escalation,
        "output_status": checked.status,
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


_ENTITY_FIELDS = ("entity_name", "entity_address", "entity_type")


def _apply_entity_fields(result: dict, document_type: str) -> dict:
    """
    Stores the holder read as entity_name / entity_address / entity_type under the names the app reads:
    company_name and company_address for a corporate, individual_name and individual_address for an
    individual, plus entity_type. A type that contradicts the name (an "individual" called "X PTE LTD") is
    resolved to corporate and left at low confidence, so the officer reviews it.
    """
    if document_type not in ENTITY_DOCUMENT_TYPES:
        return result
    fields = result.get("extracted_fields") or {}
    confidences = result.setdefault("field_confidences", {})
    reasons = result.setdefault("field_reasons", {})
    taken = {name: fields.pop(name, None) for name in _ENTITY_FIELDS}
    entity_confidence = {name: confidences.pop(name, None) for name in _ENTITY_FIELDS}
    for name in _ENTITY_FIELDS:
        reasons.pop(name, None)
    name, address, raw_type = taken["entity_name"], taken["entity_address"], taken["entity_type"]
    if name in (None, "") and address in (None, ""):
        return result

    stated = str(raw_type or "").strip().lower()
    stated = stated if stated in ENTITY_TYPES else None
    corporate_name = looks_corporate(name)
    if stated == "individual" and corporate_name:
        kind, type_confidence, reason = "corporate", 0.5, "The holder was read as an individual but its name is a company's."
    elif stated is None:
        kind, type_confidence = ("corporate" if corporate_name else "individual"), 0.6
        reason = "The kind of holder was not stated; it was taken from the name."
    else:
        kind, type_confidence, reason = stated, entity_confidence["entity_type"], None

    prefix = "company" if kind == "corporate" else "individual"
    for source, target in (("entity_name", f"{prefix}_name"), ("entity_address", f"{prefix}_address")):
        if taken[source] not in (None, ""):
            fields[target] = taken[source]
            confidences[target] = entity_confidence[source]
    fields["entity_type"] = kind
    confidences["entity_type"] = type_confidence
    if reason:
        reasons["entity_type"] = reason
    result["confidence"] = _average_field_confidence(confidences)
    return result


_EXAMPLE_START_RE = re.compile(r"\s*example\s+\d+\s*:", re.IGNORECASE)
_EXAMPLE_END_RE = re.compile(r"\s*(for more details|explanation of)", re.IGNORECASE)
_GRADE_RE = re.compile(r"\b[A-Z]{2}\b")


def _split_example_blocks(raw_text: str) -> tuple[str, str]:
    """(text inside "Example N:" blocks, the rest). A block ends at "For more details" or "Explanation of"."""
    inside, outside, in_block = [], [], False
    for line in (raw_text or "").splitlines():
        if _EXAMPLE_START_RE.match(line):
            in_block = True
        elif in_block and _EXAMPLE_END_RE.match(line):
            in_block = False
        (inside if in_block else outside).append(line)
    return "\n".join(inside), "\n".join(outside)


def _drop_example_values(result: dict, raw_text: str) -> dict:
    """
    A report can print sample values in "Example N:" blocks (a credit report's Bureau Score section does).
    A top-level scalar whose value appears only inside those blocks is the sample, not this document's data.
    """
    inside, outside = _split_example_blocks(raw_text)
    figures = {float(t.replace(",", "")) for t in _TEXT_NUMBER_RE.findall(inside)}
    figures -= {float(t.replace(",", "")) for t in _TEXT_NUMBER_RE.findall(outside)}
    grades = set(_GRADE_RE.findall(inside)) - set(_GRADE_RE.findall(outside))
    fields = result.get("extracted_fields") or {}
    dropped = []
    for name, value in list(fields.items()):
        figure = _as_figure(value)
        is_example = (
            any(abs(figure - f) < 0.005 for f in figures) if figure is not None
            else isinstance(value, str) and value.strip().upper() in grades
        )
        if is_example:
            dropped.append(name)
            del fields[name]
            result.get("field_confidences", {}).pop(name, None)
            result.get("field_reasons", {}).pop(name, None)
    if dropped:
        logger.info("Dropped extracted fields that only appear in example blocks: %s", dropped)
        result["confidence"] = _average_field_confidence(result.get("field_confidences", {}))
    result["dropped_example_values"] = dropped
    return result


_STATEMENT_TOTAL_FIELDS = ("total_credits", "total_debits")
# A bare "Total" row followed by figures (one per month or page). A labelled
# "Total Credits 169,548.98" line is a whole-statement total, not one of these.
_TOTAL_LINE_RE = re.compile(r"\s*total\s*:?\s*[-(]?\d", re.IGNORECASE)
# A field named for one period ("june_total_deposits", "q2_total", "period_2_total").
_PERIOD_KEY_RE = re.compile(
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|month|period|q[1-4]|20\d\d", re.IGNORECASE
)


def _drop_period_totals(result: dict, raw_text: str) -> dict:
    """
    total_credits/total_debits are whole-statement totals. A statement that
    prints one "Total" line per month has none, but the LLM tends to copy
    the first month's figures into them anyway: printed, so
    _drop_unprinted_numbers keeps them, and BS.TOTALS then compares one
    month's total with every month's transactions. Dropped when the figure
    is also extracted as one period's total (june_total_deposits), or sits
    on one of two or more bare "Total" rows.
    """
    fields = result.get("extracted_fields") or {}
    total_lines = [line for line in (raw_text or "").splitlines() if _TOTAL_LINE_RE.match(line)]
    per_period_lines = total_lines if len(total_lines) >= 2 else []
    dropped = []
    for name in _STATEMENT_TOTAL_FIELDS:
        figure = _as_figure(fields.get(name))
        if figure is None:
            continue
        copied = any(
            other not in _STATEMENT_TOTAL_FIELDS and "total" in other.lower() and _PERIOD_KEY_RE.search(other)
            and (other_figure := _as_figure(value)) is not None and abs(other_figure - figure) < 0.005
            for other, value in fields.items()
        )
        on_period_line = any(
            abs(float(token.replace(",", "")) - figure) < 0.005
            for line in per_period_lines for token in _TEXT_NUMBER_RE.findall(line)
        )
        if copied or on_period_line:
            dropped.append(name)
            del fields[name]
            result.get("field_confidences", {}).pop(name, None)
            result.get("field_reasons", {}).pop(name, None)
    if dropped:
        logger.info("Dropped whole-statement totals that are one period's total: %s", dropped)
    result["dropped_period_totals"] = dropped
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
            # A number from 0 to 1; anything else (text, a boolean, NaN) is "unknown", never a made-up score.
            number = confidence if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else None
            confidences[name] = max(0.0, min(1.0, float(number))) if number is not None and number == number else None
            reason = entry.get("low_confidence_reason")
            if isinstance(reason, str) and reason.strip():
                reasons[name] = reason.strip()
        else:
            values[name] = entry
            confidences[name] = None
    return values, confidences, reasons
