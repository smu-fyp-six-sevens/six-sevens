"""
Regression tests (SCRUM-246) for fraud detection — "Test Cases - Sprint
3.xlsx", Fraud Detection sheet, FD-001 – FD-026:

  SCRUM-65  Image manipulation: boundary, scaling & colour    FD-001, FD-002
  SCRUM-63  Text overlay                                      FD-003, FD-004
  SCRUM-62  Layout anomalies                                  FD-005, FD-006
  SCRUM-66  Document metadata                                 FD-007, FD-008
  SCRUM-68  Numerical validation                              FD-009, FD-010
  SCRUM-69  Date anomalies                                    FD-011, FD-012
  SCRUM-64  Image manipulation: resolution & compression      FD-013, FD-014
  SCRUM-119 Lead Detection Agent                              FD-015 – FD-020
  SCRUM-61  Font anomalies (type, size, emphasis)             FD-021 – FD-024
  SCRUM-70  Spelling anomalies                                FD-025, FD-026

The real detection graph runs end to end: load context -> lead plan -> the
three specialists in parallel (each a real tool-calling subgraph) -> lead
review -> second look (at most once) -> synthesize. The tools are the real
ones and run the real checks in fraud_detection/ on the document's bytes.
Only Claude's replies are scripted, by FakeClaude standing in for
build_chat_anthropic — no request ever leaves the test (conftest.py also
blocks real Claude calls). The database and storage are in-memory fakes.

The scripted specialists behave like a well-behaved Claude: they call the
tools named in the script, then rate every finding those tools returned.
What a test controls is which tools run and how findings are rated; what the
tools find, and how the graph merges it into one report, is all real code.

Documents are generated in the test (pymupdf), each carrying the anomaly its
test case injects, because the Sprint 3 test files (C005, C028, C034...)
aren't in the repository. Whether Claude itself chooses the right tools and
ratings on the real documents is checked by the manual live run at the end
of each sprint.
"""
from __future__ import annotations

import io
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import pytest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from PIL import Image, ImageDraw

from app import findings_repository
from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.external_validation.sg_reference.holidays import BUNDLED_HOLIDAYS
from app.langgraph import trace
from app.langgraph.detection import tools as detection_tools
from app.langgraph.detection.graph import run_detection
from app.langgraph.detection.specialists import SPECIALISTS
from app.models import DocumentRecord, DocumentStatus, RoutingStatus

FIXTURES = Path(__file__).parent / "fixtures" / "regression"

LAYOUT, CONTENT, DIGITAL = "layout_rendering", "content_validation", "digital_authenticity"


# --- scripted Claude -------------------------------------------------------------


@dataclass
class Specialist:
    """One specialist's scripted behaviour: which tools it calls, and how it rates what they find."""

    tools: list[str] = field(default_factory=list)
    severity: str = "medium"
    # finding id or tool name -> severity, overriding `severity`
    rate: dict[str, str] = field(default_factory=dict)
    # finding ids or tool names it dismisses as false positives
    dismiss: set[str] = field(default_factory=set)
    summary: str = "Reviewed within my expertise."


@dataclass
class Review:
    summary: str
    second_look: dict | None = None


class DetectionAI:
    """Answers each AI step of the detection graph from a script, and records every call."""

    def __init__(self) -> None:
        self.briefs = {name: f"Brief for {name}." for name in SPECIALISTS}
        self.specialists: dict[str, Specialist] = {name: Specialist() for name in SPECIALISTS}
        self.second_looks: dict[str, Specialist] = {}
        self.reviews: list[Review] = []
        self.calls: list[str] = []

    def answer(self, messages: list[BaseMessage]) -> AIMessage:
        system = _text(messages[0].content)
        if "write each one a brief" in system:
            self.calls.append("lead_plan")
            return AIMessage(content=json.dumps({"briefs": self.briefs}))
        if "Your specialists have reported" in system:
            self.calls.append("lead_review")
            review = self.reviews.pop(0) if self.reviews else Review("Nothing worth flagging was found.")
            return AIMessage(content=json.dumps({"summary": review.summary, "second_look": review.second_look}))
        if "fill in the inputs of a bank's forgery-check numerical checks" in system:
            self.calls.append("numerical_binder")
            return AIMessage(content=json.dumps({"bindings": []}))
        name = next(n for n, spec in SPECIALISTS.items() if f"You are the {spec.title}" in system)
        second_look = "SECOND LOOK requested" in system
        script = self.second_looks[name] if second_look else self.specialists[name]
        step = f"{name}:second_look" if second_look else name
        # Its own tools, once: a result already there from another tool (the pre-run injection check) doesn't count.
        if not any(isinstance(m, ToolMessage) and m.name in script.tools for m in messages) and script.tools:
            self.calls.append(f"{step}:tools")
            return AIMessage(content="", tool_calls=[
                {"name": tool, "args": {}, "id": f"{step}-{tool}"} for tool in script.tools
            ])
        self.calls.append(f"{step}:report")
        return AIMessage(content=json.dumps(_assess(script, messages)))


