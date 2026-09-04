from __future__ import annotations

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


@pytest.mark.asyncio
async def test_document_not_found_returns_without_updating_status() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
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
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_workflow_output(document_type="payslip")  # no "escalation" key
        )

        await orchestrator.process_document("doc-1")

        assert mock_dify.run_chatflow_with_retry.await_count == 1
        assert mock_dify.run_workflow_with_retry.await_count == 1
        mock_repo.update_status.assert_called_once_with(
            "doc-1", DocumentStatus.ANALYZED, document_type="payslip"
        )


@pytest.mark.asyncio
async def test_intake_coordinator_uploads_signed_url_of_the_document() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        document = _make_document()
        mock_repo.get_document.return_value = document
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        )
        mock_dify.run_workflow_with_retry = AsyncMock(
            return_value=_workflow_output(document_type="payslip")
        )

        await orchestrator.process_document("doc-1")

        mock_storage.create_signed_url.assert_called_once_with(
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


@pytest.mark.asyncio
async def test_low_confidence_classification_fails_before_extraction() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
        mock_dify.run_chatflow_with_retry = AsyncMock(
            return_value=_chat_output(document_type=None, confidence=0.2, needs_manual_review=True)
        )
        mock_dify.run_workflow_with_retry = AsyncMock()

        await orchestrator.process_document("doc-1")

        # Only the Intake Coordinator ran — Extraction never gets called on
        # a classification that already needs manual review.
        assert mock_dify.run_chatflow_with_retry.await_count == 1
        mock_dify.run_workflow_with_retry.assert_not_called()
        args, kwargs = mock_repo.update_status.call_args
        assert args[0] == "doc-1"
        assert args[1] == DocumentStatus.FAILED
        # document_type=None here (the mocked output had no guess at all) —
        # confirms we still pass it through rather than crashing on absence.
        assert kwargs["document_type"] is None


@pytest.mark.asyncio
async def test_escalation_resolves_on_reclassification() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
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
                _workflow_output(
                    document_type="payslip",
                    escalation={"reason": "reads like a shipping manifest"},
                ),
                # 2nd Extraction call — no escalation this time
                _workflow_output(document_type="list_of_loans"),
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


@pytest.mark.asyncio
async def test_escalation_cap_routes_to_manual_review() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.settings") as mock_settings,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_settings.MAX_RECLASSIFICATION_ATTEMPTS = 2
        mock_settings.CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.6
        mock_settings.SIGNED_URL_EXPIRY_SECONDS = 300
        mock_settings.DIFY_CLASSIFIER_API_KEY = "classifier-key"
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"

        classifying = _chat_output(document_type="payslip", confidence=0.9, needs_manual_review=False)
        escalating = _workflow_output(document_type="payslip", escalation={"reason": "still doesn't fit"})
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
        assert kwargs["document_type"] == "payslip"


@pytest.mark.asyncio
async def test_intake_coordinator_invocation_failure_marks_failed() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
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
        patch("app.orchestrator.storage_service") as mock_storage,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.create_signed_url.return_value = "https://signed.example/doc.pdf"
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
