"""
Regression tests (SCRUM-206) for the AI pipeline test cases, deferred until
the LangGraph migration landed — "Test Cases - Sprint 1.xlsx" / "Sprint 2.xlsx":

  SCRUM-8   Classify documents                    DI-017
  SCRUM-17  Auto-trigger classification           MA-002
  SCRUM-9   Extract text from documents           DI-018, DI-019, DI-022, DI-027 – DI-029
  SCRUM-19  Auto-trigger extraction               MA-003, MA-004
  SCRUM-115 Generate additional file formats      DT-030

The real LangGraph pipeline runs end to end (queue -> ingest -> rule
classifier -> classify agent -> validate/repair -> record classification ->
extract -> persist -> analyzed). Only Claude's replies are scripted, by
FakeClaude standing in for app.langgraph._anthropic.build_chat_anthropic —
no request ever leaves the test (conftest.py also blocks real Claude calls).
Storage, the database and OCR are in-memory fakes that record what the
pipeline wrote.

What this proves: given what the AI answers, the pipeline records the right
type, confidence, routing, statuses and fields. Whether Claude itself still
gives the right answer for the real documents is checked by the manual live
run at the end of each sprint.

Extraction replies reuse real extraction results captured from Supabase
(tests/fixtures/regression/anchorpoint.json), so the pipeline is exercised
with real-shaped AI output.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from app.config import settings
from app.langgraph import nodes, pipeline, text_cache, trace
from app.langgraph.extract import _EXTRACTION_MAX_TOKENS
from app.langgraph.extraction_prompts import EXTRACTION_SCHEMAS
from app.models import DocumentRecord, DocumentStatus, RoutingStatus
from app.events import Event, types
from app.workers import documents as doc_worker

FIXTURES = Path(__file__).parent / "fixtures" / "regression"
ANCHORPOINT = json.loads((FIXTURES / "anchorpoint.json").read_text())


# --- scripted Claude -----------------------------------------------------------


class Script:
    """Queued replies per AI step, and a record of every call made."""

    def __init__(self) -> None:
        self.replies: dict[str, list] = defaultdict(list)
        self.calls: list[tuple[str, list]] = []

    def reply(self, step: str, *replies) -> None:
        self.replies[step].extend(replies)

    def next(self, step: str, messages: list) -> AIMessage:
        self.calls.append((step, messages))
        if not self.replies[step]:
            raise AssertionError(f"The pipeline asked Claude for an unscripted '{step}' reply")
        reply = self.replies[step].pop(0)
        return reply if isinstance(reply, AIMessage) else AIMessage(content=reply)

    def steps(self) -> list[str]:
        return [step for step, _ in self.calls]


class FakeClaude:
    """Duck-types the parts of ChatAnthropic the pipeline uses."""

    def __init__(self, script: Script, step: str) -> None:
        self.script, self.step = script, step

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        return self.script.next(self.step, list(messages))


def _step_for(model: str, max_tokens: int) -> str:
    # Each AI step builds its model with a distinct (model, max_tokens) pair
    # — see classify_graph.agent_node/repair_node, llm_classify.classify_by_llm
    # and extract.run_extraction.
    return {
        (settings.LANGGRAPH_SONNET_MODEL, 2048): "classify_agent",
        (settings.LANGGRAPH_HAIKU_MODEL, 2048): "llm_classify",
        (settings.LANGGRAPH_HAIKU_MODEL, 1024): "repair",
        (settings.LANGGRAPH_SONNET_MODEL, _EXTRACTION_MAX_TOKENS): "extract",
    }[(model, max_tokens)]


def classification(document_type: str | None, confidence: float = 0.96, *, manual_review: bool = False) -> str:
    return json.dumps({
        "document_type": document_type,
        "confidence": confidence,
        "evidence": [f"Layout and headings match a {document_type}"],
        "reasoning": f"Reads as a {document_type}.",
        "needs_manual_review": manual_review,
    })


def extraction(fields: dict[str, tuple], escalation: dict | None = None) -> str:
    """fields: name -> (value, confidence)."""
    body = {"extracted_fields": {name: {"value": v, "confidence": c} for name, (v, c) in fields.items()}}
    if escalation:
        body["escalation"] = escalation
    return json.dumps(body)


def real_extraction(filename_part: str) -> tuple[dict[str, tuple], str]:
    """A real Anchorpoint document's captured extraction, as Claude would have returned it."""
    document = next(d for d in ANCHORPOINT["documents"].values() if filename_part in d["filename"])
    fields = {name: (f["value"], f["confidence"]) for name, f in document["extracted_fields"].items()}
    return fields, document["document_type"]


