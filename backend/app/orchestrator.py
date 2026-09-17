from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re

from . import case_status, document_repository, extracted_fields_repository, storage_service
from .config import settings
from .dify_client import dify_client, DifyInvocationError
from .extraction_prompts import build_extraction_prompt, schema_field_names
from .ingestion.ingestion_service import ingest_document
from .models import ClassificationOutcome, DocumentStatus, compute_fulfilled_requirements

logger = logging.getLogger("classifier_pipeline.orchestrator")


class OrchestrationError(Exception):
    """Raised when a document can't be progressed through classification/extraction at all."""


async def process_document(document_id: str) -> None:
    """
    Runs Intake Coordinator -> Extraction for one document. Thin wrapper
    around _process_document that guarantees the "always leaves the
    document at ANALYZED or FAILED" contract described there: any
    exception _process_document doesn't already translate into a FAILED
    status (e.g. an unexpected Supabase write error, not just a
    DifyInvocationError) is caught here so the document never gets stuck
    at a non-terminal status — which would otherwise leave the frontend
    polling forever.
    """
    try:
        await _process_document(document_id)
    except Exception as exc:  # noqa: BLE001 — last line of defence against a stuck document
        logger.exception("Document %s: unhandled error during orchestration", document_id)
        try:
            await _fail(document_id, f"Unhandled orchestration error: {exc}")
        except Exception:
            logger.exception(
                "Document %s: failed to record FAILED status after unhandled error", document_id
            )