def _assess(script: Specialist, messages: list[BaseMessage]) -> dict:
    assessments = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        result = json.loads(_text(message.content))
        for finding in result.get("findings") or []:
            keys = (finding["id"], result["tool"])
            if any(key in script.dismiss for key in keys):
                assessments.append({"finding_id": finding["id"], "dismiss": True, "severity": None,
                                    "rationale": "False positive for this document."})
                continue
            severity = next((script.rate[key] for key in keys if key in script.rate), script.severity)
            assessments.append({"finding_id": finding["id"], "dismiss": False, "severity": severity,
                                "rationale": f"Rated {severity}."})
    return {"summary": script.summary, "assessments": assessments, "uncertain": False, "uncertainty_reason": None}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if isinstance(block, dict))


class FakeClaude:
    """Duck-types the parts of ChatAnthropic the detection graph uses."""

    def __init__(self, ai: DetectionAI) -> None:
        self.ai = ai

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        return self.ai.answer(list(messages))


# --- in-memory database and storage ------------------------------------------------


class FakeStore:
    """Stands in for document_repository, extracted_fields_repository and storage_service."""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentRecord] = {}
        self.fields: dict[str, list[ExtractedField]] = {}
        self.files: dict[str, bytes] = {}
        self.statuses: dict[str, list[str]] = {}
        self.guard_results: dict[str, dict] = {}
        self.reports: dict[str, dict] = {}
        self.errors: dict[str, str] = {}

    def add(self, filename: str, document_type: str, file: bytes, *, fields: dict | None = None,
            metadata: dict | None = None) -> str:
        """An approved document awaiting detection, its file in storage and its fields extracted."""
        document = DocumentRecord(
            id=f"doc-{len(self.documents) + 1}",
            case_id="case-1",
            filename=filename,
            document_type=document_type,
            classification_confidence=0.97,
            routing_status=RoutingStatus.ACCEPTED,
            storage_path=f"case-1/{filename}",
            status=DocumentStatus.APPROVED,
            uploaded_at=datetime.now(timezone.utc),
            metadata=metadata,
            extracted_fields=fields,
        )
        self.add_record(document, file)
        return document.id

    def add_record(self, document: DocumentRecord, file: bytes | None = None) -> None:
        self.documents[document.id] = document
        self.fields[document.id] = [
            ExtractedField(field_name=name, field_type="string", field_value=value, confidence=0.97)
            for name, value in (document.extracted_fields or {}).items()
        ]
        if file is not None:
            assert document.storage_path is not None
            self.files[document.storage_path] = file

    # document_repository
    def get_document(self, document_id, include_fields=True, include_metadata=False):
        return self.documents.get(document_id)

    def get_guard_result(self, document_id):
        """What the content guard saved at ingest: clean, unless a test sets one."""
        return self.guard_results.get(document_id, {
            "version": 2, "risk": "none", "checks_run": ["hidden_text", "image_text", "dictionary"],
            "spans": [], "spans_dropped": 0,
        })

    def get_document_for_detection(self, document_id):
        document = self.documents.get(document_id)
        return (document, self.get_guard_result(document_id)) if document else None

    def list_documents(self, case_id=None, **kwargs):
        return [d for d in self.documents.values() if d.case_id == case_id]

    def _status(self, document_id, status):
        self.documents[document_id].status = status
        self.statuses.setdefault(document_id, []).append(status.value)

    def mark_detecting(self, document_id):
        self._status(document_id, DocumentStatus.DETECTING)

    def mark_detected(self, document_id):
        self._status(document_id, DocumentStatus.DETECTED)

    def revert_to_approved(self, document_id):
        self._status(document_id, DocumentStatus.APPROVED)

    def end_unfinished_detection(self, document_id, *, has_report):
        if self.documents[document_id].status == DocumentStatus.DETECTING:
            self._status(document_id, DocumentStatus.DETECTED if has_report else DocumentStatus.APPROVED)

    # detection_results
    def mark_started(self, document_id):
        self.errors.pop(document_id, None)

    def save_report(self, document_id, report):
        self.reports[document_id] = report

    def save_error(self, document_id, error):
        self.errors[document_id] = error

    def has_report(self, document_id):
        return document_id in self.reports

    # extracted_fields_repository
    def get_extracted_fields(self, document_id):
        return self.fields.get(document_id, [])

    # storage_service
    def download_file(self, storage_path):
        return self.files[storage_path]


@pytest.fixture
def ai(monkeypatch) -> DetectionAI:
    script = DetectionAI()
    fake = lambda model, max_tokens, **kwargs: FakeClaude(script)  # noqa: E731
    monkeypatch.setattr("app.langgraph._anthropic.build_chat_anthropic", fake)
    monkeypatch.setattr("app.langgraph.detection.field_checks.binder.build_chat_anthropic", fake)
    return script


