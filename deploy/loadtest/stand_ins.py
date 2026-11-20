"""
The app for a load test (deploy/loadtest/README.md): the real API, events and workers, with three stand-ins so a run
spends nothing and touches no real data:
- Supabase: an in-memory database (backend/tests/support/fake_supabase.py) and in-memory file storage;
- Claude: each run (classify and extract, detection, case analysis) waits as long as a real one takes
  (LOADTEST_*_SECONDS), then writes a plain result through the app's own repositories;
- Realtime: off (the case page's live updates still go over the WebSocket from the app's own pushes).

Production code is unchanged: this patches the app before serving it. From backend/ (or the loadtest image):

    python ../deploy/loadtest/stand_ins.py      # http://localhost:8000, user loadtest / loadtest
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

# The fallback only on the laptop: in the container the script is /loadtest/stand_ins.py, which has no parents[2].
BACKEND = Path(os.environ.get("BACKEND_DIR") or Path(__file__).resolve().parents[2] / "backend")
sys.path.insert(0, str(BACKEND))

# Settings are read once, at import: everything the app needs, set before it's imported. A JWT-shaped placeholder,
# since the Supabase client checks the key's shape; nothing ever reaches it.
_PLACEHOLDER_KEY = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.bG9hZHRlc3Q"
for name, value in {
    "SUPABASE_URL": "http://supabase.invalid", "SUPABASE_SECRET_KEY": _PLACEHOLDER_KEY,
    "ANTHROPIC_API_KEY": "loadtest-no-calls", "AUTH_SECRET_KEY": "loadtest-signing-key",
    "CASE_SPEND_CAP_USD": "0", "OTEL_ENABLED": "false", "SWEEPER_ENABLED": "false",
    # config.py loads backend/.env, which never overrides what's already set: these win over it.
    "EVENT_TRANSPORT": "inprocess", "ROLE": "all", "DATA_GOV_SG_API_KEY": "", "URA_API_KEY": "",
}.items():
    os.environ.setdefault(name, value)

from app.passwords import hash_password  # noqa: E402  (no settings import behind it)

os.environ.setdefault("AUTH_USERS", f"loadtest:{hash_password(os.environ.get('LOADTEST_PASSWORD', 'loadtest'))}")

from tests.support.fake_supabase import FakeSupabase, Query  # noqa: E402

DOC_SECONDS = float(os.environ.get("LOADTEST_DOC_SECONDS", "20"))
DETECTION_SECONDS = float(os.environ.get("LOADTEST_DETECTION_SECONDS", "40"))
ANALYSIS_SECONDS = float(os.environ.get("LOADTEST_ANALYSIS_SECONDS", "30"))

# The app reads and writes from many threads at once; the fake is a plain dict of lists.
_lock = threading.RLock()
_execute = Query.execute


def _locked_execute(self):
    with _lock:
        return _execute(self)


Query.execute = _locked_execute  # type: ignore[method-assign]
_upsert = Query.upsert
# PostgREST options the fake doesn't model (ignore_duplicates, count) change nothing a load test measures.
Query.upsert = lambda self, values, on_conflict=None, **_: _upsert(self, values, on_conflict)  # type: ignore[method-assign]


class _Bucket:
    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def upload(self, path: str, file: bytes, file_options: dict | None = None) -> dict:
        self._files[path] = bytes(file)
        return {"path": path}

    def download(self, path: str) -> bytes:
        return self._files[path]

    def remove(self, paths: list[str]) -> list[dict]:
        return [{"name": p} for p in paths if self._files.pop(p, None) is not None]

    def create_signed_url(self, path: str, expires_in: int, options: dict | None = None) -> dict:
        return {"signedURL": f"http://localhost/loadtest-files/{path}", "signedUrl": f"http://localhost/loadtest-files/{path}"}


class _Storage:
    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    def from_(self, bucket: str) -> _Bucket:
        return _Bucket(self._files)


class _Database(FakeSupabase):
    """The fake, with what the real schema adds to an inserted row: its column defaults and the case number trigger."""

    _next_case_number = 1000

    def add(self, table, values):
        row = dict(values)
        if table == "cases" and not str(row.get("greenlink_case_id") or "").strip():
            _Database._next_case_number += 1
            row["greenlink_case_id"] = f"GLDB-{_Database._next_case_number}"
        now = datetime.now(timezone.utc).isoformat()
        for column in _TIMESTAMPS.get(table, ()):
            row.setdefault(column, now)
        return super().add(table, row)


# backend/sql/schema.sql's defaults, for the tables a load test writes.
_TIMESTAMPS = {
    "cases": ("created_at", "updated_at"), "documents": ("uploaded_at",), "detection_results": ("updated_at",),
    "findings": ("created_at", "updated_at"), "case_analysis_runs": ("started_at",), "risk_scores": ("scored_at",),
    "audit_events": ("occurred_at",), "agent_versions": ("first_seen_at",),
}
database = _Database(
    keys={
        "detection_results": ("document_id",), "risk_scores": ("case_id",), "langgraph_trace": ("document_id",),
        "agent_versions": ("fingerprint",), "finding_feedback": ("source_key",),
    },
    defaults={
        "cases": {"status": "open", "recommended_additional_documents": [], "checklist_enforced": False,
                  "reviews_required": True, "risk_rank": 0},
        "documents": {"status": "uploaded", "retry_count": 0, "evidence": [], "fulfilled_requirements": []},
        "findings": {"summary": "", "evidence": {}},
        "case_analysis_runs": {"result": {}, "trace": []},
        "audit_events": {"outcome": "success", "detail": {}},
    },
)
database.storage = _Storage()  # type: ignore[attr-defined]
# The database functions the app calls: nothing to learn from and no spend to cap in a load test.
for function in ("feedback_counts", "case_cost_totals", "type_corrections", "field_edit_counts"):
    database.functions[function] = lambda params: []

import app.supabase_client as supabase_module  # noqa: E402

supabase_module.supabase_client = database  # type: ignore[assignment]
supabase_module.service_supabase_client = lambda: database  # type: ignore[assignment]


async def _no_realtime():
    raise RuntimeError("no Realtime in a load test")


supabase_module.build_async_supabase_client = _no_realtime  # type: ignore[assignment]

# Only now the rest of the app, which binds the client above as it's imported.
from app import detection_results, document_repository  # noqa: E402
from app.case_pipeline import service as analysis_service  # noqa: E402
from app.langgraph import pipeline  # noqa: E402
from app.langgraph.detection.graph import _stamped  # noqa: E402
from app.models import DocumentStatus, DocumentType, RoutingStatus  # noqa: E402
from app.workers import detection as detection_worker  # noqa: E402


def _write_reviewed_field(document_id: str) -> None:
    """One field, as an officer saved it: what an Other document needs before it can be approved."""
    database.table("extracted_fields").insert({
        "document_id": document_id, "field_name": "reference", "field_value": "load test", "confidence": 1.0,
        "field_type": "text",
    }).execute()
    database.table("documents").update({"fields_reviewed_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("id", document_id).execute()


async def _process_document(document_id: str) -> None:
    """Classify and extract: Other, accepted, one reviewed field."""
    await asyncio.sleep(DOC_SECONDS)
    await asyncio.to_thread(_write_reviewed_field, document_id)
    await asyncio.to_thread(
        document_repository.update_status, document_id, DocumentStatus.EXTRACTING,
        expected_current_statuses=[DocumentStatus.PROCESSING],
    )
    await asyncio.to_thread(
        document_repository.record_classification, document_id,
        document_type=DocumentType.UNRECOGNISED.value, classification_confidence=1.0,
        routing_status=RoutingStatus.ACCEPTED.value,
    )
    await asyncio.to_thread(
        document_repository.update_status, document_id, DocumentStatus.ANALYZED,
        expected_current_statuses=[DocumentStatus.EXTRACTING],
    )


async def _run_detection(document_id: str) -> dict:
    """A clean report, saved and marked the way the real run does it (under the run's claim)."""
    await asyncio.sleep(DETECTION_SECONDS)
    report = {"verdict": "clean", "summary": "Load test: no detection ran.", "findings": []}
    await asyncio.to_thread(detection_results.save_report, document_id, report)
    await asyncio.to_thread(document_repository.mark_detected, document_id, **_stamped())
    return report


async def _analyse(analysis, documents, reports, failed_ids, trace) -> None:
    await asyncio.sleep(ANALYSIS_SECONDS)
    analysis.status = "complete"


pipeline.process_document = _process_document  # type: ignore[assignment]
detection_worker.run_detection = _run_detection  # type: ignore[assignment]
analysis_service._analyse = _analyse  # type: ignore[assignment]


if __name__ == "__main__":
    import uvicorn

    from app.main import app

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="warning")
