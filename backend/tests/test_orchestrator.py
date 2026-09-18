from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app import orchestrator
from app.dify_client import DifyInvocationError
from app.models import DocumentRecord, DocumentStatus


def _make_document(document_id: str = "doc-1") -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename="payslip.pdf",
        storage_path=f"case-1/{document_id}_payslip.pdf",
        status=DocumentStatus.PROCESSING,
        uploaded_at="2026-08-26T00:00:00Z",
    )


def _chat_output(
    document_type: str | None = None,
    confidence: float = 0.0,
    needs_manual_review: bool | None = None,
    reasoning: str = "",
    evidence: list[str] | None = None,
    conversation_id: str = "conv-1",
) -> dict:
    """
    Wraps outputs the way the Classifier chatflow actually replies — a JSON
    object as the chat `answer` (see the "Validate Output" node), plus
    Dify's own top-level conversation_id (see _unwrap_chatflow_output).
    """
    if needs_manual_review is None:
        needs_manual_review = document_type is None
    payload = {
        "document_type": document_type,
        "confidence": confidence,
        "needs_manual_review": needs_manual_review,
        "reasoning": reasoning,
        "evidence": evidence or [],
    }
    return {"answer": json.dumps(payload), "conversation_id": conversation_id}


def _workflow_output(**outputs) -> dict:
    """Wraps outputs the way Dify's /workflows/run response nests them."""
    return {"data": {"outputs": outputs}}


def _extraction_output(
    extracted_fields: dict | None = None,
    confidence: float = 0.9,
    escalation: dict | None = None,
    field_confidence: float | None = None,
) -> dict:
    """
    Wraps outputs the way the Extraction workflow actually replies: a
    single `llm_output` string containing the JSON payload (see
    orchestrator._parse_extraction_output), not a raw outputs dict.

    Callers pass extracted_fields as a plain {name: value} dict for
    convenience — this wraps each value as {"value": ..., "confidence": ...}
    to match the real per-field-confidence prompt shape (see
    extraction_prompts.py). field_confidence defaults to the same value as
    the overall confidence unless a test needs them to differ.
    """
    resolved_field_confidence = confidence if field_confidence is None else field_confidence
    wrapped_fields = {
        name: {"value": value, "confidence": resolved_field_confidence}
        for name, value in (extracted_fields or {}).items()
    }
    payload = {
        "extracted_fields": wrapped_fields,
        "confidence": confidence,
        "escalation": escalation,
    }
    return _workflow_output(llm_output=json.dumps(payload))


def _stub_ingestion(
    mock_storage, mock_ingest, raw_text: str = "sample document text", metadata: dict | None = None
) -> None:
    """
    Any test that reaches Extraction goes through _extract_raw_text first,
    which downloads the file then runs it through ingestion_service.ingest_document
    — stub both so that step succeeds without touching real bytes/OCR.
    """
    mock_storage.download_file.return_value = b"%PDF-1.4 fake bytes"
    mock_ingest.return_value = {
        "raw_text": raw_text,
        "needs_review": False,
        "extraction_method": "native_pdf",
        "metadata": metadata if metadata is not None else {},
    }


@pytest.mark.asyncio
async def test_document_not_found_returns_without_updating_status() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = None

        await orchestrator.process_document("missing-doc")

        mock_repo.update_status.assert_not_called()
        mock_dify.run_chatflow_with_retry.assert_not_called()
        mock_dify.run_workflow_with_retry.assert_not_called()