# --- in-memory storage, database and OCR ----------------------------------------


class FakeStore:
    """Stands in for document_repository, extracted_fields_repository, storage and OCR."""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentRecord] = {}
        self.text: dict[str, str] = {}
        self.statuses: dict[str, list[str]] = defaultdict(list)
        self.classifications: dict[str, dict] = {}
        self.fields: dict[str, dict] = {}
        self.field_confidences: dict[str, dict] = {}
        self.extraction_confidence: dict[str, float] = {}
        self.fulfilled: dict[str, list[str]] = {}
        self.escalations: dict[str, list[str]] = defaultdict(list)
        self.ingested: list[tuple[str, str]] = []

    def add(self, filename: str, text: str) -> str:
        document_id = f"doc-{len(self.documents) + 1}"
        self.documents[document_id] = DocumentRecord(
            id=document_id,
            case_id="case-1",
            filename=filename,
            storage_path=f"case-1/{document_id}_{filename}",
            status=DocumentStatus.QUEUED,
            uploaded_at=datetime.now(timezone.utc),
        )
        self.text[filename] = text
        return document_id

    # document_repository
    def get_document(self, document_id, include_fields=True, include_metadata=False):
        return self.documents.get(document_id)

    def update_status(self, document_id, status, **changes):
        document = self.documents[document_id]
        expected = changes.get("expected_current_statuses")
        if expected is not None and document.status not in expected:
            return False
        document.status = DocumentStatus(status)
        self.statuses[document_id].append(document.status.value)
        if changes.get("document_type"):
            document.document_type = changes["document_type"]
        # The real repository's rule: a reason belongs to a failed run.
        if document.status != DocumentStatus.FAILED:
            document.failure_reason = None
        elif changes.get("failure_reason") is not None:
            document.failure_reason = changes["failure_reason"]
        return True

    def record_classification(self, document_id, **recorded):
        self.classifications[document_id] = recorded
        document = self.documents[document_id]
        document.document_type = recorded["document_type"]
        document.classification_confidence = recorded["classification_confidence"]
        document.routing_status = RoutingStatus(recorded["routing_status"])

    def update_extracted_fields(self, document_id, extraction_confidence, fulfilled_requirements=None):
        self.extraction_confidence[document_id] = extraction_confidence
        self.fulfilled[document_id] = fulfilled_requirements

    def record_escalation(self, document_id, reason):
        self.escalations[document_id].append(reason)

    def update_ingest_results(self, document_id, *, guard_result, metadata):
        pass

    # extracted_fields_repository
    def replace_extracted_fields(self, document_id, fields, *, field_confidences=None, default_confidence=None,
                                 required_field_names=None):
        self.fields[document_id] = dict(fields)
        self.field_confidences[document_id] = dict(field_confidences or {})

    # storage_service
    def download_file(self, storage_path):
        return b"%PDF-1.4 stored file"

    # ingestion_service.ingest_document
    def ingest(self, file_bytes, filename, content_type, **_):
        self.ingested.append((filename, content_type))
        is_image = content_type.startswith("image/") or "scanned" in filename
        return {
            "raw_text": self.text[filename],
            "metadata": {},
            "needs_review": False,
            "extraction_method": "ocr" if is_image else "native",
        }


@pytest.fixture
def ai(monkeypatch) -> Script:
    script = Script()
    monkeypatch.setattr(
        "app.langgraph._anthropic.build_chat_anthropic",
        lambda model, max_tokens, **kwargs: FakeClaude(script, _step_for(model, max_tokens)),
    )
    return script


