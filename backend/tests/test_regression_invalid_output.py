"""
Regression tests (SCRUM-296) for an agent reply that fails schema
validation — "Test Cases - Sprint 4.xlsx", Multi-Agent sheet, Handle invalid
agent output (SCRUM-54): MA-024 – MA-027.

The live run forced a bad extraction reply with the FORCE_BAD_EXTRACTION
test hook. Here the same happens without it: the real LangGraph pipeline
runs end to end (queue -> ingest -> classify -> extract -> persist), and the
scripted Claude from test_regression_ai_pipeline.py answers the extraction
step with a reply that isn't the JSON object the schema describes — once
(MA-024) or on both attempts (MA-025 – MA-027). Storage, the database and
OCR are the same in-memory fakes. The frontend half (the document marked
Failed, "Extraction output could not be used: …") is in
frontend/e2e/specs/case-analysis.spec.ts.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.case_pipeline.readiness import is_ready_for_analysis
from app.langgraph import trace
from app.main import app
from app.models import DocumentStatus
from tests import test_regression_ai_pipeline as pipeline_harness
from tests.test_regression_ai_pipeline import classification, extraction, real_extraction, run, steps

# The Sprint 2 harness's fixtures: scripted Claude, the in-memory storage, database and OCR, and the cleanup of
# each run's trace and text cache.
ai = pipeline_harness.ai
store = pipeline_harness.store
_clear_pipeline_memory = pipeline_harness._clear_pipeline_memory

client = TestClient(app)

# What the live run's FORCE_BAD_EXTRACTION hook made the model answer: prose, not the JSON object.
BAD_REPLY = "Sure! Here are the fields I found on this payslip: the employee is paid monthly."


def _extraction_step(document_id: str) -> dict:
    [step] = [s for s in trace.get(document_id)["steps"] if s["step"] == "extraction"]
    return step


def _valid_payslip_reply() -> str:
    """The real payslip extraction from GLDB-02, as Claude returned it: valid against the schema."""
    fields, _ = real_extraction("Payslip")
    return extraction(fields)


def _add_document(store, ai, filename: str, text: str, real_document: str, *extraction_replies: str) -> str:
    """An uploaded document, and the classification and extraction replies its run will get, in order."""
    fields, document_type = real_extraction(real_document)
    document_id = store.add(filename, text)
    ai.reply("classify_agent", classification(document_type))
    ai.reply("extract", *(extraction_replies or (extraction(fields),)))
    return document_id


def _add_payslip(store, ai, *extraction_replies: str) -> str:
    return _add_document(store, ai, "C001-P2_Payslip.pdf", "PAYSLIP Golden Harvest Trading Pte Ltd Net Pay 5,200.00",
                         "Payslip", *extraction_replies)


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-54", "MA-024")
async def test_an_invalid_extraction_reply_is_retried_once_and_the_valid_retry_is_used(ai, store):
    document_id = _add_payslip(store, ai, BAD_REPLY, _valid_payslip_reply())

    await run(document_id)

    step = _extraction_step(document_id)
    assert step["retried"] is True
    assert step["output_error"] is None
    assert step["output_status"] == "ok"
    assert ai.steps().count("extract") == 2
    # The retry shows the model its own unusable reply and asks for the JSON object only.
    retry_messages = ai.calls[-1][1]
    assert retry_messages[1].content == BAD_REPLY
    assert "ONLY that JSON object" in retry_messages[2].content
    # Its fields are saved and the document carries on (ready for review, then approval and detection).
    assert store.fields[document_id]
    assert store.documents[document_id].status == DocumentStatus.ANALYZED
    assert store.documents[document_id].failure_reason is None


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-54", "MA-025")
async def test_a_retry_that_is_also_invalid_fails_the_document_and_nothing_runs_after_extraction(ai, store):
    document_id = _add_payslip(store, ai, BAD_REPLY, BAD_REPLY)

    await run(document_id)

    step = _extraction_step(document_id)
    assert step["retried"] is True
    assert step["output_status"] == "failed"
    assert step["output_error"].startswith("Extraction did not return valid JSON.")
    assert ai.steps().count("extract") == 2  # one retry, no more
    assert store.documents[document_id].status == DocumentStatus.FAILED
    # Nothing downstream: no fields saved, and extraction is the last step the pipeline ran (then only its
    # closing bookkeeping).
    assert document_id not in store.fields
    order = steps(document_id)
    assert order[order.index("extraction") + 1:] == ["done", "run_audit"]
    assert not any(s.startswith("detection") for s in order)


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-54", "MA-026")
async def test_the_failed_document_carries_the_reason_the_page_shows(ai, store):
    document_id = _add_payslip(store, ai, BAD_REPLY, BAD_REPLY)

    await run(document_id)

    reason = store.documents[document_id].failure_reason
    assert reason.startswith("Extraction output could not be used: Extraction did not return valid JSON.")
    # The model's own words are kept in the reason, so the officer can see what came back.
    assert BAD_REPLY in reason


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-54", "MA-027")
async def test_the_other_documents_carry_on_and_the_failed_one_can_be_retried(ai, store):
    # Added in the order they're processed, since each takes the next queued reply.
    incorp = _add_document(store, ai, "C001_CertIncorp.pdf", "CERTIFICATE CONFIRMING INCORPORATION OF COMPANY",
                           "BizFile_333996")
    payslip = _add_payslip(store, ai, BAD_REPLY, BAD_REPLY)
    noa = _add_document(store, ai, "C001_NOA.pdf", "NOTICE OF ASSESSMENT Year of Assessment 2026", "IRAS_NOA")

    for document_id in (incorp, payslip, noa):
        await run(document_id)

    statuses = {d: store.documents[d].status for d in (incorp, payslip, noa)}
    assert statuses == {incorp: DocumentStatus.ANALYZED, payslip: DocumentStatus.FAILED, noa: DocumentStatus.ANALYZED}
    assert store.fields[incorp] and store.fields[noa]
    assert store.documents[payslip].failure_reason.startswith("Extraction output could not be used:")

    # The case stays usable: once the others are approved and scanned, the analysis can run without the failed one.
    for document_id in (incorp, noa):
        store.documents[document_id].status = DocumentStatus.DETECTED
    assert is_ready_for_analysis(list(store.documents.values())) is True

    # The failed document can be retried, and goes through the pipeline again.
    with (
        patch("app.routers._dependencies.document_repository", store),
        patch("app.routers.documents.document_repository", store),
    ):
        response = await asyncio.to_thread(client.post, f"/documents/{payslip}/retry")
    assert response.status_code == 200
    assert store.documents[payslip].status == DocumentStatus.QUEUED

    _, document_type = real_extraction("Payslip")
    ai.reply("classify_agent", classification(document_type))
    ai.reply("extract", _valid_payslip_reply())
    await run(payslip)

    assert store.documents[payslip].status == DocumentStatus.ANALYZED
    assert store.documents[payslip].failure_reason is None
    assert store.fields[payslip]