@pytest.fixture
def store(monkeypatch) -> FakeStore:
    fake = FakeStore()
    for module in ("app.langgraph.detection.graph", "app.langgraph.detection.tools"):
        monkeypatch.setattr(f"{module}.document_repository", fake)
        monkeypatch.setattr(f"{module}.extracted_fields_repository", fake)
    monkeypatch.setattr("app.langgraph.detection.tools.storage_service", fake)
    monkeypatch.setattr("app.langgraph.detection.graph.detection_results", fake)
    # These fraud-tool regressions test each document in isolation. Identity
    # matching is covered separately by test_detection_case_match.py.
    monkeypatch.setattr(
        "app.case_service.get_case_match_for_document",
        lambda _document_id, **_kwargs: None,
    )

    async def bundled_holidays():
        return BUNDLED_HOLIDAYS

    # Bank statements fetch the live public-holiday list; the bundled copy keeps the test offline.
    monkeypatch.setattr("app.fraud_detection.external_validation.runner.public_holidays", bundled_holidays)
    monkeypatch.setattr(trace, "_persist", lambda document_id: None)
    monkeypatch.setattr(trace, "get_persisted", lambda document_id: None)
    # Detection ends by comparing the document with its case's identity; that has its own
    # regression file (test_regression_case_match), so here the case is taken to match.
    monkeypatch.setattr(
        "app.case_service.get_case_match_for_document",
        lambda document_id, **_kwargs: None,
    )
    monkeypatch.setattr(findings_repository, "sync_document", lambda document_id, case_id, report: 0)
    return fake


@pytest.fixture(autouse=True)
def _clear_detection_memory():
    yield
    trace._traces.clear()
    detection_tools._context.clear()


def findings_from(report: dict, tool: str) -> list[dict]:
    return [f for f in report["findings"] if f["tool"] == tool]


def indicators(report: dict, tool: str) -> set[str]:
    return {f["indicator"] for f in findings_from(report, tool)}


def consulted(report: dict, name: str) -> dict:
    return next(c for c in report["consulted"] if c["specialist"] == name)


# --- generated documents ----------------------------------------------------------
# Each builder writes a small native-text PDF; the tampered variant carries
# exactly the anomaly its test case injects into the real test document.


def _pdf(build: Callable[[pymupdf.Document], None]) -> bytes:
    doc = pymupdf.open()
    build(doc)
    data = doc.tobytes()
    doc.close()
    return data


_LABELS = ["Basic salary", "Overtime pay", "Transport allowance", "Employee CPF", "Income tax", "Net pay"]
_AMOUNTS = ["$5,200.00", "$480.00", "$300.00", "$1,040.00", "$212.00", "$4,728.00"]


def payslip_pdf(*, font: dict[int, str] | None = None, size: dict[int, float] | None = None,
                underline: int | None = None) -> bytes:
    """A payslip's label/amount table; the overrides restyle one label (SCRUM-61's three tampers)."""

    def build(doc):
        page = doc.new_page(width=400, height=300)
        for i, (label, amount) in enumerate(zip(_LABELS, _AMOUNTS, strict=True)):
            y = 80 + i * 18
            label_font = (font or {}).get(i, "helv")
            label_size = (size or {}).get(i, 9)
            page.insert_text((50, y), label, fontsize=label_size, fontname=label_font)
            page.insert_text((250, y), amount, fontsize=9, fontname="helv")
            if i == underline:
                width = pymupdf.get_text_length(label, label_font, label_size)
                page.draw_line((50, y + 2), (50 + width, y + 2), width=0.6)

    return _pdf(build)


_CLAUSE = (
    "The registered office of the Company shall be at such place in Singapore "
    "as the directors may from time to time determine, and the Company shall "
    "keep its registers at that office in accordance with the Act."
)


def clauses_pdf(*, indent_third: bool = False, overlay: str | None = None) -> bytes:
    """Four body paragraphs; tampered by indenting one (SCRUM-62) or typing over one (SCRUM-63)."""

    def build(doc):
        page = doc.new_page(width=612, height=792)
        y = 90
        for i in range(4):
            left = 96 if indent_third and i == 2 else 72
            page.insert_textbox(pymupdf.Rect(left, y, 540, y + 70), _CLAUSE, fontsize=11, fontname="helv")
            y += 80
        if overlay:
            page.insert_text((150, 104), overlay, fontsize=11, fontname="helv")

    return _pdf(build)


_CREST = pymupdf.Rect(100, 100, 220, 240)