async def _process_document(document_id: str) -> None:
    """
    Runs Intake Coordinator -> Extraction for one document, handling the
    bounded classification<->extraction escalation loop:

      - Extraction may report `escalation` in its output when the
        classified type doesn't fit the content (e.g. classified as
        Payslip but reads like a shipping manifest).
      - On escalation, Intake Coordinator is re-run WITH the escalation
        reason as extra context (not a blind retry) so it can weigh it.
      - Capped at settings.MAX_RECLASSIFICATION_ATTEMPTS — if still
        unresolved after that, the document is routed to manual review
        rather than looping indefinitely. Same escalation-depth-cap
        principle used everywhere else in this pipeline.

    Note: Dify workflows are forward-only DAGs, so this back-and-forth
    control flow lives here, not inside a single Dify workflow. Dify
    holds each agent's reasoning; this orchestrator holds the control
    flow between agents.

    Terminal: always leaves the document at ANALYZED or FAILED — failures
    are caught and translated into a FAILED status + failure_reason rather
    than propagating, so the queue worker doesn't need its own retry loop
    around this (see queue_service._process_document). Any exception this
    function doesn't itself translate is caught by process_document above.
    """
    document = await asyncio.to_thread(document_repository.get_document, document_id)
    if document is None:
        logger.error("Document %s not found; cannot orchestrate", document_id)
        return

    try:
        raw_text = await _ingest_document_text(document_id, document)
    except OrchestrationError as exc:
        await _fail(document_id, str(exc))
        return

    try:
        classification = await _run_intake_coordinator(document_id, document, raw_text)
    except DifyInvocationError as exc:
        await _fail(document_id, f"Intake Coordinator invocation failed: {exc}")
        return

    await _finalise_classification(document_id, document.case_id, classification)

    if not raw_text.strip():
        # Extraction has no vision fallback (unlike Classifier's
        # vision_classify tool), so an image-only document that made it
        # through classification via vision still can't be extracted today.
        await _fail(
            document_id,
            "Extraction produced no usable text",
            document_type=classification["document_type"],
        )
        return

    try:
        extraction = await _run_extraction(
            document_id, document, classification["document_type"], raw_text
        )
    except DifyInvocationError as exc:
        await _fail(
            document_id,
            f"Extraction invocation failed: {exc}",
            document_type=classification["document_type"],
        )
        return

    attempts = 0
    while extraction.get("escalation") and attempts < settings.MAX_RECLASSIFICATION_ATTEMPTS:
        escalation = extraction["escalation"]
        logger.info(
            "Document %s: Extraction escalated classification (%s) — attempt %d/%d",
            document_id, escalation.get("reason"), attempts + 1,
            settings.MAX_RECLASSIFICATION_ATTEMPTS,
        )
        # Persisted before re-running Intake Coordinator (not just logged) so
        # the frontend can show "classification was re-run" regardless of
        # whether it happens to poll during the reclassification window, and
        # regardless of what the loop eventually resolves to.
        await asyncio.to_thread(
            document_repository.record_escalation, document_id, escalation.get("reason", "")
        )

        try:
            classification = await _run_intake_coordinator(
                document_id,
                document,
                raw_text,
                conversation_id=classification.get("conversation_id"),
                query=_reconsideration_query(
                    escalation.get("reason", ""),
                    escalation.get("evidence") or [],
                    escalation.get("suggested_document_type"),
                ),
                escalation_context={
                    "reason": escalation.get("reason", ""),
                    "prior_document_type": classification["document_type"],
                    "suggested_document_type": escalation.get("suggested_document_type"),
                    "evidence": escalation.get("evidence") or [],
                },
            )
        except DifyInvocationError as exc:
            await _fail(document_id, f"Re-classification invocation failed: {exc}")
            return

        await _finalise_classification(document_id, document.case_id, classification)

        try:
            extraction = await _run_extraction(
                document_id, document, classification["document_type"], raw_text
            )
        except DifyInvocationError as exc:
            await _fail(
                document_id,
                f"Extraction invocation failed: {exc}",
                document_type=classification["document_type"],
            )
            return

        attempts += 1

    # Persisted regardless of whether the escalation above ever resolved:
    # even when the document ends up FAILED, whatever fields Extraction
    # actually found (address, name, amounts, ...) are real data pulled
    # from the document — discarding them just because the *type* was
    # never confidently resolved means manual review starts from a blank
    # slate instead of what was already there. See _persist_extracted_fields.
    await _persist_extracted_fields(document_id, classification["document_type"], extraction)

    if extraction.get("escalation"):
        await _fail(
            document_id,
            f"Unresolved after {settings.MAX_RECLASSIFICATION_ATTEMPTS} reclassification "
            f"attempt(s): {extraction['escalation'].get('reason')}",
            document_type=classification["document_type"],
        )
        return

    await asyncio.to_thread(
        document_repository.update_status,
        document_id,
        DocumentStatus.ANALYZED,
        document_type=classification["document_type"],
    )
    # A case's proof_of_address coverage can only become true once
    # Extraction has run (see compute_fulfilled_requirements above), so this
    # needs its own sync — the one in _finalise_classification runs too
    # early to see it. Not needed on the FAILED path above: "unrecognised"
    # (the only document_type a failed escalation can leave in place) isn't
    # one of REQUIRED_DOCUMENT_TYPES, so it can never itself change case
    # coverage regardless of what fields were extracted from it.
    if document.case_id is not None:
        await asyncio.to_thread(case_status.sync_submission_finalized, document.case_id)
    logger.info("Document %s: classification+extraction complete", document_id)


async def _persist_extracted_fields(document_id: str, document_type: str, extraction: dict) -> None:
    """
    Writes Extraction's output to both the jsonb blob on documents (what
    the frontend currently reads) and the normalised one-row-per-field
    table (for querying/reporting on individual fields) — shared by both
    the ANALYZED and FAILED paths in _process_document, since a failed
    escalation shouldn't discard whatever Extraction actually found.
    """
    extracted_fields = extraction.get("extracted_fields", {})
    field_confidences = extraction.get("field_confidences", {})
    extraction_confidence = extraction.get("confidence", 0.0)
    # Recomputed here (not left as whatever Classification recorded) since
    # this is the point individual_address is actually known — an NRIC only
    # turns out to also fulfil proof_of_address once Extraction has run.
    fulfilled_requirements = compute_fulfilled_requirements(document_type, extracted_fields)
    await asyncio.to_thread(
        document_repository.update_extracted_fields,
        document_id,
        extracted_fields,
        extraction_confidence,
        fulfilled_requirements,
    )
    await asyncio.to_thread(
        extracted_fields_repository.replace_extracted_fields,
        document_id,
        extracted_fields,
        field_confidences=field_confidences,
        default_confidence=extraction_confidence,
        required_field_names=set(schema_field_names(document_type)),
    )