@pytest.mark.asyncio
async def test_no_escalation_marks_analyzed() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_extraction_output(extracted_fields={"individual_name": "Jane Tan"}, confidence=0.95)
        )

        await orchestrator.process_document("doc-1")

        assert mock_dify.run_chatflow_with_retry.await_count == 1
        assert mock_dify.run_workflow_with_retry.await_count == 1
        mock_repo.update_status.assert_called_once_with(
            "doc-1", DocumentStatus.ANALYZED, document_type="payslip"
        )
        mock_repo.update_extracted_fields.assert_called_once_with(
            "doc-1", {"individual_name": "Jane Tan"}, 0.95, ["payslip"]
        )
        # Also mirrored into the normalised one-row-per-field table — with
        # required_field_names drawn from payslip's own extraction schema so
        # field_category can distinguish "required" from LLM-added extras.
        mock_fields_repo.replace_extracted_fields.assert_called_once_with(
            "doc-1",
            {"individual_name": "Jane Tan"},
            field_confidences={"individual_name": 0.95},
            default_confidence=0.95,
            required_field_names={"individual_name", "company_name", "net_pay", "company_address"},
        )


@pytest.mark.asyncio
async def test_extracted_address_adds_proof_of_address_fulfilment() -> None:
    """
    An ID document whose Extraction output actually carries an address
    fulfils proof_of_address too — decided here from the extracted field,
    not from the Classifier's own prose judgement (see
    models.compute_fulfilled_requirements).
    """
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.case_status") as mock_case_status,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="nric", confidence=0.95, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_extraction_output(
                extracted_fields={"individual_name": "Jane Tan", "individual_address": "1 Main St"},
                confidence=0.9,
            )
        )

        await orchestrator.process_document("doc-1")

        mock_repo.update_extracted_fields.assert_called_once_with(
            "doc-1",
            {"individual_name": "Jane Tan", "individual_address": "1 Main St"},
            0.9,
            ["id_document", "proof_of_address"],
        )
        # Synced again after extraction (on top of the classification-time
        # sync) since proof_of_address coverage can only become true here.
        assert mock_case_status.sync_submission_finalized.call_count == 2


@pytest.mark.asyncio
async def test_metadata_persisted_when_present() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(
            mock_storage, mock_ingest, metadata={"Author": "Jane Tan", "Software": "Acrobat"}
        )
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_extraction_output(extracted_fields={"individual_name": "Jane Tan"})
        )

        await orchestrator.process_document("doc-1")

        # Persisted as soon as it's available (during raw-text extraction),
        # independent of whether classification/Extraction later succeed.
        mock_repo.update_metadata.assert_called_once_with(
            "doc-1", {"Author": "Jane Tan", "Software": "Acrobat"}
        )


@pytest.mark.asyncio
async def test_metadata_not_persisted_when_empty() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest, metadata={})
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_extraction_output(extracted_fields={"individual_name": "Jane Tan"})
        )

        await orchestrator.process_document("doc-1")

        mock_repo.update_metadata.assert_not_called()


@pytest.mark.asyncio
async def test_accepted_classification_syncs_case_submission_finalized() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = _make_document()  # case_id="case-1"
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_extraction_output(extracted_fields={"individual_name": "Jane Tan"})
        )

        await orchestrator.process_document("doc-1")

        mock_case_status.sync_submission_finalized.assert_called_once_with("case-1")


@pytest.mark.asyncio
async def test_manual_review_classification_still_syncs_case_submission_finalized() -> None:
    """
    A manual-review outcome can still change coverage (e.g. a retry that
    reclassifies a document AWAY from something it used to fulfil), so the
    sync must not be skipped just because classification wasn't accepted.
    """
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = _make_document()  # case_id="case-1"
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=True)
        )

        await orchestrator.process_document("doc-1")

        mock_case_status.sync_submission_finalized.assert_called_once_with("case-1")


@pytest.mark.asyncio
async def test_unassigned_document_does_not_sync_submission_finalized() -> None:
    document = _make_document()
    document.case_id = None

    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = document
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(return_value=_workflow_output(document_type="payslip"))

        await orchestrator.process_document("doc-1")

        mock_case_status.sync_submission_finalized.assert_not_called()