@pytest.fixture
def store(monkeypatch) -> FakeStore:
    fake = FakeStore()
    for target in ("app.langgraph.nodes.document_repository", "app.workers.documents.document_repository"):
        monkeypatch.setattr(target, fake)
    monkeypatch.setattr("app.langgraph.nodes.extracted_fields_repository", fake)
    monkeypatch.setattr("app.langgraph.nodes.storage_service", fake)
    monkeypatch.setattr("app.langgraph.nodes.ingest_document", fake.ingest)
    monkeypatch.setattr("app.langgraph.nodes.extract_metadata", lambda *_: {})
    monkeypatch.setattr(trace, "_persist", lambda document_id: None)  # keep the trace in memory only
    return fake


async def run(document_id: str) -> None:
    """Process one uploaded document, exactly as the doc-worker does on its event."""
    await doc_worker.on_queued(Event(type=types.DOCUMENT_UPLOADED, document_id=document_id))


def steps(document_id: str) -> list[str]:
    return [
        s["step"]
        for s in (trace.get(document_id) or {}).get("steps", [])
        if s["step"] != "span"
    ]


# --- SCRUM-17 Auto-trigger classification ---------------------------------------


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-17", "MA-002")
async def test_a_queued_document_is_handed_to_the_classifier_within_one_second(ai, store, monkeypatch):
    document_id = store.add("C001-P2_Payslip.pdf", "PAYSLIP Employee Net Pay 5,200.00")
    handed_off: list[tuple[str, float]] = []
    dequeued_at = time.monotonic()

    async def classifier(doc_id):
        handed_off.append((doc_id, time.monotonic() - dequeued_at))

    monkeypatch.setattr("app.workers.documents.langgraph_pipeline.process_document", classifier)
    await doc_worker.on_queued(Event(type=types.DOCUMENT_UPLOADED, document_id=document_id))

    assert store.statuses[document_id] == ["processing"]
    assert store.documents[document_id].status == DocumentStatus.PROCESSING
    [(doc_id, seconds)] = handed_off
    assert doc_id == document_id
    assert seconds < 1.0


# --- SCRUM-8 Classify documents ------------------------------------------------

C001_SET = [
    ("C001_CertIncorp.pdf", "certificate_of_incorporation"),
    ("C001_CertIncumbency.pdf", "certificate_of_incumbency"),
    ("C001_Constitution.pdf", "company_constitution"),
    ("C001_CreditReport.pdf", "credit_report"),
    ("C001_ListLoans.pdf", "list_of_loans"),
    ("C001_selfcert.pdf", "fatca_crs_declaration"),
    ("C001_BankStatement_2026-06_to_2026-08.pdf", "bank_statement"),
    ("C001-P1_NRICPassport.pdf", "nric"),
    ("C001-P2_Payslip.pdf", "payslip"),
]