def _crest(width: int = 300, height: int = 350) -> Image.Image:
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 40, width - 40, height - 90), fill=(220, 40, 50), outline=(30, 30, 30), width=4)
    draw.polygon([(width // 2, 70), (width // 2 + 40, 160), (width // 2 - 40, 160)], fill=(250, 250, 250))
    draw.rectangle((60, height - 80, width - 60, height - 60), fill=(200, 160, 30))
    return image


def _encode(image: Image.Image, fmt: str = "PNG", quality: int = 90) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, quality=quality)
    return buffer.getvalue()


def id_card_pdf(*, replace_crest: bool = False) -> bytes:
    """An ID page with one crest; tampered by painting the crest over with a low-resolution JPEG copy."""

    def build(doc):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), "REPUBLIC OF SINGAPORE", fontsize=16)
        page.insert_image(_CREST, stream=_encode(_crest()))
        if replace_crest:
            cover = pymupdf.Rect(_CREST.x0 - 0.8, _CREST.y0 - 0.8, _CREST.x1 + 0.8, _CREST.y1 + 0.8)
            page.draw_rect(cover, color=None, fill=(1, 1, 1), overlay=True)
            low_res = _encode(_crest().resize((52, 60)), "JPEG", quality=16)
            page.insert_image(cover, stream=low_res, keep_proportion=False)

    return _pdf(build)


def image_only_pdf() -> bytes:
    """A page that is one picture: no text for the font and layout checks to read."""

    def build(doc):
        page = doc.new_page(width=200, height=200)
        page.insert_image(pymupdf.Rect(20, 20, 180, 120), stream=_encode(_crest()))

    return _pdf(build)


def certificate_pdf(company_name: str) -> bytes:
    def build(doc):
        page = doc.new_page(width=595, height=842)
        lines = [
            "ACCOUNTING AND CORPORATE REGULATORY AUTHORITY",
            "CERTIFICATE CONFIRMING INCORPORATION OF COMPANY",
            f"This is to confirm that {company_name}",
            "Registration No. 201634564N",
            "is incorporated under the Companies Act 1967 on 26 March 2016",
            "and that the company is a private company limited by shares.",
        ]
        for i, line in enumerate(lines):
            page.insert_text((72, 100 + i * 24), line, fontsize=11, fontname="helv")

    return _pdf(build)


# A credit report's embedded metadata (what ingestion's exiftool stored).
TAMPERED_METADATA = {
    "Author": "FraudTests",
    "Creator": "Adobe Acrobat Pro (64-bit) 24.1",
    "Producer": "Adobe Acrobat Pro (64-bit) 24.1",
}
CLEAN_METADATA = {
    "Producer": "Experian Credit Bureau Report Generator",
    "Creator": "Experian",
    "CreateDate": "2026:04:14 09:00:00",
    "ModifyDate": "2026:04:14 09:00:00",
}