@pytest.mark.asyncio
async def test_intake_coordinator_uploads_signed_url_of_the_document() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        document = _make_document()
        mock_repo.get_document.return_value = document
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_extraction_output(extracted_fields={"individual_name": "Jane Tan"})
        )

        await orchestrator.process_document("doc-1")

        # Called once for the Classifier's chat `files` attachment and once
        # more for Extraction's `input_file` workflow input — both need the
        # same document, so both get a signed URL for it.
        assert mock_storage.create_signed_url.call_count == 2
        mock_storage.create_signed_url.assert_called_with(
            document.storage_path, orchestrator.settings.SIGNED_URL_EXPIRY_SECONDS, document.filename
        )
        args, kwargs = mock_dify.run_chatflow_with_retry.call_args
        assert args[0] == orchestrator.settings.DIFY_CLASSIFIER_API_KEY
        assert args[1] == {"document_id": "doc-1", "extracted_text": "sample document text"}
        assert kwargs["query"] == "Classify this document"
        assert kwargs["user"] == "doc-1"
        assert kwargs["files"] == [
            {
                "type": "document",
                "transfer_method": "remote_url",
                "url": "https://signed.example/doc.pdf",
            }
        ]

        # Extraction's Start node requires a file-type `input_file` input —
        # this was missing entirely before and Dify rejected every call
        # with "input_file is required in input form".
        extraction_args, extraction_kwargs = mock_dify.run_workflow_with_retry.call_args
        assert extraction_args[1]["input_file"] == {
            "transfer_method": "remote_url",
            "url": "https://signed.example/doc.pdf",
            "type": "document",
        }
        assert extraction_kwargs["user"] == "doc-1"


@pytest.mark.asyncio
async def test_typed_low_confidence_classification_keeps_its_type_for_extraction() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.2, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_extraction_output(extracted_fields={"company_name": "Acme"}, confidence=0.4)
        )

        await orchestrator.process_document("doc-1")

        # A real type guess that's just below the confidence threshold is no
        # longer flattened to "unrecognised" the way a genuinely typeless
        # guess is (see test_unrecognised_classification_still_runs_general_
        # extraction) — the named type is kept so Extraction runs against
        # payslip's own specific schema right away instead of the generic
        # one, and so the frontend shows what the Classifier actually
        # thinks this is. classification_outcome still records
        # manual_review either way — only what Extraction has to work with
        # changes, not whether the document reads as confidently
        # classified. If Extraction can actually tell it's something else,
        # it escalates back to the Classifier (see the escalation tests)
        # instead of the document being stuck on a bad first guess.
        assert mock_dify.run_chatflow_with_retry.await_count == 1
        assert mock_dify.run_workflow_with_retry.await_count == 1
        mock_repo.record_classification.assert_called_once_with(
            "doc-1",
            document_type="payslip",
            confidence=0.2,
            classification_outcome="manual_review",
            evidence=[],
            # A self-report only — case_status.compute_requirement_coverage
            # still ignores this since classification_outcome isn't
            # ACCEPTED, so storing "payslip" here rather than [] doesn't
            # let an unconfirmed guess retire a checklist item.
            fulfilled_requirements=["payslip"],
        )
        # Confirms Extraction actually ran against payslip's own specific
        # prompt (REQUIRED fields, asks for net_pay) rather than the
        # generic unrecognised one ("could not be matched to any known...
        # category").
        extraction_prompt = mock_dify.run_workflow_with_retry.call_args[0][1]["extraction_prompt"]
        assert "REQUIRED fields" in extraction_prompt
        assert "net_pay" in extraction_prompt
        assert "could not be matched to any known" not in extraction_prompt
        mock_repo.update_status.assert_called_once_with(
            "doc-1", DocumentStatus.ANALYZED, document_type="payslip"
        )