# Confidence is computed from the rule classifier alone here (the agent
# calls no tools), not the 0.96 the agent reports — see
# classification_confidence.py. A "certain" text pattern scores 0.97; a
# strong rule match with nothing to corroborate it is capped at 0.85. The
# NRIC's stand-in text carries no NRIC wording, leaving only a weak
# filename hit, so it goes to manual review.
C001_EXPECTED_CONFIDENCE = {
    "certificate_of_incorporation": (0.97, "accepted"),
    "certificate_of_incumbency": (0.85, "accepted"),
    "company_constitution": (0.97, "accepted"),
    "credit_report": (0.85, "accepted"),
    "list_of_loans": (0.85, "accepted"),
    "fatca_crs_declaration": (0.97, "accepted"),
    "bank_statement": (0.85, "accepted"),
    "nric": (0.4, "manual_review"),
    "payslip": (0.85, "accepted"),
}


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-8", "DI-017")
@pytest.mark.parametrize("filename, document_type", C001_SET)
async def test_each_clean_file_is_recorded_with_its_type_confidence_and_routing(ai, store, filename, document_type):
    document_id = store.add(filename, f"{document_type.replace('_', ' ').title()} for Golden Harvest Trading Pte Ltd")
    ai.reply("classify_agent", classification(document_type, 0.96))
    ai.reply("extract", extraction({"company_name": ("Golden Harvest Trading Pte Ltd", 0.98)}))

    await run(document_id)

    expected_confidence, expected_routing = C001_EXPECTED_CONFIDENCE[document_type]
    recorded = store.classifications[document_id]
    assert recorded["document_type"] == document_type
    assert recorded["classification_confidence"] == expected_confidence
    assert recorded["routing_status"] == expected_routing
    assert store.documents[document_id].status == DocumentStatus.ANALYZED


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-8", "DI-017")
async def test_an_unrecognised_file_is_routed_to_manual_review(ai, store):
    document_id = store.add("Fashion-Purchase-Order-Template-TemplateLab.com_.pdf", "PURCHASE ORDER Item Qty Unit Price")
    ai.reply("classify_agent", classification("unrecognised", 0.2, manual_review=True))
    ai.reply("extract", extraction({"document_title": ("Purchase Order", 0.9)}))

    await run(document_id)

    recorded = store.classifications[document_id]
    assert recorded["document_type"] == "unrecognised"
    assert recorded["routing_status"] == "manual_review"


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-8", "DI-017")
async def test_a_confident_type_below_the_threshold_is_kept_but_sent_to_manual_review(ai, store):
    document_id = store.add("scan_0042.pdf", "Net Pay ... Employer")
    below = settings.CLASSIFICATION_CONFIDENCE_THRESHOLD - 0.1
    ai.reply("classify_agent", classification("payslip", below))
    ai.reply("extract", extraction({"net_pay": (5200, 0.9)}))

    await run(document_id)

    recorded = store.classifications[document_id]
    assert recorded["document_type"] == "payslip"
    assert recorded["routing_status"] == "manual_review"


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-8", "DI-017")
async def test_the_agent_can_consult_the_llm_classifier_tool_before_answering(ai, store):
    document_id = store.add("C001-P2_Payslip.pdf", "PAYSLIP Employee Net Pay 5,200.00")
    ai.reply("classify_agent", AIMessage(content="", tool_calls=[{"name": "llm_classify_tool", "args": {}, "id": "call-1"}]))
    ai.reply("llm_classify", json.dumps({"top_candidates": [
        {"document_type": "payslip", "match_strength": 0.95, "evidence": ["Net Pay"], "reasoning": "payslip layout"},
    ]}))
    ai.reply("classify_agent", classification("payslip", 0.97))
    ai.reply("extract", extraction({"net_pay": (5200, 0.95)}))

    await run(document_id)

    assert ai.steps() == ["classify_agent", "llm_classify", "classify_agent", "extract"]
    assert store.classifications[document_id]["document_type"] == "payslip"
    assert store.classifications[document_id]["routing_status"] == "accepted"


# --- SCRUM-19 Auto-trigger extraction -------------------------------------------


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-19", "MA-003")
async def test_extraction_starts_automatically_once_classification_is_recorded(ai, store):
    document_id = store.add("CreditReport_Experian_20250924.pdf", "CREDIT BUREAU REPORT Enquiry")
    fields, document_type = real_extraction("Credit_Bureau_Report")
    ai.reply("classify_agent", classification(document_type))
    ai.reply("extract", extraction(fields))

    await run(document_id)

    assert store.statuses[document_id] == ["processing", "extracting", "analyzed"]
    order = steps(document_id)
    assert order.index("classification_finalised") < order.index("extraction") < order.index("done")
    assert ai.steps() == ["classify_agent", "extract"]


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-19", "MA-004")
async def test_an_unrecognised_document_still_proceeds_to_extraction(ai, store):
    document_id = store.add("C007_BudgetProposal.pdf", "BUDGET PROPOSAL Working capital Equipment")
    ai.reply("classify_agent", classification(None, 0.1, manual_review=True))
    # No type in the agent's answer triggers one repair re-ask, which also
    # finds none — so the document is normalised to "unrecognised".
    ai.reply("repair", json.dumps({"document_type": None}))
    ai.reply("extract", extraction({"document_title": ("Budget Proposal", 0.92), "amount": ("250,000", 0.8)}))

    await run(document_id)

    assert store.classifications[document_id]["document_type"] == "unrecognised"
    assert store.statuses[document_id] == ["processing", "extracting", "analyzed"]
    extract_prompt = str(ai.calls[-1][1])
    for field in EXTRACTION_SCHEMAS["unrecognised"]["additional"]:
        assert field in extract_prompt


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-19", "MA-003")
async def test_extraction_disagreeing_with_the_type_sends_it_back_for_reclassification(ai, store):
    document_id = store.add("statement_scan.pdf", "OCBC Statement of Account Opening Balance")
    ai.reply("classify_agent", classification("payslip", 0.96))
    ai.reply("extract", extraction({}, escalation={"reason": "This is a bank statement, not a payslip",
                                                   "suggested_document_type": "bank_statement"}))
    ai.reply("classify_agent", classification("bank_statement", 0.95))
    ai.reply("extract", extraction({"bank_name": ("OCBC", 0.97), "account_number": ("601-234567-001", 0.95)}))

    await run(document_id)

    assert store.escalations[document_id] == ["This is a bank statement, not a payslip"]
    assert store.classifications[document_id]["document_type"] == "bank_statement"
    assert store.documents[document_id].status == DocumentStatus.ANALYZED
    assert store.fields[document_id]["bank_name"] == "OCBC"


