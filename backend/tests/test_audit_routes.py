"""
The audit trail's routes (routers/audit.py): the trail itself and a case's Claude cost, each read through the audit
module, and a 502 that says what failed when the trail can't be read.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import audit
from app.events import Event, types
from app.main import app
from app.models import DocumentRecord, DocumentStatus

client = TestClient(app)
UNREADABLE = "Failed to read the audit trail. Check Supabase table access."
COST = audit.CaseCost(case_id="case-1", total_usd=0.42, documents_usd={"doc-1": 0.42}, case_analysis_usd=0.0,
                      runs=[], timings=[])


def test_the_trail_is_read_for_the_case_or_document_asked_for() -> None:
    event = Event(type=types.DOCUMENT_APPROVED, case_id="case-1", document_id="doc-1", actor_type="user", actor="demo")
    with patch("app.routers.audit.audit.list_events", return_value=[event]) as list_events:
        response = client.get("/audit/events", params={"case_id": "case-1", "document_id": "doc-1", "limit": 50})

    assert response.status_code == 200
    assert [(e["type"], e["actor"]) for e in response.json()] == [(types.DOCUMENT_APPROVED, "demo")]
    list_events.assert_called_once_with(case_id="case-1", document_id="doc-1", limit=50)


def test_the_trail_limit_is_bounded() -> None:
    assert client.get("/audit/events", params={"limit": 0}).status_code == 422
    assert client.get("/audit/events", params={"limit": 1001}).status_code == 422


def test_a_case_cost_counts_the_documents_the_case_has_now() -> None:
    documents = [
        DocumentRecord(id=document_id, case_id="case-1", filename=f"{document_id}.pdf", status=DocumentStatus.DETECTED,
                       uploaded_at=datetime(2026, 11, 1, tzinfo=timezone.utc))
        for document_id in ("doc-1", "doc-2")
    ]
    with (
        patch("app.routers.audit.document_repository.list_documents", return_value=documents),
        patch("app.routers.audit.audit.case_cost", return_value=COST) as case_cost,
    ):
        response = client.get("/audit/cases/case-1/cost")

    assert (response.status_code, response.json()["total_usd"]) == (200, 0.42)
    case_cost.assert_called_once_with("case-1", ["doc-1", "doc-2"])


def test_the_dashboards_costs_are_read_for_every_case_asked_for() -> None:
    with patch("app.routers.audit.audit.cost_usd_many", return_value={"case-1": 0.42}) as cost_usd_many:
        response = client.get("/audit/costs", params=[("case_id", "case-1"), ("case_id", "case-2")])

    assert response.json() == {"case-1": 0.42}
    cost_usd_many.assert_called_once_with(["case-1", "case-2"])


def test_a_trail_that_cannot_be_read_is_a_502_that_says_so() -> None:
    failing = RuntimeError("connection refused")
    with (
        patch("app.routers.audit.audit.list_events", side_effect=failing),
        patch("app.routers.audit.audit.cost_usd_many", side_effect=failing),
        patch("app.routers.audit.document_repository.list_documents", side_effect=failing),
    ):
        responses = [client.get("/audit/events"), client.get("/audit/costs"), client.get("/audit/cases/case-1/cost")]

    assert [(r.status_code, r.json()["detail"]) for r in responses] == [(502, UNREADABLE)] * 3