@pytest.mark.asyncio
async def test_unrecognised_classification_still_runs_general_extraction() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type=None, confidence=0.2, needs_manual_review=True)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_extraction_output(extracted_fields={"company_name": "Acme"}, confidence=0.4)
        )

        await orchestrator.process_document("doc-1")

        # Classification is still recorded as manual_review (the type guess
        # had no real category at all), but Extraction runs anyway — with
        # the general/loan-application field list build_extraction_prompt
        # falls back to for document_type == "unrecognised" — rather than
        # being skipped outright, so a reviewer gets something to look at.
        assert mock_dify.run_chatflow_with_retry.await_count == 1
        assert mock_dify.run_workflow_with_retry.await_count == 1
        mock_repo.record_classification.assert_called_once_with(
            "doc-1",
            document_type="unrecognised",
            confidence=0.2,
            classification_outcome="manual_review",
            evidence=[],
            fulfilled_requirements=[],
        )
        mock_repo.update_status.assert_called_once_with(
            "doc-1", DocumentStatus.ANALYZED, document_type="unrecognised"
        )


@pytest.mark.asyncio
async def test_escalation_resolves_on_reclassification() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            side_effect=[
                # 1st Intake Coordinator call
                _chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False),
                # Re-run Intake Coordinator with escalation context
                _chat_output(document_type="list_of_loans", confidence=0.85, needs_manual_review=False),
            ]
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            side_effect=[
                # 1st Extraction call — escalates
                _extraction_output(escalation={"reason": "reads like a shipping manifest"}),
                # 2nd Extraction call — no escalation this time
                _extraction_output(extracted_fields={"loan_amount": "SGD 10,000"}, confidence=0.85),
            ]
        )

        await orchestrator.process_document("doc-1")

        assert mock_dify.run_chatflow_with_retry.await_count == 2
        assert mock_dify.run_workflow_with_retry.await_count == 2
        # document_type reflects the FINAL (post-escalation) classification,
        # not the original "payslip" guess that got escalated away.
        mock_repo.update_status.assert_called_once_with(
            "doc-1", DocumentStatus.ANALYZED, document_type="list_of_loans"
        )
        # Persisted so the frontend can show "classification was re-run" even
        # though the document ultimately resolved cleanly.
        mock_repo.record_escalation.assert_called_once_with(
            "doc-1", "reads like a shipping manifest"
        )


@pytest.mark.asyncio
async def test_escalation_cap_routes_to_manual_review() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.settings") as mock_settings,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_settings.MAX_RECLASSIFICATION_ATTEMPTS = 2
        mock_settings.CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.6
        mock_settings.SIGNED_URL_EXPIRY_SECONDS = 300
        mock_settings.DIFY_CLASSIFIER_API_KEY = "classifier-key"
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)

        classifying = _chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        escalating = _extraction_output(escalation={"reason": "still doesn't fit"})
        # Intake -> Extraction(escalate) -> Intake -> Extraction(escalate)
        # -> Intake -> Extraction(escalate) : cap of 2 attempts exhausted
        mock_dify.run_chatflow_with_retry = AsyncMock(
            side_effect=[classifying, classifying, classifying]
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            side_effect=[escalating, escalating, escalating]
        )

        await orchestrator.process_document("doc-1")

        args, kwargs = mock_repo.update_status.call_args
        assert args[0] == "doc-1"
        assert args[1] == DocumentStatus.FAILED
        assert "Unresolved after 2 reclassification" in kwargs["failure_reason"]
        assert "still doesn't fit" in kwargs["failure_reason"]
        assert kwargs["document_type"] == "payslip"
        # Escalation is recorded on every attempt, not just the first —
        # each one is a real re-run of Intake Coordinator.
        assert mock_repo.record_escalation.call_count == 2
        mock_repo.record_escalation.assert_called_with("doc-1", "still doesn't fit")


