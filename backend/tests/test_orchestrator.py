from __future__ import annotations

import json
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
) -> dict:
    """
    Wraps outputs the way the Classifier chatflow actually replies — labelled
    markdown fields, not JSON (see _unwrap_chatflow_output).
    """
    if needs_manual_review is None:
        needs_manual_review = document_type is None
    lines = [
        f"**Document type:** {document_type if document_type is not None else 'null'}",
        f"**Confidence:** {confidence}",
        f"**Needs manual review:** {needs_manual_review}",
    ]
    if reasoning:
        lines.append(f"**Reasoning:** {reasoning}")
    if evidence:
        lines.append("**Evidence:**")
        lines.extend(f"- {item}" for item in evidence)
    return {"answer": "\n".join(lines)}


def _workflow_output(**outputs) -> dict:
    """Wraps outputs the way Dify's /workflows/run response nests them."""
    return {"data": {"outputs": outputs}}


def _extraction_output(
    extracted_fields: dict | None = None,
    confidence: float = 0.9,
    escalation: dict | None = None,
) -> dict:
    """
    Wraps outputs the way the Extraction workflow actually replies: a
    single `llm_output` string containing the JSON payload (see
    orchestrator._parse_extraction_output), not a raw outputs dict.
    """
    payload = {
        "extracted_fields": extracted_fields or {},
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
            confidence=0.95,
            required_field_names={"individual_name", "company_name", "net_pay"},
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
            return_value=_chat_output(document_type="id_document", confidence=0.95, needs_manual_review=False)
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
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = _make_document()  # case_id="case-1"
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(return_value=_workflow_output(document_type="payslip"))

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
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = _make_document()  # case_id="case-1"
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
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
        patch("app.orchestrator.dify_client") as mock_dify,
        patch("app.orchestrator.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = document
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
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
        assert args[1] == {"document_id": "doc-1"}
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
async def test_typed_low_confidence_classification_fails_before_extraction() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.2, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock()

        await orchestrator.process_document("doc-1")

        # A real type guess that's just below the confidence threshold still
        # fails before Extraction ever runs — unlike "unrecognised" (see
        # test_unrecognised_classification_still_runs_general_extraction),
        # there's no fallback field list to fall back to here.
        assert mock_dify.run_chatflow_with_retry.await_count == 1
        mock_dify.run_workflow_with_retry.assert_not_called()
        args, kwargs = mock_repo.update_status.call_args
        assert args[0] == "doc-1"
        assert args[1] == DocumentStatus.FAILED
        assert kwargs["document_type"] == "payslip"


@pytest.mark.asyncio
async def test_unrecognised_classification_still_runs_general_extraction() -> None:
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


@pytest.mark.asyncio
async def test_intake_coordinator_invocation_failure_marks_failed() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.extracted_fields_repository") as mock_fields_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
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


def test_unwrap_chatflow_output_parses_real_dify_markdown_reply() -> None:
    # Actual shape observed from the Classifier chatflow in Dify.
    answer = (
        "**Document type:** payslip\n"
        "**Confidence:** 1.0\n"
        "**Needs manual review:** False\n\n"
        "**Reasoning:** All three classification methods independently and "
        "unanimously identified the document as a payslip with high confidence.\n\n"
        "**Evidence:**\n"
        "- Rule-based matched patterns: 'pay slip', 'net pay', 'basic salary'\n"
        "- LLM identified employee name, pay period December 2025\n"
        "- Vision model confirmed employee name Tan Wei Jun, net pay SGD 3,346.04\n"
    )

    result = orchestrator._unwrap_chatflow_output({"answer": answer})

    assert result["document_type"] == "payslip"
    assert result["confidence"] == 1.0
    assert result["needs_manual_review"] is False
    assert "unanimously identified" in result["reasoning"]
    assert result["evidence"] == [
        "Rule-based matched patterns: 'pay slip', 'net pay', 'basic salary'",
        "LLM identified employee name, pay period December 2025",
        "Vision model confirmed employee name Tan Wei Jun, net pay SGD 3,346.04",
    ]


def test_unwrap_chatflow_output_normalises_multi_word_document_type() -> None:
    answer = "**Document type:** Certificate of Incorporation\n**Confidence:** 0.8\n**Needs manual review:** False\n"

    result = orchestrator._unwrap_chatflow_output({"answer": answer})

    assert result["document_type"] == "certificate_of_incorporation"


def test_unwrap_chatflow_output_treats_null_document_type_as_none() -> None:
    answer = "**Document type:** null\n**Confidence:** 0.0\n**Needs manual review:** True\n"

    result = orchestrator._unwrap_chatflow_output({"answer": answer})

    assert result["document_type"] is None
    assert result["needs_manual_review"] is True


def test_unwrap_chatflow_output_defaults_needs_manual_review_when_field_missing() -> None:
    # No "**Needs manual review:**" field at all — falls back to
    # "no document type found" rather than crashing on a missing field.
    result = orchestrator._unwrap_chatflow_output({"answer": "**Confidence:** 0.5\n"})

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
            "answer": (
                "**Document type:** id_document\n"
                "**Confidence:** 0.95\n"
                "**Needs manual review:** False\n"
            )
        }
    )

    assert "fulfilled_requirements" not in result
