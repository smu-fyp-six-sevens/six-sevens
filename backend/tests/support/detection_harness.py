"""
Sprint 3's detection harness (SCRUM-246), shared by the detection regression suites: the real detection graph runs
with its real tools, and only Claude's replies are scripted (FakeClaude, in place of build_chat_anthropic). The
scripted specialists call the tools named in their Specialist script and rate every finding those tools returned;
the lead's plan and review come from DetectionAI. The database and storage are an in-memory FakeStore.

A test module uses the fixtures by assigning them (`ai = detection_harness.ai`), so pytest collects them there.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from app import findings_repository
from app.fraud_detection.external_validation.sg_reference.holidays import BUNDLED_HOLIDAYS
from app.fraud_detection.font_consistency import ExtractedField
from app.langgraph import trace
from app.langgraph.detection import tools as detection_tools
from app.langgraph.detection.specialists import SPECIALISTS
from app.llm_io import parse_json_object
from app.models import DocumentRecord, DocumentStatus, RoutingStatus

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
    # The specialist's confidence in its review and in each assessment (0: it gave none).
    confidence: float = 0.0


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
        # The tool's own output is the first block, inside a fence; officer feedback may follow it.
        first = message.content[:1] if isinstance(message.content, list) else message.content
        result = parse_json_object(_text(first)) or {}
        for finding in result.get("findings") or []:
            keys = (finding["id"], result["tool"])
            if any(key in script.dismiss for key in keys):
                assessments.append({"finding_id": finding["id"], "dismiss": True, "severity": None,
                                    "rationale": "False positive for this document.", "confidence": script.confidence})
                continue
            severity = next((script.rate[key] for key in keys if key in script.rate), script.severity)
            assessments.append({"finding_id": finding["id"], "dismiss": False, "severity": severity,
                                "rationale": f"Rated {severity}.", "confidence": script.confidence})
    return {"summary": script.summary, "confidence": script.confidence, "assessments": assessments,
            "uncertain": False, "uncertainty_reason": None}


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


def wire_store(monkeypatch, fake: FakeStore) -> None:
    """Puts the in-memory store in place of the database and storage the detection graph and its tools use."""
    for module in ("app.langgraph.detection.graph", "app.langgraph.detection.tools"):
        monkeypatch.setattr(f"{module}.document_repository", fake)
        monkeypatch.setattr(f"{module}.extracted_fields_repository", fake)
    monkeypatch.setattr("app.langgraph.detection.tools.storage_service", fake)
    monkeypatch.setattr("app.langgraph.detection.graph.detection_results", fake)
    # Detection ends by comparing the document with its case's identity; that has its own regression file
    # (test_regression_case_match), so here each document is tested in isolation and the case is taken to match.
    monkeypatch.setattr("app.case_service.get_case_match_for_document", lambda document_id, **_kwargs: None)

    async def bundled_holidays():
        return BUNDLED_HOLIDAYS

    # Bank statements fetch the live public-holiday list; the bundled copy keeps the test offline.
    monkeypatch.setattr("app.fraud_detection.external_validation.runner.public_holidays", bundled_holidays)
    monkeypatch.setattr(trace, "_persist", lambda document_id: None)
    monkeypatch.setattr(trace, "get_persisted", lambda document_id: None)
    monkeypatch.setattr(findings_repository, "sync_document", lambda document_id, case_id, report: 0)


@pytest.fixture
def store(monkeypatch) -> FakeStore:
    fake = FakeStore()
    wire_store(monkeypatch, fake)
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