async def test_escalation_cap_still_persists_whatever_extraction_found() -> None:
    """
    Regression test: a document that never resolves its escalation (routed
    to FAILED) shouldn't lose whatever fields Extraction actually pulled
    out along the way — discarding them just because the *type* was never
    confidently resolved means manual review starts from nothing instead
    of the real data that was already there.
    """
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.settings") as mock_settings,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_settings.MAX_RECLASSIFICATION_ATTEMPTS = 1
        mock_settings.CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.6
        mock_settings.SIGNED_URL_EXPIRY_SECONDS = 300
        mock_settings.DIFY_CLASSIFIER_API_KEY = "classifier-key"
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)

        classifying = _chat_output(document_type="unrecognised", confidence=0.5)
        # Extraction found real values even though it couldn't resolve the
        # document type — this is exactly what should survive onto the
        # FAILED document instead of being thrown away.
        escalating = _extraction_output(
            extracted_fields={"account_number": "1008616304738692", "broker_address": "10 Marina Blvd"},
            confidence=0.72,
            escalation={"reason": "looks like a brokerage statement"},
        )
        mock_dify.run_chatflow_with_retry = AsyncMock(side_effect=[classifying, classifying])
        mock_dify.run_workflow_with_retry = AsyncMock(side_effect=[escalating, escalating])

        await orchestrator.process_document("doc-1")

        # Persisted (not skipped) despite the document ending up FAILED.
        mock_repo.update_extracted_fields.assert_called_once()
        persisted_fields = mock_repo.update_extracted_fields.call_args[0][1]
        persisted_confidence = mock_repo.update_extracted_fields.call_args[0][2]
        assert persisted_fields == {
            "account_number": "1008616304738692",
            "broker_address": "10 Marina Blvd",
        }
        assert persisted_confidence == 0.72
        mock_fields_repo.replace_extracted_fields.assert_called_once()

        # Still correctly ends up FAILED, not silently ANALYZED.
        args, kwargs = mock_repo.update_status.call_args
        assert args[1] == DocumentStatus.FAILED


@pytest.mark.asyncio
async def test_intake_coordinator_invocation_failure_marks_failed() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(side_effect=DifyInvocationError("network blip"))

        await orchestrator.process_document("doc-1")

        args, kwargs = mock_repo.update_status.call_args
        assert args[0] == "doc-1"
        assert args[1] == DocumentStatus.FAILED
        assert "Intake Coordinator invocation failed" in kwargs["failure_reason"]
        # No classification was ever obtained on this path — nothing to persist.
        assert kwargs["document_type"] is None


@pytest.mark.asyncio
async def test_extraction_invocation_failure_marks_failed() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(side_effect=DifyInvocationError("network blip"))

        await orchestrator.process_document("doc-1")

        args, kwargs = mock_repo.update_status.call_args
        assert args[0] == "doc-1"
        assert args[1] == DocumentStatus.FAILED
        assert "Extraction invocation failed" in kwargs["failure_reason"]
        assert kwargs["document_type"] == "payslip"


def test_parse_extraction_output_splits_value_and_confidence_per_field() -> None:
    llm_output = json.dumps({
        "extracted_fields": {
            "bank_name": {"value": "OCBC", "confidence": 0.95},
            "account_number": {"value": None, "confidence": 0.2},
        },
        "confidence": 0.8,
        "escalation": None,
    })

    result = orchestrator._parse_extraction_output(llm_output)

    assert result["extracted_fields"] == {"bank_name": "OCBC", "account_number": None}
    assert result["field_confidences"] == {"bank_name": 0.95, "account_number": 0.2}
    assert result["confidence"] == 0.8


def test_parse_extraction_output_strips_markdown_code_fence() -> None:
    # Real LLM output sometimes wraps the JSON in a ```json ... ``` fence
    # despite the prompt asking for raw JSON — this should be stripped
    # rather than failing to parse.
    payload = json.dumps({"extracted_fields": {}, "confidence": 0.7, "escalation": None})
    llm_output = f"```json\n{payload}\n```"

    result = orchestrator._parse_extraction_output(llm_output)

    assert result["confidence"] == 0.7


