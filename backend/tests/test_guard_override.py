"""An officer's "process anyway" on a document the content guard stopped (document_service.process_anyway)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import document_service
from app.events import types
from app.guardrails import content_guard
from app.langgraph import nodes
from app.main import app
from app.models import DocumentRecord, DocumentStatus

client = TestClient(app)
BLOCKED = (
    f'{content_guard.BLOCKED_PREFIX}: hidden text on page 1 is worded as an instruction to the AI ("ignore all '
    'previous instructions"). No AI model read this document.'
)
SPAN = {"caught_by": "dictionary", "where": "hidden", "page": 1, "quote": "ignore all previous instructions", "score": 3}


def _document(**overrides) -> DocumentRecord:
    values = dict(
        id="doc-1", case_id="case-1", filename="payslip.pdf", status=DocumentStatus.FAILED, failure_reason=BLOCKED,
        uploaded_at=datetime.now(timezone.utc),
    )
    values.update(overrides)
    return DocumentRecord(**values)


def _guard(**extra) -> dict:
    return {"risk": "high", "checks_run": ["hidden_text"], "spans": [SPAN], "spans_dropped": 0, **extra}


# --- the guard honours the override ---------------------------------------------------------------------------


def test_a_blocked_result_with_an_officers_override_is_let_through() -> None:
    assert content_guard.blocking_reason(_guard()) is not None
    assert content_guard.blocking_reason(_guard(override={"by": "officer", "at": "2026-11-13T09:00:00+00:00"})) is None


async def test_a_new_scan_carries_the_override_over_from_the_saved_result() -> None:
    saved = {"override": {"by": "officer", "at": "2026-11-13T09:00:00+00:00"}}
    with (
        patch.object(nodes, "run_cpu", AsyncMock(return_value=(_guard(), []))),
        patch.object(nodes.document_repository, "get_guard_result", return_value=saved),
        patch.object(nodes.document_repository, "update_ingest_results") as write,
        patch.object(nodes, "trace"),
    ):
        _, result = await nodes._guard_document("doc-1", b"%PDF", "application/pdf", "text", None)

    assert result["override"] == saved["override"]
    assert nodes.guard_block_reason(result) is None
    assert write.call_args.kwargs["guard_result"]["override"] == saved["override"]  # and it is saved again


async def test_a_scan_with_nothing_blocking_does_not_look_the_override_up() -> None:
    clean = {"risk": None, "checks_run": ["hidden_text"], "spans": [], "spans_dropped": 0}
    with (
        patch.object(nodes, "run_cpu", AsyncMock(return_value=(clean, []))),
        patch.object(nodes.document_repository, "get_guard_result") as read,
        patch.object(nodes.document_repository, "update_ingest_results"),
        patch.object(nodes, "trace"),
    ):
        await nodes._guard_document("doc-1", b"%PDF", "application/pdf", "text", None)

    read.assert_not_called()


# --- the action -----------------------------------------------------------------------------------------------


async def test_processing_anyway_saves_the_override_logs_it_and_retries() -> None:
    document = _document()
    retried = _document(status=DocumentStatus.QUEUED, failure_reason=None)
    with (
        patch.object(document_service.document_repository, "get_guard_result", return_value=_guard()),
        patch.object(document_service.document_repository, "update_ingest_results") as write,
        patch.object(document_service, "current_actor", return_value="officer"),
        patch.object(document_service.events, "record", new_callable=AsyncMock) as record,
        patch.object(document_service, "retry", AsyncMock(return_value=retried)) as retry,
    ):
        result = await document_service.process_anyway(document)

    assert result is retried
    saved = write.call_args.kwargs["guard_result"]
    assert saved["override"]["by"] == "officer" and saved["spans"] == [SPAN]  # what the guard found is kept
    [event] = [call.args[0] for call in record.await_args_list]
    assert (event.type, event.document_id) == (types.DOCUMENT_GUARD_OVERRIDDEN, "doc-1")
    assert event.detail["reason"] == BLOCKED
    retry.assert_awaited_once_with(document)


@pytest.mark.parametrize("document", [
    _document(status=DocumentStatus.ANALYZED, failure_reason=None),
    _document(failure_reason="Extraction produced no usable text"),
])
async def test_only_a_document_the_guard_stopped_can_be_processed_anyway(document) -> None:
    with (
        patch.object(document_service.document_repository, "update_ingest_results") as write,
        patch.object(document_service, "retry", AsyncMock()) as retry,
        pytest.raises(document_service.Refused),
    ):
        await document_service.process_anyway(document)

    write.assert_not_called()
    retry.assert_not_called()


def test_the_route_hands_the_document_to_the_action() -> None:
    document = _document()
    with (
        patch("app.routers._dependencies.document_repository") as lookup,
        patch("app.routers.documents.document_service.process_anyway", AsyncMock(return_value=_document(status=DocumentStatus.QUEUED))) as action,
    ):
        lookup.get_document.return_value = document
        response = client.post("/documents/doc-1/process-anyway")

    assert response.status_code == 200 and response.json()["status"] == "queued"
    action.assert_awaited_once()
    assert action.await_args is not None and action.await_args.args[0].id == "doc-1"


def test_the_route_refusal_is_a_conflict() -> None:
    document = _document(status=DocumentStatus.ANALYZED, failure_reason=None)
    with patch("app.routers._dependencies.document_repository") as lookup:
        lookup.get_document.return_value = document
        response = client.post("/documents/doc-1/process-anyway")

    assert response.status_code == 409