# --- SCRUM-9 Extract text from documents -----------------------------------------


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-9", "DI-018")
@pytest.mark.parametrize("filename, real_document, text", [
    ("C007-P2_Payslip.pdf", "Payslip", "payslip text"),
    ("C007_selfcert.pdf", "FATCA_Form", "FATCA / CRS Self-Certification"),
    ("C007_CreditReport.pdf", "Credit_Bureau_Report", "credit report text"),
])
async def test_each_document_type_is_extracted_against_its_own_fields(ai, store, filename, real_document, text):
    # Confident classifications only: a low-confidence type is extracted generically.
    fields, document_type = real_extraction(real_document)
    document_id = store.add(filename, text)
    ai.reply("classify_agent", classification(document_type))
    ai.reply("extract", extraction(fields))

    await run(document_id)

    extract_prompt = str(ai.calls[-1][1])
    for field in EXTRACTION_SCHEMAS[document_type]["required"]:
        assert field in extract_prompt, f"{document_type} prompt doesn't ask for {field}"
    assert store.fields[document_id] == {name: value for name, (value, _) in fields.items()}
    assert set(store.field_confidences[document_id]) == set(fields)


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-9", "DI-019")
@pytest.mark.parametrize("filename", ["C007_BudgetProposal.pdf", "Fashion-Purchase-Order-Template-TemplateLab.com_.pdf"])
async def test_an_unrecognised_document_is_still_extracted_without_being_blocked(ai, store, filename):
    document_id = store.add(filename, "Proposal / order text")
    ai.reply("classify_agent", classification("unrecognised", 0.15, manual_review=True))
    ai.reply("extract", extraction({"document_title": ("Budget Proposal", 0.9), "company_name": ("Golden Harvest", 0.85)}))

    await run(document_id)

    assert store.documents[document_id].status == DocumentStatus.ANALYZED
    assert store.documents[document_id].failure_reason is None
    assert store.fields[document_id]["document_title"] == "Budget Proposal"


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-9", "DI-022")
@pytest.mark.parametrize("filename, content_type", [
    ("clear_front.JPG", "image/jpeg"),
    ("NRIC_6554_back.png", "image/png"),
    ("scanned.pdf", "application/pdf"),
])
async def test_images_and_scanned_pdfs_are_read_and_extracted(ai, store, filename, content_type):
    fields, document_type = real_extraction("MuhammadD_NRIC_back")
    document_id = store.add(filename, "REPUBLIC OF SINGAPORE ... BLK 123 ...")
    ai.reply("classify_agent", classification(document_type))
    ai.reply("extract", extraction(fields))

    await run(document_id)

    assert store.ingested == [(filename, content_type)]
    assert store.documents[document_id].status == DocumentStatus.ANALYZED
    assert store.field_confidences[document_id]


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-9", "DI-027")
async def test_a_certificate_of_incumbency_is_classified_as_such_and_its_officers_saved(ai, store):
    fields, document_type = real_extraction("BizFile_Office")
    document_id = store.add("C032_CertIncorp.pdf", "ACRA Business Profile (Company) Officers Shareholders")
    ai.reply("classify_agent", classification("certificate_of_incumbency", 0.97))
    ai.reply("extract", extraction(fields))

    await run(document_id)

    assert document_type == "certificate_of_incumbency"
    assert store.classifications[document_id]["document_type"] == "certificate_of_incumbency"
    saved = store.fields[document_id]
    for field in ("company_name", "uen", "officers", "shareholders"):
        assert saved.get(field), f"{field} not saved"
    assert "certificate_of_incumbency" in store.fulfilled[document_id]


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-9", "DI-028")
async def test_both_sides_of_an_nric_are_extracted(ai, store):
    front_fields, _ = real_extraction("MuhammadD_NRIC_front")
    back_fields, _ = real_extraction("MuhammadD_NRIC_back")
    front = store.add("IMG_5099_front.jpg", "IDENTITY CARD NO. Name Race Date of birth")
    back = store.add("IMG_5099_back.jpg", "Address Date of issue")
    ai.reply("classify_agent", classification("nric"), classification("nric"))
    ai.reply("extract", extraction(front_fields), extraction(back_fields))

    await run(front)
    await run(back)

    assert store.fields[front]["individual_name"]
    assert store.fields[back]["individual_address"]
    assert store.fields[front]["nric_number"] == store.fields[back]["nric_number"]
    assert store.fulfilled[front] == ["id_document"]
    assert store.fulfilled[back] == ["id_document", "proof_of_address"]


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-9", "DI-029")
async def test_document_confidence_is_the_average_of_its_field_confidences(ai, store):
    # The real FATCA form's fields range from 0.85 to 1.0, so its average
    # (0.935) and its lowest field (0.85) are different numbers.
    fields, document_type = real_extraction("FATCA_Form")
    document_id = store.add("FATCA_Form_20250522.pdf", "FATCA / CRS Self-Certification")
    ai.reply("classify_agent", classification(document_type))
    ai.reply("extract", extraction(fields))

    await run(document_id)

    confidences = [c for _, c in fields.values()]
    average = sum(confidences) / len(confidences)
    assert average != pytest.approx(min(confidences))
    assert store.extraction_confidence[document_id] == pytest.approx(average)