# --- SCRUM-65 Image manipulation: boundary, scaling & colour ---------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-65", "FD-001")
async def test_an_image_painted_over_with_a_replacement_is_flagged(ai, store):
    document_id = store.add("IRAS_NOA_Company_20260617.pdf", "notice_of_assessment", id_card_pdf(replace_crest=True))
    ai.specialists[LAYOUT] = Specialist(tools=["image_tampering_tool"])

    report = await run_detection(document_id)

    assert report["verdict"] == "flagged"
    assert "Image Overlay" in indicators(report, "image_tampering_tool")
    assert all(f["specialists"] == [LAYOUT] for f in findings_from(report, "image_tampering_tool"))


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-65", "FD-002")
async def test_a_clean_image_is_not_flagged_for_tampering(ai, store):
    document_id = store.add("C020_NOA.pdf", "notice_of_assessment", id_card_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["image_tampering_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "image_tampering_tool") == []
    assert "image_tampering_tool" in consulted(report, LAYOUT)["tools_called"]


async def test_a_rerun_clears_the_error_an_earlier_attempt_left(ai, store):
    document_id = store.add("C020_NOA.pdf", "notice_of_assessment", id_card_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["image_tampering_tool"])
    store.errors[document_id] = "Cancelled by loan officer"

    await run_detection(document_id)

    assert document_id not in store.errors
    assert store.reports[document_id]["verdict"]


# --- SCRUM-63 Text overlay -----------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-63", "FD-003")
async def test_text_typed_over_other_text_is_flagged_as_a_text_overlay(ai, store):
    document_id = store.add("C005-P2_Payslip.pdf", "payslip", clauses_pdf(overlay="SGD 12,450.00 paid in full"))
    ai.specialists[LAYOUT] = Specialist(tools=["layout_anomalies_tool"])

    report = await run_detection(document_id)

    overlays = [f for f in findings_from(report, "layout_anomalies_tool") if f["type"] == "Text Overlay"]
    assert overlays
    assert "SGD 12,450.00" in overlays[0]["text"]
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-63", "FD-004")
async def test_a_clean_document_has_no_text_overlay(ai, store):
    document_id = store.add("C009-P2_Payslip.pdf", "payslip", clauses_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["layout_anomalies_tool"])

    report = await run_detection(document_id)

    assert [f for f in findings_from(report, "layout_anomalies_tool") if f["type"] == "Text Overlay"] == []


# --- SCRUM-62 Layout anomalies ---------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-62", "FD-005")
async def test_an_indented_clause_is_reported_as_a_layout_anomaly(ai, store):
    document_id = store.add("C018_Constitution.pdf", "company_constitution", clauses_pdf(indent_third=True))
    ai.specialists[LAYOUT] = Specialist(tools=["layout_anomalies_tool"])

    report = await run_detection(document_id)

    layout = findings_from(report, "layout_anomalies_tool")
    assert [f["type"] for f in layout] == ["Indentation"]
    assert report["verdict"] == "flagged"
    # "No unrelated fraud dimensions should be reported" (FD-005's expected result).
    assert {f["tool"] for f in report["findings"]} == {"layout_anomalies_tool"}


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-62", "FD-006")
async def test_a_clean_document_reports_no_layout_anomaly(ai, store):
    document_id = store.add("C035_NOA.pdf", "notice_of_assessment", clauses_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["layout_anomalies_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "layout_anomalies_tool") == []
    assert report["verdict"] == "clean"


# --- SCRUM-66 Document metadata --------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-66", "FD-007")
async def test_metadata_showing_an_editor_and_a_placeholder_author_is_flagged(ai, store):
    document_id = store.add("C028_CreditReport.pdf", "credit_report", payslip_pdf(), metadata=TAMPERED_METADATA)
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"], rate={"metadata_tool-1": "low"})

    report = await run_detection(document_id)

    metadata = {f["field"]: f for f in findings_from(report, "metadata_tool")}
    assert metadata["author"]["value"] == "FraudTests"
    assert "Adobe Acrobat" in metadata["creator"]["value"]
    assert all(f["specialists"] == [DIGITAL] for f in metadata.values())
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-66", "FD-008")
async def test_metadata_from_the_issuers_own_system_is_clean(ai, store):
    document_id = store.add("C027_CreditReport.pdf", "credit_report", payslip_pdf(), metadata=CLEAN_METADATA)
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "metadata_tool") == []
    assert "metadata_tool" in consulted(report, DIGITAL)["tools_called"]


# --- SCRUM-68 Numerical validation -----------------------------------------------------


def _transactions(tamper: bool) -> list[dict]:
    return [
        {"date": "01 Jun 2026", "description": "Balance B/F", "amount": 0, "balance": 46873.63},
        {"date": "05 Jun 2026", "description": "Inward credit", "amount": 18141.88, "balance": 65015.51},
        {"date": "12 Jun 2026", "description": "Supplier transfer", "amount": -5709.40,
         "balance": 69306.11 if tamper else 59306.11},
        {"date": "30 Jun 2026", "description": "Service charge", "amount": -35.00,
         "balance": 69271.11 if tamper else 59271.11},
    ]


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-68", "FD-009")
async def test_a_balance_that_does_not_reconcile_is_flagged(ai, store):
    document_id = store.add("C002_BankStatement_2026-06_to_2026-08.pdf", "bank_statement", payslip_pdf(),
                            fields={"transactions": _transactions(tamper=True)})
    ai.specialists[CONTENT] = Specialist(tools=["numerical_checks_tool"], severity="high")

    report = await run_detection(document_id)

    numerical = {f["check_code"]: f for f in findings_from(report, "numerical_checks_tool")}
    # The edited balance breaks the running balance, and so the opening/closing reconciliation too.
    assert set(numerical) == {"BS.RUNNING_BALANCE", "BS.OPEN_CLOSE"}
    assert numerical["BS.RUNNING_BALANCE"]["severity"] == "high"
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-68", "FD-010")
async def test_balances_that_reconcile_raise_no_numerical_finding(ai, store):
    document_id = store.add("C003_BankStatement_2026-06_to_2026-08.pdf", "bank_statement", payslip_pdf(),
                            fields={"transactions": _transactions(tamper=False)})
    ai.specialists[CONTENT] = Specialist(tools=["numerical_checks_tool"])

    report = await run_detection(document_id)

    assert [f for f in findings_from(report, "numerical_checks_tool") if f["check_code"] == "BS.RUNNING_BALANCE"] == []


# --- SCRUM-69 Date anomalies ------------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-69", "FD-011")
async def test_an_incorporation_date_in_the_future_is_flagged(ai, store):
    document_id = store.add("BizFile_112712.pdf", "certificate_of_incorporation", certificate_pdf("ACME PTE LTD"),
                            fields={"incorporation_date": "2027-04-18", "uen": "201634564N"})
    ai.specialists[CONTENT] = Specialist(tools=["date_consistency_tool"], severity="high")

    report = await run_detection(document_id)

    dates = findings_from(report, "date_consistency_tool")
    future = [f for f in dates if f["check_code"] == "GEN.DATE_VALID" and f["field"] == "incorporation_date"]
    assert len(future) == 1
    assert future[0]["stated"] == "2027-04-18"
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-69", "FD-011")
async def test_a_specialist_that_runs_no_tool_is_reported_as_incomplete_not_as_a_pass(ai, store):
    # FD-011's live run: the content specialist came back without running any
    # tool, so the future date was never checked. The report must say so —
    # "incomplete", never quietly "nothing found".
    document_id = store.add("BizFile_112712.pdf", "certificate_of_incorporation", certificate_pdf("ACME PTE LTD"),
                            fields={"incorporation_date": "2027-04-18", "uen": "201634564N"})
    ai.specialists[CONTENT] = Specialist(tools=[])

    report = await run_detection(document_id)

    content = consulted(report, CONTENT)
    assert content["tools_called"] == []
    assert content["uncertain"] is True


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-69", "FD-012")
async def test_consistent_dates_raise_no_date_finding(ai, store):
    document_id = store.add("C010_CertIncorp.pdf", "certificate_of_incorporation", certificate_pdf("ACME PTE LTD"),
                            fields={"incorporation_date": "2016-03-26", "certificate_date": "2016-03-26",
                                    "uen": "201634564N"})
    ai.specialists[CONTENT] = Specialist(tools=["date_consistency_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "date_consistency_tool") == []


# --- SCRUM-64 Image manipulation: resolution & compression ---------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-64", "FD-013")
async def test_a_crest_replaced_at_a_lower_resolution_and_quality_is_flagged(ai, store):
    document_id = store.add("C011-P1_NRICPassport.pdf", "passport", id_card_pdf(replace_crest=True))
    ai.specialists[LAYOUT] = Specialist(tools=["image_manipulation_tool"])

    report = await run_detection(document_id)

    assert indicators(report, "image_manipulation_tool") == {"Resolution Mismatch", "Compression Mismatch"}
    assert consulted(report, LAYOUT)["title"] == "Layout & Rendering Analyst"
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-64", "FD-014")
async def test_an_untouched_image_raises_no_resolution_or_compression_finding(ai, store):
    # Like C004 in the live run: one image, nothing to compare it with — a
    # pass with nothing to check, not a finding and not a failed check.
    document_id = store.add("C004-P1_NRICPassport.pdf", "passport", id_card_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["image_manipulation_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "image_manipulation_tool") == []
    [skipped] = [n for n in report["nothing_to_check"] if n["tool"] == "image_manipulation_tool"]
    assert skipped["reason"].startswith("Only 1 distinct image asset")
    assert report["unable_to_assess"] == []


# --- SCRUM-119 Lead Detection Agent ------------------------------------------------------


def _credit_report_with_font_and_metadata_tampering(store: FakeStore) -> str:
    """C028 in the live run: a restyled field and Acrobat/"FraudTests" metadata."""
    return store.add("C028_CreditReport.pdf", "credit_report", payslip_pdf(font={2: "cour"}),
                     metadata=TAMPERED_METADATA)


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-015")
async def test_detection_produces_one_verdict_one_summary_and_one_findings_list(ai, store):
    document_id = _credit_report_with_font_and_metadata_tampering(store)
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool", "layout_anomalies_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"])
    ai.reviews = [Review("The font of one field and the file's metadata both point to editing.")]

    report = await run_detection(document_id)

    assert report["verdict"] == "flagged"
    assert report["summary"] == "The font of one field and the file's metadata both point to editing."
    tools = {f["tool"] for f in report["findings"]}
    assert {"font_consistency_tool", "metadata_tool"} <= tools
    for finding in report["findings"]:
        assert finding["specialists"], finding
        assert set(finding["specialists"]) <= set(SPECIALISTS)
    assert len(report["consulted"]) >= 2
    assert ai.calls.count("lead_plan") == 1
    assert store.statuses[document_id] == ["detecting", "detected"]


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-016")
async def test_the_report_lists_each_specialist_with_its_brief_and_the_tools_it_ran(ai, store):
    document_id = store.add("C030-P2_Payslip.pdf", "payslip", payslip_pdf())
    ai.briefs = {
        LAYOUT: "Native-text payslip: check fonts, spacing and images.",
        CONTENT: "Check the CPF arithmetic and dates.",
        DIGITAL: "Check the producing software and timestamps.",
    }
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool", "image_tampering_tool"])
    ai.specialists[CONTENT] = Specialist(tools=["date_consistency_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"])

    report = await run_detection(document_id)

    # Every specialist reviews every document (the lead briefs, it doesn't
    # skip), so "each skipped specialist with its reason" is an empty list.
    assert {c["specialist"] for c in report["consulted"]} == set(SPECIALISTS)
    for name, brief in ai.briefs.items():
        assert consulted(report, name)["brief"] == brief
        assert consulted(report, name)["title"] == SPECIALISTS[name].title
    assert consulted(report, LAYOUT)["tools_called"] == ["hidden_text_tool", "font_consistency_tool", "image_tampering_tool"]
    assert consulted(report, CONTENT)["tools_called"] == ["date_consistency_tool"]
    # The injection check runs for digital_authenticity on every document, before its own choice of tools.
    assert consulted(report, DIGITAL)["tools_called"] == ["prompt_injection_tool", "metadata_tool"]
    # A check with nothing to look at is listed with its reason, as a pass.
    assert any(n["tool"] == "image_tampering_tool" for n in report["nothing_to_check"])


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-017")
@pytest.mark.parametrize("severity", ["low", "medium", "high"])
async def test_any_finding_rated_low_or_above_flags_the_document(ai, store, severity):
    document_id = store.add("C028_CreditReport.pdf", "credit_report", payslip_pdf(), metadata=TAMPERED_METADATA)
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"], severity="info",
                                         rate={"metadata_tool-1": severity})

    report = await run_detection(document_id)

    assert report["verdict"] == "flagged"
    assert report["findings"][0]["severity"] == severity


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-017")
async def test_findings_the_specialist_rates_info_or_dismisses_do_not_flag(ai, store):
    document_id = store.add("C028_CreditReport.pdf", "credit_report", payslip_pdf(), metadata=TAMPERED_METADATA)
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"], severity="info", dismiss={"metadata_tool-1"})

    report = await run_detection(document_id)

    assert report["verdict"] == "clean"
    assert {f["severity"] for f in report["findings"]} == {"info"}
    assert [d["id"] for d in consulted(report, DIGITAL)["dismissed"]] == ["metadata_tool-1"]


async def clean_report(ai: DetectionAI, store: FakeStore) -> dict:
    """FD-018: a clean certificate where every check runs."""
    document_id = store.add("C004-P1_NRICPassport.pdf", "certificate_of_incorporation",
                            certificate_pdf("ACME PTE LTD"), metadata=CLEAN_METADATA,
                            fields={"incorporation_date": "2016-03-26", "uen": "201634564N"})
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])
    ai.specialists[CONTENT] = Specialist(tools=["date_consistency_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"])
    return await run_detection(document_id)


async def inconclusive_report(ai: DetectionAI, store: FakeStore) -> dict:
    """FD-019: a page that is one picture, so the font check can't read any text."""
    document_id = store.add("clean_test.pdf", "certificate_of_incorporation", image_only_pdf(),
                            metadata=CLEAN_METADATA)
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"])
    ai.reviews = [Review("No findings. The metadata check passed, but the fonts could not be checked.")]
    return await run_detection(document_id)


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-018")
async def test_a_clean_document_where_every_check_runs_is_clean(ai, store):
    report = await clean_report(ai, store)

    assert report["findings"] == []
    assert report["unable_to_assess"] == []
    assert report["verdict"] == "clean"
    assert report["summary"] == "Nothing worth flagging was found."


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-019")
async def test_no_findings_but_a_check_that_could_not_run_is_inconclusive(ai, store):
    report = await inconclusive_report(ai, store)

    assert report["findings"] == []
    assert [u["tool"] for u in report["unable_to_assess"]] == ["font_consistency_tool"]
    assert report["verdict"] == "inconclusive"
    assert "could not be checked" in report["summary"]


def _second_look_request(finding_id: str, to: str) -> dict:
    return {"finding_id": finding_id, "to": to,
            "question": "Please examine the PDF metadata for the producing software.",
            "reason": "A restyled field alone could be a template quirk."}


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-020")
async def test_a_second_look_runs_once_on_a_different_specialist_and_shows_the_question(ai, store):
    document_id = _credit_report_with_font_and_metadata_tampering(store)
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=[])  # first pass: nothing run
    ai.second_looks[DIGITAL] = Specialist(tools=["metadata_tool"])
    ai.reviews = [
        Review("A restyled field; asking for a second opinion.",
               _second_look_request("font_consistency_tool-1", DIGITAL)),
        # The lead asks again after the second look: refused, it's once per run.
        Review("Font and metadata both point to editing.", _second_look_request("font_consistency_tool-1", CONTENT)),
    ]

    report = await run_detection(document_id)

    assert report["second_look"] == {
        "from": LAYOUT,
        "to": DIGITAL,
        "question": "Please examine the PDF metadata for the producing software.",
        "reason": "A restyled field alone could be a template quirk.",
    }
    assert ai.calls.count(f"{DIGITAL}:second_look:tools") == 1
    assert not any(call.startswith(f"{CONTENT}:second_look") for call in ai.calls)
    assert consulted(report, DIGITAL)["second_look"] is True
    second_look_findings = findings_from(report, "metadata_tool")
    assert second_look_findings
    assert all(f["via_second_look"] for f in second_look_findings)
    assert ai.calls.count("lead_review") == 2


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-020")
async def test_a_second_look_back_to_the_specialist_that_raised_the_finding_is_refused(ai, store):
    document_id = _credit_report_with_font_and_metadata_tampering(store)
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])
    ai.reviews = [Review("Asking the same specialist again.", _second_look_request("font_consistency_tool-1", LAYOUT))]

    report = await run_detection(document_id)

    assert report["second_look"] is None
    assert not any("second_look" in call for call in ai.calls)


# --- SCRUM-61 Font anomalies -------------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-61", "FD-021")
async def test_a_label_in_another_typeface_is_flagged_and_kept_in_the_lead_report(ai, store):
    # FD-021's remark: the font finding was detected but missing from the
    # lead's report. The report's findings list is merged in code from the
    # tool results, so a finding the specialist didn't dismiss is always in it.
    document_id = store.add("C021_SourceWealth_BS.pdf", "source_of_wealth", payslip_pdf(font={2: "cour"}))
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])
    ai.reviews = [Review("Summary that does not mention the font.")]

    report = await run_detection(document_id)

    [finding] = findings_from(report, "font_consistency_tool")
    assert finding["deviation_type"] == "font"
    assert finding["locations"][0]["text"] == "Transport allowance"
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-61", "FD-022")
async def test_a_label_in_another_font_size_is_flagged(ai, store):
    document_id = store.add("C024-P1_ProofAddress.pdf", "proof_of_address", payslip_pdf(size={0: 12}))
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])

    report = await run_detection(document_id)

    [finding] = findings_from(report, "font_consistency_tool")
    assert finding["deviation_type"] == "size"
    assert finding["locations"][0]["text"] == "Basic salary"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-61", "FD-023")