async def _ingest_document_text(document_id: str, document) -> str:
    """
    Runs text extraction (native PDF text, or OCR for scans/images) once, up
    front — before classification, not just before Extraction — so the
    Classifier chatflow's `extracted_text` input is populated and its Agent
    can decide for itself (via has_text) whether to fall back to the
    vision_classify tool. Deliberately does NOT raise when raw_text comes
    back empty: an image-only document with nothing OCR can read is exactly
    the case the vision path exists for, so classification must still be
    allowed to run. Extraction (which has no vision fallback yet) still
    checks for empty text itself, right before it runs — see
    _process_document.
    """
    try:
        file_bytes = await asyncio.to_thread(
            storage_service.download_file, document.storage_path
        )
    except Exception as exc:
        raise OrchestrationError(f"Failed to download document for extraction: {exc}") from exc

    content_type = _content_type_from_filename(document.filename)

    try:
        ingestion_result = await asyncio.to_thread(
            ingest_document, file_bytes, document.filename, content_type
        )
    except Exception as exc:
        raise OrchestrationError(f"Text extraction failed: {exc}") from exc

    # Persisted regardless of whether raw_text turns out usable below — file
    # metadata (author, creation date, software, ...) is independent of text
    # extraction quality, so a document that fails the check right after
    # this still keeps whatever metadata was found.
    if ingestion_result["metadata"]:
        await asyncio.to_thread(
            document_repository.update_metadata, document_id, ingestion_result["metadata"]
        )

    if ingestion_result["needs_review"]:
        logger.warning(
            "Document %s: extraction flagged needs_review (method=%s)",
            document_id, ingestion_result["extraction_method"],
        )

    return ingestion_result["raw_text"]


