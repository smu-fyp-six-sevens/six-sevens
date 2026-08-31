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


def _output(**outputs) -> dict:
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
        mock_dify.run_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_no_escalation_marks_analyzed() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_dify.run_workflow = AsyncMock(
            side_effect=[
                _output(document_type="payslip", confidence=0.9, needs_manual_review=False),
                _output(document_type="payslip"),  # no "escalation" key
            ]
        )

        await orchestrator.process_document("doc-1")

        assert mock_dify.run_workflow.await_count == 2
        mock_repo.update_status.assert_called_once_with(
            "doc-1", DocumentStatus.ANALYZED, document_type="payslip"
        )


@pytest.mark.asyncio
async def test_low_confidence_classification_fails_before_extraction() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_dify.run_workflow = AsyncMock(
            return_value=_output(document_type=None, confidence=0.2, needs_manual_review=True)
        )

        await orchestrator.process_document("doc-1")

        # Only the Intake Coordinator ran — Extraction never gets called on
        # a classification that already needs manual review.
        assert mock_dify.run_workflow.await_count == 1
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
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_dify.run_workflow = AsyncMock(
            side_effect=[
                # 1st Intake Coordinator call
                _output(document_type="payslip", confidence=0.9, needs_manual_review=False),
                # 1st Extraction call — escalates
                _output(
                    document_type="payslip",
                    escalation={"reason": "reads like a shipping manifest"},
                ),
                # Re-run Intake Coordinator with escalation context
                _output(
                    document_type="list_of_loans", confidence=0.85, needs_manual_review=False
                ),
                # 2nd Extraction call — no escalation this time
                _output(document_type="list_of_loans"),
            ]
        )

        await orchestrator.process_document("doc-1")

        assert mock_dify.run_workflow.await_count == 4
        # document_type reflects the FINAL (post-escalation) classification,
        # not the original "payslip" guess that got escalated away.
        mock_repo.update_status.assert_called_once_with(
            "doc-1", DocumentStatus.ANALYZED, document_type="list_of_loans"
        )


@pytest.mark.asyncio
async def test_escalation_cap_routes_to_manual_review() -> None:
    with (
        patch("app.orchestrator.document_repository") as mock_repo,
        patch("app.orchestrator.settings") as mock_settings,
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_settings.MAX_RECLASSIFICATION_ATTEMPTS = 2
        mock_settings.CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.6
        mock_repo.get_document.return_value = _make_document()

        escalating = _output(
            document_type="payslip", escalation={"reason": "still doesn't fit"}
        )
        classifying = _output(
            document_type="payslip", confidence=0.9, needs_manual_review=False
        )
        # Intake -> Extraction(escalate) -> Intake -> Extraction(escalate)
        # -> Intake -> Extraction(escalate) : cap of 2 attempts exhausted
        mock_dify.run_workflow = AsyncMock(
            side_effect=[classifying, escalating, classifying, escalating, classifying, escalating]
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
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_dify.run_workflow = AsyncMock(side_effect=DifyInvocationError("network blip"))

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
        patch("app.orchestrator.dify_client") as mock_dify,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_dify.run_workflow = AsyncMock(
            side_effect=[
                _output(document_type="payslip", confidence=0.9, needs_manual_review=False),
                DifyInvocationError("network blip"),
            ]
        )

        await orchestrator.process_document("doc-1")

        args, kwargs = mock_repo.update_status.call_args
        assert args[0] == "doc-1"
        assert args[1] == DocumentStatus.FAILED
        assert "Extraction invocation failed" in kwargs["failure_reason"]
        assert kwargs["document_type"] == "payslip"