# --- SCRUM-115 Generate additional file formats -----------------------------------


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-115", "DT-030")
@pytest.mark.parametrize("filename", ["NRIC_6200.jpeg", "IMG_6156.jpg", "Passport_6871.png", "C014_CertIncorp.pdf"])
async def test_document_type_and_file_details_are_kept_after_ingestion(ai, store, filename):
    document_id = store.add(filename, "document text")
    before = store.documents[document_id].model_copy()
    ai.reply("classify_agent", classification("certificate_of_incorporation"))
    ai.reply("extract", extraction({"company_name": ("Harbor Light Shipping Pte Ltd", 0.97)}))

    await run(document_id)

    after = store.documents[document_id]
    assert (after.filename, after.storage_path, after.case_id) == (before.filename, before.storage_path, before.case_id)
    assert after.document_type == "certificate_of_incorporation"
    assert after.status == DocumentStatus.ANALYZED


@pytest.fixture(autouse=True)
def _clear_pipeline_memory():
    yield
    for document_id in [f"doc-{n}" for n in range(1, 10)]:
        text_cache.discard(document_id)
        trace._traces.pop(document_id, None)


# --- Officer type correction: extraction rerun ---------------------------------


BUDGET_PROPOSAL_TEXT = (
    "Proposal Budget - Combined funding sources\n"
    "Meridian Warehousing Solutions Pte Ltd\n"
    "Term loan - DBS Bank $680,000\nShareholder loan $70,000\n"
    "PLEASE NOTE: If during the facility period, any actual expense differs from the budgeted expense"
)


def _officer_set_type(store: FakeStore, monkeypatch, filename: str, text: str, document_type: str) -> str:
    """A document the officer has just re-typed via PATCH .../document-type."""
    monkeypatch.setattr("app.langgraph.pipeline.document_repository", store)
    document_id = store.add(filename, text)
    document = store.documents[document_id]
    document.document_type = document_type
    document.classification_confidence = 1.0
    document.routing_status = "accepted"
    document.status = DocumentStatus.EXTRACTING
    return document_id


async def test_extraction_cannot_overrule_an_officer_confirmed_other(ai, store, monkeypatch):
    document_id = _officer_set_type(store, monkeypatch, "C067_BudgetProposal.pdf", BUDGET_PROPOSAL_TEXT, "unrecognised")
    ai.reply("extract", extraction(
        {"document_title": ("Proposal Budget", 0.95), "company_name": ("Meridian Warehousing Solutions Pte Ltd", 0.97)},
        escalation={"suggested_document_type": "list_of_loans", "reason": "Mentions a DBS term loan facility"},
    ))

    await pipeline.rerun_extraction(document_id)

    document = store.documents[document_id]
    assert document.status == DocumentStatus.ANALYZED
    assert document.document_type == "unrecognised"
    assert store.fields[document_id]["company_name"] == "Meridian Warehousing Solutions Pte Ltd"