def _content_type_from_filename(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _reconsideration_query(
    reason: str, evidence: list[str], suggested_document_type: str | None = None
) -> str:
    """
    Built as the *next chat turn* in the same Dify conversation, rather than
    a Start-node input — the agent already has its own prior classification
    and reasoning in the conversation history (continued via
    conversation_id below), so there's no need to re-send document_type or
    reasoning back to it. Only genuinely new information (Extraction's
    objection, or its suggested type when the prior classification was
    "unrecognised" — see extraction_prompts.KNOWN_DOCUMENT_TYPES) needs to
    be said explicitly.
    """
    if suggested_document_type:
        query = (
            f"Extraction thinks this document may actually be a "
            f"{suggested_document_type}: {reason}"
        )
    else:
        query = f"Extraction judged your classification incorrect: {reason}"
    if evidence:
        bullets = "\n".join(f"- {line}" for line in evidence)
        query += f"\n\nEvidence from the document text:\n{bullets}"
    query += "\n\nPlease reconsider your classification given this."
    return query


async def _run_intake_coordinator(
    document_id: str,
    document,
    raw_text: str,
    *,
    conversation_id: str | None = None,
    query: str = "Classify this document",
    escalation_context: dict | None = None,
) -> dict:
    signed_url = await asyncio.to_thread(
        storage_service.create_signed_url,
        document.storage_path,
        settings.SIGNED_URL_EXPIRY_SECONDS,
        document.filename,
    )

    inputs = {"document_id": document_id, "extracted_text": raw_text}
    # The Classifier chatflow's Start node still only takes a plain string
    # here (a Code node on the Dify side unpacks it with json.loads) — the
    # conversation_id continuation above is a separate, additive mechanism,
    # not a replacement for this input.
    if escalation_context is not None:
        inputs["escalation_reason"] = json.dumps(escalation_context)

    result = await dify_client.run_chatflow_with_retry(
        settings.DIFY_CLASSIFIER_API_KEY,
        inputs,
        query=query,
        user=document_id,
        conversation_id=conversation_id,
        files=[
            {
                "type": _dify_file_type(document.filename),
                "transfer_method": "remote_url",
                "url": signed_url,
            }
        ],
    )
    return _unwrap_chatflow_output(result)


def _dify_file_type(filename: str) -> str:
    return "document" if filename.lower().endswith(".pdf") else "image"


async def _run_extraction(document_id: str, document, document_type: str, raw_text: str) -> dict:
    try:
        prompt = build_extraction_prompt(document_type, raw_text)
    except ValueError as exc:
        logger.error("Document %s: %s", document_id, exc)
        return {
            "extracted_fields": {},
            "field_confidences": {},
            "confidence": 0.0,
            "escalation": {"reason": str(exc)},
        }

    signed_url = await asyncio.to_thread(
        storage_service.create_signed_url,
        document.storage_path,
        settings.SIGNED_URL_EXPIRY_SECONDS,
        document.filename,
    )

    inputs = {
        "extraction_prompt": prompt,
        "raw_text": raw_text,
        "input_file": {
            "type": _dify_file_type(document.filename),
            "transfer_method": "remote_url",
            "url": signed_url,
        },
    }

    result = await dify_client.run_workflow_with_retry(
        settings.DIFY_EXTRACTION_WORKFLOW_ID,
        inputs,
        api_key=settings.DIFY_EXTRACTION_API_KEY,
        user=document_id,
    )
    llm_output = _unwrap_workflow_output(result).get("llm_output", "")
    return _parse_extraction_output(llm_output)


def _parse_extraction_output(llm_output: str) -> dict:
    cleaned = llm_output.strip()

    # Strip reasoning-model <think>...</think> blocks (e.g. DeepSeek-style
    # models prepend chain-of-thought before the actual JSON answer)
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
        # reaches the reclassification retry via _reconsideration_query and
        # the eventual FAILED reason, instead of both saying nothing more
        # than "parsing failed".
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
        "confidence": parsed.get("confidence", 0.0),
        "escalation": parsed.get("escalation"),
    }


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


def _unwrap_workflow_output(result: dict) -> dict:
    try:
        return result["data"]["outputs"]
    except (KeyError, TypeError):
        logger.error("Unexpected Dify workflow response shape: %s", result)
        return {"needs_manual_review": True}


def _unwrap_chatflow_output(result: dict) -> dict:
    try:
        answer = result["answer"]
    except (KeyError, TypeError):
        logger.error("Unexpected Dify chatflow response shape: %s", result)
        return {"needs_manual_review": True}
    parsed = _parse_chatflow_answer(answer)
    # Dify's own conversation_id (top-level, not the chatflow-authored
    # "convo_id" echoed inside the answer) — carried through so a
    # reclassification retry can continue the same Dify conversation
    # instead of starting a fresh one each time.
    parsed["conversation_id"] = result.get("conversation_id")
    return parsed


def _parse_chatflow_answer(answer: str) -> dict:
    """
    The Classifier chatflow's Answer node emits a single JSON object (see
    the "Validate Output" code node) — not markdown, so this is a plain
    json.loads, with a fallback to the old failure shape if that ever isn't
    true (e.g. a Dify Studio edit regresses the Answer node's template).
    """
    try:
        parsed = json.loads(answer)
    except (json.JSONDecodeError, TypeError):
        logger.error("Chatflow answer was not valid JSON: %s", answer)
        return {"needs_manual_review": True}

    document_type = parsed.get("document_type")
    if document_type is not None:
        normalised = str(document_type).strip().lower()
        document_type = None if normalised in ("null", "none", "n/a", "") else normalised.replace(" ", "_")

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    needs_manual_review = parsed.get("needs_manual_review")
    if needs_manual_review is None:
        needs_manual_review = document_type is None
    else:
        needs_manual_review = bool(needs_manual_review)

    return {
        "document_type": document_type,
        "confidence": confidence,
        "needs_manual_review": needs_manual_review,
        "reasoning": parsed.get("reasoning") or "",
        "evidence": parsed.get("evidence") or [],
    }


