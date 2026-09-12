from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re

<<<<<<< Updated upstream
from . import document_repository, extracted_fields_repository, storage_service
=======
from . import case_status, document_repository, storage_service
>>>>>>> Stashed changes
from .config import settings
from .dify_client import dify_client, DifyInvocationError
from .extraction_prompts import EXTRACTION_SCHEMAS, build_extraction_prompt
from .ingestion.ingestion_service import ingest_document
from .models import REQUIRED_DOCUMENT_TYPES, ClassificationOutcome, DocumentStatus, DocumentType

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
        classification = await _run_intake_coordinator(document_id, document)
    except DifyInvocationError as exc:
        await _fail(document_id, f"Intake Coordinator invocation failed: {exc}")
        return

    if not await _finalise_classification(document_id, document.case_id, classification):
        return

    try:
        raw_text = await _extract_raw_text(document_id, document)
    except OrchestrationError as exc:
        await _fail(document_id, str(exc), document_type=classification["document_type"])
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
                document_id, document, escalation_reason=escalation.get("reason")
            )
        except DifyInvocationError as exc:
            await _fail(document_id, f"Re-classification invocation failed: {exc}")
            return

        if not await _finalise_classification(document_id, document.case_id, classification):
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

        attempts += 1

    if extraction.get("escalation"):
        await _fail(
            document_id,
            f"Unresolved after {settings.MAX_RECLASSIFICATION_ATTEMPTS} reclassification "
            f"attempt(s): {extraction['escalation'].get('reason')}",
            document_type=classification["document_type"],
        )
        return

    # Persist extracted fields before marking ANALYZED — both the jsonb blob
    # on documents (what the frontend currently reads) and the normalised
    # one-row-per-field table (for querying/reporting on individual fields).
    extracted_fields = extraction.get("extracted_fields", {})
    extraction_confidence = extraction.get("confidence", 0.0)
    await asyncio.to_thread(
        document_repository.update_extracted_fields,
        document_id,
        extracted_fields,
        extraction_confidence,
    )
    await asyncio.to_thread(
        extracted_fields_repository.replace_extracted_fields,
        document_id,
        extracted_fields,
        confidence=extraction_confidence,
        required_field_names=set(EXTRACTION_SCHEMAS.get(classification["document_type"], [])),
    )

    await asyncio.to_thread(
        document_repository.update_status,
        document_id,
        DocumentStatus.ANALYZED,
        document_type=classification["document_type"],
    )
    logger.info("Document %s: classification+extraction complete", document_id)


async def _extract_raw_text(document_id: str, document) -> str:
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

    if not ingestion_result["raw_text"].strip():
        raise OrchestrationError("Extraction produced no usable text")

    if ingestion_result["needs_review"]:
        logger.warning(
            "Document %s: extraction flagged needs_review (method=%s)",
            document_id, ingestion_result["extraction_method"],
        )

    return ingestion_result["raw_text"]


