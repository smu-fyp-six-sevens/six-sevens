"""
Saves one real case, exactly as the API returns it, as a JSON fixture for
the regression suites (SCRUM-206) — so the tests keep working after the
shared Supabase is reset.

Read-only: it only calls GET endpoints (through FastAPI's TestClient, so the
responses are byte-for-byte what the frontend receives) plus the case
WebSocket's snapshot builder. Nothing is written to Supabase or storage.

    cd backend
    python scripts/capture_regression_fixture.py GLDB-02 anchorpoint

Writes tests/fixtures/regression/<name>.json containing:
  case        GET /cases/{id}                  (case summary + checklist)
  snapshot    what /ws/cases/{id} pushes       (case, documents, case_match)
  documents   GET /documents/{id} for each document, keyed by id
  rows        the plain documents rows          (for backend mapping tests)

Storage paths and file metadata are blanked: they point at real storage
objects / carry local machine details, and no test needs them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import case_repository, case_status, document_repository  # noqa: E402
from app.auth import require_auth  # noqa: E402
from app.main import app  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "regression"


def _scrub(document: dict) -> dict:
    document = dict(document)
    if document.get("storage_path"):
        document["storage_path"] = f"regression-fixture/{document['filename']}"
    document["metadata"] = None
    document["metadata_anomalies"] = None
    return document


def capture(greenlink_case_id: str, name: str) -> Path:
    matches = [c for c in case_repository.list_cases() if c.greenlink_case_id == greenlink_case_id]
    if not matches:
        raise SystemExit(f"No case with greenlink_case_id {greenlink_case_id}")
    case_id = matches[0].id

    app.dependency_overrides[require_auth] = lambda: None
    with TestClient(app) as client:
        case = client.get(f"/cases/{case_id}")
        case.raise_for_status()
        rows = document_repository.list_documents(case_id=case_id)
        documents = {}
        for row in rows:
            response = client.get(f"/documents/{row.id}")
            response.raise_for_status()
            documents[row.id] = _scrub(response.json())
    app.dependency_overrides.pop(require_auth, None)

    snapshot = case_status.build_case_snapshot(case_id)
    snapshot["documents"] = [_scrub(d) for d in snapshot["documents"]]

    fixture = {
        "source": f"Supabase case {greenlink_case_id}, captured by scripts/capture_regression_fixture.py",
        "case": case.json(),
        "snapshot": snapshot,
        "documents": documents,
        "rows": [_scrub(row.model_dump(mode="json")) for row in rows],
    }

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / f"{name}.json"
    path.write_text(json.dumps(fixture, indent=2, sort_keys=True, default=str) + "\n")
    return path


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(capture(sys.argv[1], sys.argv[2]))