def _is_unrecognised(document_type: str | None) -> bool:
    return document_type in (None, "", "unrecognised")


def _is_below_threshold(confidence: float | None) -> bool:
    return confidence is None or confidence < settings.CLASSIFICATION_CONFIDENCE_THRESHOLD


async def _finalise_classification(document_id: str, case_id: str | None, classification: dict) -> None:
    """
    Records the classification-stage result (type, confidence, outcome) and
    enforces the confidence threshold as a system-level gate rather than
    trusting Dify's own needs_manual_review flag alone — that flag is still
    OR'd in as an extra signal for cases the numeric threshold alone
    wouldn't catch (e.g. the sub-classifiers disagreeing despite a
    survivable confidence number).

    Also syncs the case's submission_finalized flag, since this is the one
    place a document's fulfilled_requirements (and therefore the case's
    overall requirement coverage) actually changes — both a document's
    first classification and a /retry reclassification land here.

    Extraction always runs after this, regardless of outcome. What type it
    runs against depends on what the Classifier actually said: a genuinely
    unnamed guess (no type at all) is normalised to "unrecognised" and goes
    to Extraction under the general/loan-application field list (see
    build_extraction_prompt) rather than failing the document outright.
    But a *named*, just-unconfident guess (e.g. "source_of_wealth" at 0.6,
    below CLASSIFICATION_CONFIDENCE_THRESHOLD) is kept as-is rather than
    also being flattened to "unrecognised" — Extraction then runs against
    that type's own specific schema straight away instead of the generic
    one, which is both a better starting point than a blank guess and lets
    the frontend show what the Classifier actually thinks this is instead
    of a blanket "unrecognised". Either way, if Extraction can actually
    tell what the document is from its content, it escalates back to the
    Classifier (see extraction_prompts.KNOWN_DOCUMENT_TYPES /
    _reconsideration_query) instead of the document being permanently
    stuck on a bad first guess. classification_outcome still records
    MANUAL_REVIEW for anything not accepted regardless of which of these
    two cases it is — this only changes what Extraction has to work with,
    not whether the document reads as confidently classified.
    """
    document_type = classification.get("document_type")
    confidence = classification.get("confidence")
    unnamed = _is_unrecognised(document_type)
    accepted = (
        not unnamed
        and not _is_below_threshold(confidence)
        and not classification.get("needs_manual_review")
    )
    if unnamed:
        # Only a genuinely typeless response gets normalised to the
        # sentinel — see the docstring above for why a named-but-unconfident
        # guess is deliberately left alone here instead. Written back into
        # the classification dict (not just this local variable) so the
        # caller's later `classification["document_type"]` reads see the
        # normalised value too.
        document_type = "unrecognised"
        classification["document_type"] = document_type

    outcome = ClassificationOutcome.ACCEPTED if accepted else ClassificationOutcome.MANUAL_REVIEW
    # Self-fulfilment only at this stage — individual_address isn't known
    # until Extraction runs, so proof_of_address cross-fulfilment (see
    # compute_fulfilled_requirements) gets recomputed and persisted again
    # once extraction succeeds, further down in _process_document.
    await asyncio.to_thread(
        document_repository.record_classification,
        document_id,
        document_type=document_type,
        confidence=confidence,
        classification_outcome=outcome.value,
        evidence=classification.get("evidence", []),
        fulfilled_requirements=compute_fulfilled_requirements(document_type, None),
    )

    if case_id is not None:
        await asyncio.to_thread(case_status.sync_submission_finalized, case_id)


async def _fail(document_id: str, reason: str, *, document_type: str | None = None) -> None:
    await asyncio.to_thread(
        document_repository.update_status,
        document_id,
        DocumentStatus.FAILED,
        failure_reason=reason,
        document_type=document_type,
    )
    logger.warning("Document %s marked FAILED — manual review required: %s", document_id, reason)