def _content_type_from_filename(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


async def _run_intake_coordinator(
    document_id: str, document, *, escalation_reason: str | None = None
) -> dict:
    signed_url = await asyncio.to_thread(
        storage_service.create_signed_url,
        document.storage_path,
        settings.SIGNED_URL_EXPIRY_SECONDS,
        document.filename,
    )

    inputs = {"document_id": document_id}
    if escalation_reason:
        inputs["escalation_reason"] = escalation_reason
        inputs["prior_classification"] = None

    result = await dify_client.run_chatflow_with_retry(
        settings.DIFY_CLASSIFIER_API_KEY,
        inputs,
        query="Classify this document",
        user=document_id,
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
        return {"extracted_fields": {}, "confidence": 0.0, "escalation": {"reason": str(exc)}}

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
        return {
            "extracted_fields": {},
            "confidence": 0.0,
            "escalation": {"reason": "Extraction produced unparsable output"},
        }

    return {
        "extracted_fields": parsed.get("extracted_fields", {}),
        "confidence": parsed.get("confidence", 0.0),
        "escalation": parsed.get("escalation"),
    }


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
    return _parse_chatflow_answer(answer)


def _answer_field(answer: str, label: str) -> str | None:
    match = re.search(rf"\*\*{re.escape(label)}:\*\*\s*(.+)", answer, re.IGNORECASE)
    return match.group(1).strip() or None if match else None


def _answer_section(answer: str, label: str) -> str | None:
    match = re.search(
        rf"\*\*{re.escape(label)}:\*\*\s*(.+?)(?=\n\s*\*\*[^*]+:\*\*|\Z)",
        answer, re.IGNORECASE | re.DOTALL,
    )
    return match.group(1).strip() or None if match else None


def _parse_chatflow_answer(answer: str) -> dict:
    document_type = _answer_field(answer, "Document type")
    if document_type is not None:
        normalised = document_type.strip().lower()
        document_type = None if normalised in ("null", "none", "n/a") else normalised.replace(" ", "_")

    confidence_raw = _answer_field(answer, "Confidence")
    try:
        confidence = float(confidence_raw) if confidence_raw is not None else 0.0
    except ValueError:
        confidence = 0.0

    needs_manual_review_raw = _answer_field(answer, "Needs manual review")
    needs_manual_review = (
        needs_manual_review_raw.lower() == "true"
        if needs_manual_review_raw is not None
        else document_type is None
    )

    reasoning = _answer_section(answer, "Reasoning") or ""
    evidence_block = _answer_section(answer, "Evidence") or ""
    evidence = [
        line.lstrip("-* ").strip()
        for line in evidence_block.splitlines()
        if line.strip().startswith(("-", "*"))
    ]

    fulfilled_requirements = _parse_fulfilled_requirements(answer, document_type)

    # Enforced deterministically rather than trusting the Classifier to
    # correctly propagate its own "Address check: visible" evidence line
    # into Fulfils requirements — it reliably writes the evidence line when
    # asked to, but self-consistency between two separate output fields is
    # exactly the kind of thing LLMs are unreliable at even when told
    # explicitly to do it. Reading the same signal back out of evidence here
    # is a 100%-reliable string match instead of a probabilistic judgement.
    if (
        document_type in (DocumentType.ID_DOCUMENT.value, DocumentType.BANK_STATEMENT.value)
        and DocumentType.PROOF_OF_ADDRESS.value not in fulfilled_requirements
        and _evidence_confirms_visible_address(evidence)
    ):
        fulfilled_requirements = [*fulfilled_requirements, DocumentType.PROOF_OF_ADDRESS.value]

    return {
        "document_type": document_type,
        "confidence": confidence,
        "needs_manual_review": needs_manual_review,
        "reasoning": reasoning,
        "evidence": evidence,
        "fulfilled_requirements": fulfilled_requirements,
    }


def _evidence_confirms_visible_address(evidence: list[str]) -> bool:
    """
    Matches "Address check: visible — ..." but not "Address check: not
    visible — ..." (the "not" sits between "check:" and "visible", so the
    pattern's tight `:\\s*visible` never matches the negative phrasing).
    """
    pattern = re.compile(r"address\s*check\s*:\s*visible", re.IGNORECASE)
    return any(pattern.search(line) for line in evidence)


def _parse_fulfilled_requirements(answer: str, document_type: str | None) -> list[str]:
    """
    A document can satisfy more than one checklist requirement (e.g. an
    NRIC also covers Proof of Address, but a passport doesn't) — this reads
    the Classifier's own judgement of which ones, rather than assuming a
    fixed type-to-requirement table, since that judgement depends on the
    document's actual content.

    Falls back to just [document_type] when the Classifier prompt hasn't
    been updated yet to include this section — self-fulfilment is still
    correct even without the cross-requirement judgement.
    """
    block = _answer_section(answer, "Fulfils requirements") or ""
    values = [
        line.lstrip("-* ").strip().lower().replace(" ", "_")
        for line in block.splitlines()
        if line.strip().startswith(("-", "*"))
    ]
    recognised = [value for value in values if value in REQUIRED_DOCUMENT_TYPES]
    if recognised:
        return recognised

    if document_type in REQUIRED_DOCUMENT_TYPES:
        return [document_type]
    return []


def _is_unrecognised(document_type: str | None) -> bool:
    return document_type in (None, "", "unrecognised")


def _is_below_threshold(confidence: float | None) -> bool:
    return confidence is None or confidence < settings.CLASSIFICATION_CONFIDENCE_THRESHOLD


async def _finalise_classification(
    document_id: str, case_id: str | None, classification: dict
) -> bool:
    """
    Records the classification-stage result (type, confidence, outcome) and
    enforces the confidence threshold as a system-level gate rather than
    trusting Dify's own needs_manual_review flag alone — that flag is still
    OR'd in as an extra signal for cases the numeric threshold alone
    wouldn't catch (e.g. the sub-classifiers disagreeing despite a
    survivable confidence number).

<<<<<<< Updated upstream
    Returns True if extraction should run — either because classification was
    accepted outright, or because it came back "unrecognised", in which case
    Extraction still runs but against a general, loan-application-relevant
    field list rather than a type-specific one (see build_extraction_prompt)
    so a reviewer has something pulled out instead of nothing. classification
    is still recorded as MANUAL_REVIEW in that case — this only changes
    whether Extraction runs, not whether the document reads as confidently
    classified. Returns False only for a *typed* classification that's below
    the confidence threshold or explicitly flagged for manual review, where
    FAILED has already been recorded.
=======
    Also syncs the case's submission_finalized flag, since this is the one
    place a document's fulfilled_requirements (and therefore the case's
    overall requirement coverage) actually changes — both a document's
    first classification and a /retry reclassification land here.

    Returns True if classification is accepted and extraction should run;
    False if it's been routed to manual review (FAILED already recorded).
>>>>>>> Stashed changes
    """
    document_type = classification.get("document_type")
    confidence = classification.get("confidence")
    unrecognised = _is_unrecognised(document_type)
    if unrecognised:
        # Normalise Dify's various ways of saying "no type" (None, "", or the
        # literal words null/none/n/a already folded to None by
        # _parse_chatflow_answer) to the explicit "unrecognised" sentinel so
        # it's what actually lands in Supabase/the frontend, not NULL. Written
        # back into the classification dict (not just this local variable) so
        # the caller's later `classification["document_type"]` reads — used to
        # build the Extraction prompt — see the normalised value too, rather
        # than the original None.
        document_type = "unrecognised"
        classification["document_type"] = document_type
    below_threshold = _is_below_threshold(confidence)
    accepted = not unrecognised and not below_threshold and not classification.get("needs_manual_review")

    outcome = ClassificationOutcome.ACCEPTED if accepted else ClassificationOutcome.MANUAL_REVIEW
    await asyncio.to_thread(
        document_repository.record_classification,
        document_id,
        document_type=document_type,
        confidence=confidence,
        classification_outcome=outcome.value,
        evidence=classification.get("evidence", []),
        fulfilled_requirements=classification.get("fulfilled_requirements", []),
    )

<<<<<<< Updated upstream
    if accepted or unrecognised:
=======
    if case_id is not None:
        await asyncio.to_thread(case_status.sync_submission_finalized, case_id)

    if accepted:
>>>>>>> Stashed changes
        return True

    if below_threshold:
        reason = (
            f"Classification confidence ({confidence}) is below the required "
            f"threshold ({settings.CLASSIFICATION_CONFIDENCE_THRESHOLD})"
        )
    else:
        reason = f"Classifier flagged for manual review (confidence {confidence})"
        if classification.get("reasoning"):
            reason += f": {classification['reasoning']}"

    await _fail(document_id, reason, document_type=document_type)
    return False


async def _fail(document_id: str, reason: str, *, document_type: str | None = None) -> None:
    await asyncio.to_thread(
        document_repository.update_status,
        document_id,
        DocumentStatus.FAILED,
        failure_reason=reason,
        document_type=document_type,
    )
    logger.warning("Document %s marked FAILED — manual review required: %s", document_id, reason)