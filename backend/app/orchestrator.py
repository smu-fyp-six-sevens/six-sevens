from __future__ import annotations

import asyncio
import logging
import re

from . import classification_cache, document_repository, storage_service
from .config import settings
from .dify_client import dify_client, DifyInvocationError
from .models import ClassificationOutcome, DocumentStatus

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

    if not await _finalise_classification(document_id, classification):
        return

    try:
        extraction = await _run_extraction(document_id, document, classification["document_type"])
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

        try:
            classification = await _run_intake_coordinator(
                document_id, document, escalation_reason=escalation.get("reason")
            )
        except DifyInvocationError as exc:
            await _fail(document_id, f"Re-classification invocation failed: {exc}")
            return

        if not await _finalise_classification(document_id, classification):
            return

        try:
            extraction = await _run_extraction(document_id, document, classification["document_type"])
        except DifyInvocationError as exc:
            await _fail(
                document_id,
                f"Extraction invocation failed: {exc}",
                document_type=classification["document_type"],
            )
            return

        attempts += 1

    if extraction.get("escalation"):
        # Cap reached and Extraction is still unhappy with the classification —
        # don't loop forever, this needs a human. Persist the last attempted
        # type anyway — still useful context even though unresolved.
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
    logger.info("Document %s: classification+extraction complete", document_id)


async def _run_intake_coordinator(
    document_id: str, document, *, escalation_reason: str | None = None
) -> dict:
    """
    The Classifier/Intake Coordinator is a Dify Chatflow app (not a
    Workflow app like Extraction) — it reads the document itself via
    vision/file understanding rather than being fed pre-extracted text, so
    the file is attached to the chat message as a signed URL instead of
    being described through input fields.
    """
    signed_url = await asyncio.to_thread(
        storage_service.create_signed_url,
        document.storage_path,
        settings.SIGNED_URL_EXPIRY_SECONDS,
        document.filename,
    )

    inputs = {"document_id": document_id}
    if escalation_reason:
        inputs["escalation_reason"] = escalation_reason
        inputs["prior_classification"] = None  # filled in by caller if tracking is needed

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
    classification = _unwrap_chatflow_output(result)
    classification_cache.set_evidence(document_id, classification.get("evidence", []))
    return classification


def _dify_file_type(filename: str) -> str:
    return "document" if filename.lower().endswith(".pdf") else "image"


async def _run_extraction(document_id: str, document, document_type: str) -> dict:
    """
    Extraction's Start node requires a file-type input variable named
    input_file — same signed-URL pattern as the Classifier's `files`
    attachment, just passed as a workflow input instead of a chat file.
    """
    signed_url = await asyncio.to_thread(
        storage_service.create_signed_url,
        document.storage_path,
        settings.SIGNED_URL_EXPIRY_SECONDS,
        document.filename,
    )
    inputs = {
        "document_id": document_id,
        "document_type": document_type,
        "input_file": {
            "transfer_method": "remote_url",
            "url": signed_url,
            "type": _dify_file_type(document.filename),
        },
    }
    result = await dify_client.run_workflow_with_retry(
        settings.DIFY_EXTRACTION_WORKFLOW_ID, inputs, user=document_id
    )
    return _unwrap_workflow_output(result)


def _unwrap_workflow_output(result: dict) -> dict:
    """Dify's /workflows/run response nests actual outputs under data.outputs."""
    try:
        return result["data"]["outputs"]
    except (KeyError, TypeError):
        logger.error("Unexpected Dify workflow response shape: %s", result)
        return {"needs_manual_review": True}


def _unwrap_chatflow_output(result: dict) -> dict:
    """
    Dify's /chat-messages response returns the model's reply as free text
    under `answer` (unlike a workflow app's structured data.outputs). The
    Classifier chatflow replies with labelled markdown fields, e.g.:

        **Document type:** payslip
        **Confidence:** 1.0
        **Needs manual review:** False
        **Reasoning:** ...
        **Evidence:**
        - ...

    rather than JSON, so we pull each field out by its label instead of
    parsing the whole thing as a single structure.
    """
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
    """Like _answer_field, but captures everything up to the next **Label:** or end of text."""
    match = re.search(
        rf"\*\*{re.escape(label)}:\*\*\s*(.+?)(?=\n\s*\*\*[^*]+:\*\*|\Z)",
        answer,
        re.IGNORECASE | re.DOTALL,
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

    return {
        "document_type": document_type,
        "confidence": confidence,
        "needs_manual_review": needs_manual_review,
        "reasoning": reasoning,
        "evidence": evidence,
    }


def _is_unrecognised(document_type: str | None) -> bool:
    return document_type in (None, "", "unrecognised")


def _is_below_threshold(confidence: float | None) -> bool:
    return confidence is None or confidence < settings.CLASSIFICATION_CONFIDENCE_THRESHOLD


async def _finalise_classification(document_id: str, classification: dict) -> bool:
    """
    Records the classification-stage result (type, confidence, outcome) and
    enforces the confidence threshold as a system-level gate rather than
    trusting Dify's own needs_manual_review flag alone — that flag is still
    OR'd in as an extra signal for cases the numeric threshold alone
    wouldn't catch (e.g. the sub-classifiers disagreeing despite a
    survivable confidence number).

    Returns True if classification is accepted and extraction should run;
    False if it's been routed to manual review (FAILED already recorded).
    """
    document_type = classification.get("document_type")
    confidence = classification.get("confidence")
    unrecognised = _is_unrecognised(document_type)
    if unrecognised:
        # Normalise Dify's various ways of saying "no type" (None, "", or the
        # literal words null/none/n/a already folded to None by
        # _parse_chatflow_answer) to the explicit "unrecognised" sentinel so
        # it's what actually lands in Supabase/the frontend, not NULL.
        document_type = "unrecognised"
    below_threshold = _is_below_threshold(confidence)
    accepted = not unrecognised and not below_threshold and not classification.get("needs_manual_review")

    outcome = ClassificationOutcome.ACCEPTED if accepted else ClassificationOutcome.MANUAL_REVIEW
    await asyncio.to_thread(
        document_repository.record_classification,
        document_id,
        document_type=document_type,
        confidence=confidence,
        classification_outcome=outcome.value,
    )

    if accepted:
        return True

    if unrecognised:
        reason = "Document does not match any known category — flagged for manual review"
    elif below_threshold:
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
