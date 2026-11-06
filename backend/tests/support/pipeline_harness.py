"""
Sprint 2's LangGraph pipeline harness (SCRUM-206), shared by the document-pipeline regression suites: the real
pipeline runs end to end (queue -> ingest -> classify -> extract -> analyzed), and only Claude's replies are scripted
(FakeClaude, in place of build_chat_anthropic, answering from a Script). Storage, the database and OCR are an
in-memory FakeStore. Extraction replies can reuse the real results captured on the Anchorpoint case
(fixtures/regression/anchorpoint.json).

A test module uses the fixtures by assigning them (`ai = pipeline_harness.ai`), so pytest collects them there.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from app.config import settings
from app.langgraph import text_cache, trace
from app.langgraph.extract import _EXTRACTION_MAX_TOKENS
from app.models import DocumentRecord, DocumentStatus, RoutingStatus
from app.events import Event, types
from app.workers import documents as doc_worker

FIXTURES = Path(__file__).parent.parent / "fixtures" / "regression"
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
    """The pipeline's steps, in order; the agent-run spans recorded alongside them (telemetry.steps_into) are left out."""
    return [
        s["step"]
        for s in (trace.get(document_id) or {}).get("steps", [])
        if s["step"] != "span"
    ]


@pytest.fixture(autouse=True)
def _clear_pipeline_memory():
    yield
    for document_id in [f"doc-{n}" for n in range(1, 10)]:
        text_cache.discard(document_id)
        trace._traces.pop(document_id, None)