def test_parse_extraction_output_falls_back_for_unwrapped_field() -> None:
    # Defensive case: the LLM didn't follow the {"value", "confidence"}
    # shape for one field — its bare value should still come through, just
    # with an unknown (None) per-field confidence rather than crashing or
    # dropping the field.
    llm_output = json.dumps({
        "extracted_fields": {"bank_name": "OCBC"},
        "confidence": 0.8,
        "escalation": None,
    })

    result = orchestrator._parse_extraction_output(llm_output)

    assert result["extracted_fields"] == {"bank_name": "OCBC"}
    assert result["field_confidences"] == {"bank_name": None}


def test_parse_extraction_output_malformed_json_returns_escalation() -> None:
    # A refusal/prose reply instead of JSON (e.g. the LLM declined to
    # extract) shouldn't crash the pipeline — it should read as an
    # escalation like any other extraction-side objection.
    result = orchestrator._parse_extraction_output("Sorry, I cannot process this document.")

    assert result["extracted_fields"] == {}
    assert result["escalation"] is not None


def test_parse_extraction_output_malformed_json_escalation_includes_model_text() -> None:
    # The model's prose often still contains its actual finding (e.g. "no
    # individual_name present") even when it fails to wrap that in JSON —
    # the escalation reason needs to carry that excerpt through rather than
    # a fixed, contentless message, since it's what the reclassification
    # retry (_reconsideration_query) and the eventual FAILED reason both
    # read from.
    result = orchestrator._parse_extraction_output(
        "Missing fields: individual_name is not present on this document."
    )

    assert "individual_name is not present" in result["escalation"]["reason"]


def test_parse_extraction_output_strips_reasoning_model_think_block() -> None:
    # DeepSeek-style reasoning models prepend a <think>...</think> block of
    # chain-of-thought before the actual JSON answer.
    llm_output = (
        "<think>some reasoning</think>"
        '{"extracted_fields": {"individual_name": {"value": "Jane Tan", "confidence": 0.98}}, '
        '"confidence": 0.98, "escalation": null}'
    )

    result = orchestrator._parse_extraction_output(llm_output)

    assert result["extracted_fields"] == {"individual_name": "Jane Tan"}


def test_unwrap_chatflow_output_parses_real_dify_json_reply() -> None:
    # Actual shape observed from the Classifier chatflow's "Validate Output"
    # node — a single JSON object as the chat answer, plus Dify's own
    # top-level conversation_id.
    answer = json.dumps(
        {
            "document_type": "payslip",
            "confidence": 1.0,
            "needs_manual_review": False,
            "reasoning": (
                "All three classification methods independently and "
                "unanimously identified the document as a payslip with high confidence."
            ),
            "evidence": [
                "Rule-based matched patterns: 'pay slip', 'net pay', 'basic salary'",
                "LLM identified employee name, pay period December 2025",
                "Vision model confirmed employee name Tan Wei Jun, net pay SGD 3,346.04",
            ],
        }
    )

    result = orchestrator._unwrap_chatflow_output({"answer": answer, "conversation_id": "conv-1"})

    assert result["document_type"] == "payslip"
    assert result["confidence"] == 1.0
    assert result["needs_manual_review"] is False
    assert "unanimously identified" in result["reasoning"]
    assert result["evidence"] == [
        "Rule-based matched patterns: 'pay slip', 'net pay', 'basic salary'",
        "LLM identified employee name, pay period December 2025",
        "Vision model confirmed employee name Tan Wei Jun, net pay SGD 3,346.04",
    ]
    assert result["conversation_id"] == "conv-1"


def test_unwrap_chatflow_output_normalises_multi_word_document_type() -> None:
    answer = json.dumps(
        {"document_type": "Certificate of Incorporation", "confidence": 0.8, "needs_manual_review": False}
    )

    result = orchestrator._unwrap_chatflow_output({"answer": answer})

    assert result["document_type"] == "certificate_of_incorporation"