async def test_an_underlined_label_is_flagged_as_an_emphasis_deviation(ai, store):
    document_id = store.add("C026_CertIncumbency.pdf", "certificate_of_incumbency", payslip_pdf(underline=3))
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])

    report = await run_detection(document_id)

    [finding] = findings_from(report, "font_consistency_tool")
    assert finding["deviation_type"] == "emphasis"
    assert finding["detected"]["underline"] is True
    assert finding["locations"][0]["text"] == "Employee CPF"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-61", "FD-024")
async def test_a_consistently_styled_document_has_no_font_finding(ai, store):
    document_id = store.add("C024_CertIncumbency.pdf", "certificate_of_incumbency", payslip_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "font_consistency_tool") == []


# --- SCRUM-70 Spelling anomalies ---------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-70", "FD-026")
async def test_a_correctly_spelt_certificate_has_no_spelling_finding(ai, store):
    document_id = store.add("C008_CertIncorp.pdf", "certificate_of_incorporation",
                            certificate_pdf("HARBOR LIGHT SHIPPING PTE LTD"),
                            fields={"company_name": "HARBOR LIGHT SHIPPING PTE LTD"})
    ai.specialists[CONTENT] = Specialist(tools=["spelling_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "spelling_tool") == []


# --- shared fixture for the Playwright suite ---------------------------------------
# frontend/e2e/specs/detection.spec.ts renders this report (FD-015 – FD-020),
# so the UI is tested against what the graph really produces. If the graph's
# output changes, this test fails until the fixture is regenerated:
#     REGENERATE_REGRESSION_FIXTURES=1 python -m pytest tests/test_regression_detection.py -k playwright

DETECTION_REPORT_FIXTURE = FIXTURES / "detection-report-c028.json"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-015 – FD-020")
async def test_the_playwright_suites_detection_report_matches_the_graph(ai, store):
    document_id = _credit_report_with_font_and_metadata_tampering(store)
    ai.briefs = {
        LAYOUT: "Credit report with printed figures: check fonts and layout.",
        CONTENT: "Check the dates and the credit report's internal consistency.",
        DIGITAL: "Check the producing software and authorship metadata.",
    }
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool", "image_tampering_tool"])
    ai.specialists[CONTENT] = Specialist(tools=["spelling_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=[])
    ai.second_looks[DIGITAL] = Specialist(tools=["metadata_tool"], rate={"metadata_tool-1": "low"})
    ai.reviews = [
        Review("A restyled field; asking for a second opinion.",
               _second_look_request("font_consistency_tool-1", DIGITAL)),
        Review("One field is set in a different typeface, and the file was last saved in Adobe Acrobat by "
               "\"FraudTests\". Together these suggest the report was edited after it was issued."),
    ]

    report = await run_detection(document_id)
    report = {**report, "generated_at": "2026-10-14T00:00:00+00:00"}

    if os.environ.get("REGENERATE_REGRESSION_FIXTURES"):
        DETECTION_REPORT_FIXTURE.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    assert json.loads(DETECTION_REPORT_FIXTURE.read_text()) == json.loads(json.dumps(report)), (
        "detection-report-c028.json is out of date with the detection graph — regenerate it (see above)"
    )


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-018 / FD-019")
@pytest.mark.parametrize("verdict, scenario", [("clean", clean_report), ("inconclusive", inconclusive_report)])
async def test_the_playwright_suites_clean_and_inconclusive_reports_match_the_graph(ai, store, verdict, scenario):
    """detection-report-clean.json and detection-report-inconclusive.json, which the browser suite renders."""
    fixture = FIXTURES / f"detection-report-{verdict}.json"
    report = {**await scenario(ai, store), "generated_at": "2026-10-14T00:00:00+00:00"}

    if os.environ.get("REGENERATE_REGRESSION_FIXTURES"):
        fixture.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    assert json.loads(fixture.read_text()) == json.loads(json.dumps(report)), (
        f"{fixture.name} is out of date with the detection graph — regenerate it (see above)"
    )