async def test_extraction_rejecting_an_officer_chosen_type_still_fails_it(ai, store, monkeypatch):
    document_id = _officer_set_type(store, monkeypatch, "C067_BudgetProposal.pdf", BUDGET_PROPOSAL_TEXT, "payslip")
    ai.reply("extract", extraction({}, escalation={"reason": "This is a budget proposal, not a payslip"}))

    await pipeline.rerun_extraction(document_id)

    document = store.documents[document_id]
    assert document.status == DocumentStatus.FAILED
    assert "not a payslip" in document.failure_reason


async def test_a_rerun_that_succeeds_clears_the_reason_an_earlier_failure_left(ai, store, monkeypatch):
    document_id = _officer_set_type(store, monkeypatch, "C067_BudgetProposal.pdf", BUDGET_PROPOSAL_TEXT, "unrecognised")
    store.documents[document_id].failure_reason = "Extraction output could not be used: cut off"
    ai.reply("extract", extraction({"document_title": ("Proposal Budget", 0.95)}))

    await pipeline.rerun_extraction(document_id)

    assert store.documents[document_id].status == DocumentStatus.ANALYZED
    assert store.documents[document_id].failure_reason is None


def test_a_passing_mention_of_a_loan_is_not_a_loan_document():
    from app.langgraph.rule_classify import classify_by_rules

    budget = classify_by_rules("C067_BudgetProposal.pdf", BUDGET_PROPOSAL_TEXT)
    assert budget["top_candidates"] == []

    offer = classify_by_rules("scan.pdf", "We are pleased to offer you the following Term Loan Facility")
    assert offer["top_candidates"][0]["document_type"] == "list_of_loans"


# --- SCRUM-8 / SCRUM-9 additions -------------------------------------------------


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-8", "DI-017")
@pytest.mark.parametrize("confidence, expected_routing", [
    (settings.CLASSIFICATION_CONFIDENCE_THRESHOLD, "accepted"),
    (settings.CLASSIFICATION_CONFIDENCE_THRESHOLD - 0.0001, "manual_review"),
])
async def test_the_classification_threshold_is_inclusive(store, confidence, expected_routing):
    document_id = store.add("C001_Constitution.pdf", "Constitution text")
    await nodes.record_classification_result(
        document_id, {"document_type": "company_constitution", "confidence": confidence, "evidence": []}
    )

    assert store.classifications[document_id]["routing_status"] == expected_routing


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-9", "DI-018")
async def test_each_extracted_fields_own_confidence_is_saved(ai, store):
    fields, document_type = real_extraction("FATCA_Form")
    document_id = store.add("FATCA_Form_20250522.pdf", "FATCA / CRS Self-Certification")
    ai.reply("classify_agent", classification(document_type))
    ai.reply("extract", extraction(fields))

    await run(document_id)

    assert store.field_confidences[document_id] == {name: confidence for name, (_, confidence) in fields.items()}


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-9", "DI-018")
async def test_a_required_field_the_document_does_not_contain_is_saved_as_null(ai, store):
    document_id = store.add("C001_Constitution.pdf", "Constitution text with no company name")
    ai.reply("classify_agent", classification("company_constitution"))
    ai.reply("extract", extraction({"company_name": (None, 0.0)}))

    await run(document_id)

    assert "company_name" in store.fields[document_id]
    assert store.fields[document_id]["company_name"] is None