def test_unwrap_chatflow_output_treats_null_document_type_as_none() -> None:
    answer = json.dumps({"document_type": None, "confidence": 0.0, "needs_manual_review": True})

    result = orchestrator._unwrap_chatflow_output({"answer": answer})

    assert result["document_type"] is None
    assert result["needs_manual_review"] is True


def test_unwrap_chatflow_output_defaults_needs_manual_review_when_field_missing() -> None:
    # No "needs_manual_review" key at all — falls back to "no document type
    # found" rather than crashing on a missing field.
    result = orchestrator._unwrap_chatflow_output({"answer": json.dumps({"confidence": 0.5})})

    assert result["document_type"] is None
    assert result["needs_manual_review"] is True


def test_unwrap_chatflow_output_missing_answer_key_falls_back_to_manual_review() -> None:
    result = orchestrator._unwrap_chatflow_output({"unexpected": "shape"})

    assert result == {"needs_manual_review": True}


def test_finalise_classification_records_self_fulfilment_only() -> None:
    """
    fulfilled_requirements at classification time is trivial self-fulfilment
    only — proof_of_address cross-fulfilment now depends on
    individual_address, which isn't known until Extraction runs (see
    models.compute_fulfilled_requirements and the recompute in
    orchestrator._process_document after extraction succeeds).
    """
    result = orchestrator._unwrap_chatflow_output(
        {
            "answer": json.dumps(
                {"document_type": "nric", "confidence": 0.95, "needs_manual_review": False}
            )
        }
    )

    assert "fulfilled_requirements" not in result


@pytest.mark.asyncio
async def test_unhandled_error_is_swallowed_even_when_recording_failure_also_fails() -> None:
    # process_document's own last-line-of-defence: an exception _process_document
    # doesn't translate itself (e.g. a Supabase read blowing up before
    # classification even starts) must never propagate out of process_document,
    # even if the attempt to record FAILED status *also* raises.
    with patch("app.orchestrator.document_repository") as mock_repo:
        mock_repo.get_document.side_effect = RuntimeError("db unreachable")
        mock_repo.update_status.side_effect = RuntimeError("also unreachable")

        await orchestrator.process_document("doc-1")

        mock_repo.update_status.assert_called_once()


@pytest.mark.asyncio
async def test_download_failure_marks_failed() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository"),
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document"),
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.download_file.side_effect = RuntimeError("bucket unreachable")

        await orchestrator.process_document("doc-1")

        args, kwargs = mock_repo.update_status.call_args
        assert args[1] == DocumentStatus.FAILED
        assert "Failed to download document for extraction" in kwargs["failure_reason"]
        mock_dify.run_chatflow_with_retry.assert_not_called()


@pytest.mark.asyncio
async def test_text_extraction_failure_marks_failed() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository"),
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.download_file.return_value = b"%PDF-1.4 fake bytes"
        mock_ingest.side_effect = RuntimeError("ocr engine crashed")

        await orchestrator.process_document("doc-1")

        args, kwargs = mock_repo.update_status.call_args
        assert args[1] == DocumentStatus.FAILED
        assert "Text extraction failed" in kwargs["failure_reason"]
        mock_dify.run_chatflow_with_retry.assert_not_called()


@pytest.mark.asyncio
async def test_ingestion_needs_review_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository"),
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_ingest.return_value["needs_review"] = True
        mock_ingest.return_value["extraction_method"] = "ocr"
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_extraction_output(extracted_fields={"individual_name": "Jane Tan"})
        )

        with caplog.at_level(logging.WARNING, logger="classifier_pipeline.orchestrator"):
            await orchestrator.process_document("doc-1")

        assert any("needs_review" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_empty_extracted_text_after_classification_marks_failed() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository"),
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest, raw_text="   ")
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )

        await orchestrator.process_document("doc-1")

        args, kwargs = mock_repo.update_status.call_args
        assert args[1] == DocumentStatus.FAILED
        assert kwargs["failure_reason"] == "Extraction produced no usable text"
        assert kwargs["document_type"] == "payslip"
        mock_dify.run_workflow_with_retry.assert_not_called()


