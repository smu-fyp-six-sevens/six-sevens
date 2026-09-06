from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re

from . import document_repository, storage_service
from .config import settings
from .dify_client import dify_client, DifyInvocationError
from .extraction_prompts import build_extraction_prompt
from .ingestion.ingestion_service import ingest_document
from .models import DocumentStatus

logger = logging.getLogger("classifier_pipeline.orchestrator")


class OrchestrationError(Exception):
    """Raised when a document can't be progressed through classification/extraction at all."""


async def process_document(document_id: str) -> None:
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
        await _fail(
            document_id,
            f"Classification confidence below threshold ({classification.get('confidence')})",
            document_type=classification.get("document_type"),
        )
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

    # NEW: persist extracted fields before marking ANALYZED
    await asyncio.to_thread(
        document_repository.update_extracted_fields,
        document_id,
        extraction.get("extracted_fields", {}),
        extraction.get("confidence", 0.0),
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

    return {
        "document_type": document_type,
        "confidence": confidence,
        "needs_manual_review": needs_manual_review,
        "reasoning": reasoning,
        "evidence": evidence,
    }


async def _fail(document_id: str, reason: str, *, document_type: str | None = None) -> None:
    await asyncio.to_thread(
        document_repository.update_status,
        document_id,
        DocumentStatus.FAILED,
        failure_reason=reason,
        document_type=document_type,
    )
    logger.warning("Document %s marked FAILED — manual review required: %s", document_id, reason)