# --- SCRUM-19 Auto-trigger extraction and reclassification ----------------------


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-19", "MA-003")
async def test_a_generic_extraction_that_suggests_a_type_triggers_one_reclassification(ai, store):
    document_id = store.add("statement_scan.pdf", "OCBC Statement of Account Opening Balance")
    ai.reply("classify_agent", classification("unrecognised", 0.2, manual_review=True))
    ai.reply("extract", extraction({"document_title": ("Statement of Account", 0.9)}, escalation={
        "suggested_document_type": "bank_statement",
        "reason": "Heading and bank name read as a bank statement",
        "evidence": ["Statement of Account"],
    }))
    ai.reply("classify_agent", classification("bank_statement", 0.95))
    ai.reply("extract", extraction({"bank_name": ("OCBC", 0.97)}))

    await run(document_id)

    assert store.escalations[document_id] == ["Heading and bank name read as a bank statement"]
    assert store.classifications[document_id]["document_type"] == "bank_statement"
    assert store.documents[document_id].status == DocumentStatus.ANALYZED
    assert ai.steps() == ["classify_agent", "extract", "classify_agent", "extract"]


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-19", "MA-003")
async def test_a_second_disagreement_is_not_re_checked_again(ai, store):
    document_id = store.add("C001-P2_Payslip.pdf", "BANK STATEMENT Opening balance")
    ai.reply("classify_agent", classification("payslip", 0.96))
    ai.reply("extract", extraction({}, escalation={"reason": "A bank statement, not a payslip",
                                                   "suggested_document_type": "bank_statement"}))
    ai.reply("classify_agent", classification("payslip", 0.96))
    ai.reply("extract", extraction({}, escalation={"reason": "Still a bank statement",
                                                   "suggested_document_type": "bank_statement"}))

    await run(document_id)

    assert ai.steps() == ["classify_agent", "extract", "classify_agent", "extract"]
    assert store.documents[document_id].status == DocumentStatus.FAILED
    assert "Unresolved after 1 reclassification" in store.documents[document_id].failure_reason


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-19", "MA-003")
async def test_an_unrecognised_document_that_stays_unrecognised_after_a_recheck_goes_to_manual_review(ai, store):
    # Mirrors what the officer sees: the recheck runs, the document is not
    # failed, it stays Unrecognised and waits for the officer to pick a type.
    document_id = store.add("Fashion-Purchase-Order-Template-TemplateLab.com_.pdf", "PURCHASE ORDER Item Qty")
    ai.reply("classify_agent", classification("unrecognised", 0.2, manual_review=True))
    ai.reply("extract", extraction({"document_title": ("Purchase Order", 0.9)}, escalation={
        "suggested_document_type": "bank_statement", "reason": "Looked like a statement",
    }))
    ai.reply("classify_agent", classification("unrecognised", 0.2, manual_review=True))
    ai.reply("extract", extraction({"document_title": ("Purchase Order", 0.9)}))

    await run(document_id)

    document = store.documents[document_id]
    assert document.status == DocumentStatus.ANALYZED
    assert document.document_type == "unrecognised"
    assert store.classifications[document_id]["routing_status"] == "manual_review"


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-19", "MA-003")
async def test_the_reclassification_reason_and_both_attempts_are_recorded_in_the_trace(ai, store):
    document_id = store.add("statement_scan.pdf", "OCBC Statement of Account Opening Balance")
    ai.reply("classify_agent", classification("payslip", 0.96))
    ai.reply("extract", extraction({}, escalation={"reason": "Bank statement wording",
                                                   "suggested_document_type": "bank_statement"}))
    ai.reply("classify_agent", classification("bank_statement", 0.95))
    ai.reply("extract", extraction({"bank_name": ("OCBC", 0.97)}))

    await run(document_id)

    records = (trace.get(document_id) or {}).get("steps", [])
    escalation = next(r for r in records if r["step"] == "record_escalation")
    assert escalation["reason"] == "Bank statement wording"
    assert escalation["attempts"] == 1
    assert any(r["step"] == "reclassify" and r["reason"] == "Bank statement wording" for r in records)
    assert sum(1 for r in records if r["step"] == "extraction") == 2


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-19", "MA-004")
async def test_a_low_confidence_named_type_is_extracted_with_the_generic_schema(ai, store):
    # Known defect (REG-004), see TEST_PLAN.md Part 8: the code uses the named
    # type's schema here; the desired behaviour is the generic prompt.
    document_id = store.add("scan_0042.pdf", "Net Pay ... Employer")
    ai.reply("classify_agent", classification("payslip", 0.8))
    ai.reply("extract", extraction({"net_pay": (5200, 0.9)}))

    await run(document_id)

    assert store.classifications[document_id]["routing_status"] == "manual_review"
    extract_prompt = str(ai.calls[-1][1])
    assert "net_pay" not in extract_prompt
