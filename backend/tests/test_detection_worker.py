from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.events import types
from app.models import DocumentStatus
from app.workers import detection as detection_worker
from app.workers import documents as doc_worker

SINCE = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("flag", [True, False])
async def test_a_readable_cancel_flag_is_the_answer_without_the_audit_trail(flag) -> None:
    with (
        patch("app.workers.detection.document_repository.detection_cancel_requested_since", return_value=flag),
        patch("app.workers.detection.audit.find") as find,
    ):
        assert await detection_worker._cancel_requested_since("doc-1", SINCE) is flag

    find.assert_not_called()


@pytest.mark.parametrize("found, expected", [([object()], True), ([], False)])
async def test_an_unreadable_cancel_flag_falls_back_to_the_audit_trail(found, expected) -> None:
    with (
        patch("app.workers.detection.document_repository.detection_cancel_requested_since", return_value=None),
        patch("app.workers.detection.audit.find", return_value=found) as find,
    ):
        assert await detection_worker._cancel_requested_since("doc-1", SINCE) is expected

    find.assert_called_once_with(types.DETECTION_CANCEL_REQUESTED, document_id="doc-1", since=SINCE)


@pytest.mark.parametrize("status, expected", [
    (DocumentStatus.CANCELLED, True), (DocumentStatus.PROCESSING, False), (None, False),
])
async def test_the_doc_worker_reads_only_the_status_to_see_a_cancel(status, expected) -> None:
    with patch("app.workers.documents.document_repository") as repo:
        repo.get_status.return_value = status
        assert await doc_worker._cancelled_elsewhere("doc-1") is expected

    repo.get_status.assert_called_once_with("doc-1")
    repo.get_document.assert_not_called()
    repo.detection_cancel_requested_since.assert_not_called()
