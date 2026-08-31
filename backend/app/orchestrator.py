from __future__ import annotations

import asyncio
import logging

from . import document_repository
from .config import settings
from .dify_client import dify_client, DifyInvocationError
from .models import DocumentStatus

logger = logging.getLogger("classifier_pipeline.orchestrator")


class OrchestrationError(Exception):
    """Raised when a document can't be progressed through classification/extraction at all."""


async def process_document(document_id: str) -> None:
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
    around this (see queue_service._process_document).
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

    if classification.get("needs_manual_review"):
        # Still persist the best-guess type even though it's below threshold —
        # per classification.py's own design, a low-confidence guess is
        # useful context for manual review, not something to discard.
        await _fail(
            document_id,
            f"Classification confidence below threshold ({classification.get('confidence')})",
            document_type=classification.get("document_type"),
        )
        return

    try:
        extraction = await _run_extraction(document_id, classification["document_type"])
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

        if classification.get("needs_manual_review"):
            await _fail(
                document_id,
                f"Re-classification still below confidence threshold ({classification.get('confidence')})",
                document_type=classification.get("document_type"),
            )
            return

        try:
            extraction = await _run_extraction(document_id, classification["document_type"])
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
    inputs = {
        "document_id": document_id,
        "filename": document.filename,
        "extracted_text": "",  # populated by the workflow's own text-extraction step
        "confidence_threshold": settings.CLASSIFICATION_CONFIDENCE_THRESHOLD,
    }
    if escalation_reason:
        inputs["escalation_reason"] = escalation_reason
        inputs["prior_classification"] = None  # filled in by caller if tracking is needed

    result = await dify_client.run_workflow(settings.DIFY_INTAKE_COORDINATOR_WORKFLOW_ID, inputs)
    return _unwrap_workflow_output(result)


async def _run_extraction(document_id: str, document_type: str) -> dict:
    inputs = {"document_id": document_id, "document_type": document_type}
    result = await dify_client.run_workflow(settings.DIFY_EXTRACTION_WORKFLOW_ID, inputs)
    return _unwrap_workflow_output(result)


def _unwrap_workflow_output(result: dict) -> dict:
    """Dify's /workflows/run response nests actual outputs under data.outputs."""
    try:
        return result["data"]["outputs"]
    except (KeyError, TypeError):
        logger.error("Unexpected Dify workflow response shape: %s", result)
        return {"needs_manual_review": True}


async def _fail(document_id: str, reason: str, *, document_type: str | None = None) -> None:
    await asyncio.to_thread(
        document_repository.update_status,
        document_id,
        DocumentStatus.FAILED,
        failure_reason=reason,
        document_type=document_type,
    )
    logger.warning("Document %s marked FAILED — manual review required: %s", document_id, reason)