@pytest.mark.asyncio
async def test_escalation_reclassification_invocation_failure_marks_failed() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository"),
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            side_effect=[
                _chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False),
                DifyInvocationError("network blip"),
            ]
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_extraction_output(escalation={"reason": "doesn't fit"})
        )

        await orchestrator.process_document("doc-1")

        args, kwargs = mock_repo.update_status.call_args
        assert args[1] == DocumentStatus.FAILED
        assert "Re-classification invocation failed" in kwargs["failure_reason"]


@pytest.mark.asyncio
async def test_escalation_extraction_invocation_failure_marks_failed() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository"),
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.ingest_document") as mock_ingest,
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status"),
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        _stub_ingestion(mock_storage, mock_ingest)
        mock_dify.run_chatflow_with_retry = AsyncMock(
            side_effect=[
                _chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False),
                _chat_output(document_type="list_of_loans", confidence=0.85, needs_manual_review=False),
            ]
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            side_effect=[
                _extraction_output(escalation={"reason": "doesn't fit"}),
                DifyInvocationError("network blip"),
            ]
        )

        await orchestrator.process_document("doc-1")

        args, kwargs = mock_repo.update_status.call_args
        assert args[1] == DocumentStatus.FAILED
        assert "Extraction invocation failed" in kwargs["failure_reason"]
        assert kwargs["document_type"] == "list_of_loans"


@pytest.mark.asyncio
async def test_run_extraction_unknown_schema_returns_escalation() -> None:
    # document_type made it past classification (e.g. the Classifier
    # returned some string outside KNOWN_DOCUMENT_TYPES with high
    # confidence) but there's no extraction schema for it — this shouldn't
    # crash, it should escalate back to the classifier like any other
    # extraction-side objection.
    document = _make_document()

    with patch("app.orchestrator.storage_service") as mock_storage:
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"

        result = await orchestrator._run_extraction("doc-1", document, "not_a_real_type", "some text")

    assert result["extracted_fields"] == {}
    assert result["field_confidences"] == {}
    assert result["confidence"] == 0.0
    assert "No extraction schema defined" in result["escalation"]["reason"]


def test_unwrap_workflow_output_malformed_shape_falls_back() -> None:
    result = orchestrator._unwrap_workflow_output({"unexpected": "shape"})

    assert result == {"needs_manual_review": True}


def test_unwrap_chatflow_output_invalid_json_answer_falls_back_to_manual_review() -> None:
    result = orchestrator._unwrap_chatflow_output(
        {"answer": "not json at all", "conversation_id": "conv-9"}
    )

    assert result == {"needs_manual_review": True, "conversation_id": "conv-9"}


def test_unwrap_chatflow_output_invalid_confidence_defaults_to_zero() -> None:
    answer = json.dumps(
        {"document_type": "payslip", "confidence": "very confident", "needs_manual_review": False}
    )

    result = orchestrator._unwrap_chatflow_output({"answer": answer})

    assert result["confidence"] == 0.0


def test_reconsideration_query_default_phrasing() -> None:
    query = orchestrator._reconsideration_query("looks off", [])

    assert query.startswith("Extraction judged your classification incorrect: looks off")
    assert "Evidence" not in query


def test_reconsideration_query_with_suggested_document_type() -> None:
    query = orchestrator._reconsideration_query("reads like a loan letter", [], "list_of_loans")

    assert "may actually be a list_of_loans" in query


def test_reconsideration_query_with_evidence_lists_bullets() -> None:
    query = orchestrator._reconsideration_query("doesn't fit", ["Term Loan", "Facility Details"])

    assert "- Term Loan" in query
    assert "- Facility Details" in query
