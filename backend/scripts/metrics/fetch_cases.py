"""
Fetches cases someone uploaded through the app and saves them for scoring, as if run_pipeline had run them.

From backend/:
    python -m scripts.metrics.fetch_cases GLDB-6 GLDB-7 GLDB-8
    python -m scripts.metrics.fetch_cases GLDB-6 --api https://your-gateway.example     (default http://localhost:8000)

Then score as usual: python -m scripts.metrics.score

Signs in with METRICS_USERNAME / METRICS_PASSWORD (backend/.env or the environment). Read-only: it only reads cases,
documents, detection reports, the case analysis and the audit trail.

For each case it:
  1. works out which mastersheet case it is from its documents' filenames (no naming rule for the case itself);
  2. checks each uploaded file prints exactly what the dataset's file prints (metrics-testing/seed-0): the same PDF
     text, or the same image pixels. Bytes aren't compared, since every PDF carries the time it was generated. One
     that differs (another build, or the clean copy of a document the case is meant to have forged) is left out of
     the scores and listed;
  3. undoes what an officer changed, using the audit trail, so the agents' own work is scored:
     - a document's type is the classifier's, from before any officer correction;
     - a field the officer edited gets its value from before the edit; a field the officer added is dropped; a list
       field the officer edited (only its added and removed rows are recorded) is left out of the extraction score;
  4. takes the newest copy of a file uploaded more than once, and the case's latest analysis;
  5. writes metrics-testing/seed-0/results/<case>.json.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pymupdf
from dotenv import load_dotenv
from PIL import Image, ImageChops

BACKEND = Path(__file__).resolve().parents[2]
DATASET = BACKEND.parent / "metrics-testing" / "seed-0"
load_dotenv(BACKEND / ".env")

TRUTH_SUFFIX = ".truth.json"
_TYPE_EVENTS = ("document.type_corrected", "document.retyped")
_FIELD_EVENT = "document.field_edited"


def _check(response: httpx.Response, what: str) -> Any:
    if response.status_code >= 400:
        raise RuntimeError(f"{what} failed: {response.status_code} {response.text[:300]}")
    return response.json() if response.content else None


def _plain(fields: dict[str, Any] | None) -> dict[str, Any]:
    return {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in (fields or {}).items()}


def _dataset_files(labels: dict[str, Any]) -> dict[str, list[str]]:
    """filename -> the mastersheet cases holding a file of that name."""
    owners: dict[str, list[str]] = {}
    for case_id, case in labels.items():
        for document in case["documents"]:
            for name in document["files"]:
                owners.setdefault(name, []).append(case_id)
        for path in (DATASET / case_id).iterdir():
            if path.is_file() and not path.name.endswith(TRUTH_SUFFIX) and not path.name.startswith("."):
                owners.setdefault(path.name, [])
                if case_id not in owners[path.name]:
                    owners[path.name].append(case_id)
    return owners


def _which_case(filenames: list[str], owners: dict[str, list[str]]) -> tuple[str | None, int]:
    votes = Counter(case for name in filenames for case in owners.get(name, []))
    if not votes:
        return None, 0
    case_id, count = votes.most_common(1)[0]
    return case_id, count


def _same_document(uploaded: bytes, expected: Path) -> bool:
    """Whether the uploaded file prints what the dataset's file prints."""
    data = expected.read_bytes()
    if hashlib.sha256(uploaded).digest() == hashlib.sha256(data).digest():
        return True
    if data.startswith(b"%PDF"):
        if not uploaded.startswith(b"%PDF"):
            return False

        def text(blob: bytes) -> str:
            with pymupdf.open(stream=blob, filetype="pdf") as doc:
                return "\n".join(page.get_text() for page in doc)

        return text(uploaded) == text(data)
    try:
        with Image.open(io.BytesIO(uploaded)) as a, Image.open(expected) as b:
            return a.size == b.size and ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None
    except Exception:
        return False


def _events(client: httpx.Client, document_id: str) -> list[dict[str, Any]]:
    """The document's audit trail, oldest first."""
    events = _check(client.get("/audit/events", params={"document_id": document_id, "limit": 1000}), "reading the audit trail")
    return sorted(events or [], key=lambda e: e.get("occurred_at") or "")


def _undo_officer(fields: dict[str, Any], current_type: str | None, events: list[dict[str, Any]]):
    """(the agents' type, fields as the agents left them, list fields the officer edited, what the officer did)."""
    original_type = current_type
    type_changes = [e for e in events if e.get("type") in _TYPE_EVENTS]
    if type_changes:
        original_type = (type_changes[0].get("detail") or {}).get("from_type") or current_type
    fields = dict(fields)
    edited_lists: set[str] = set()
    actions = [f"type {e['detail'].get('from_type')} -> {e['detail'].get('to_type')}" for e in type_changes]
    # Newest edit first, so each field ends up with the value from before the first edit.
    for event in reversed([e for e in events if e.get("type") == _FIELD_EVENT]):
        detail = event.get("detail") or {}
        for change in detail.get("changes") or []:
            name = change.get("field")
            if not name:
                continue
            if "added" in change or "removed" in change:  # a list field: only the changed rows are recorded
                edited_lists.add(name)
                actions.append(f"edited rows of {name}")
            elif change.get("from") is None:
                fields.pop(name, None)
                actions.append(f"added {name}")
            else:
                fields[name] = change["from"]
                actions.append(f"{'deleted' if change.get('to') is None else 'edited'} {name}")
    # A type change re-runs extraction: the fields came from the agents under the corrected type, edits aside.
    return original_type, fields, sorted(edited_lists), actions


def _at(event: dict[str, Any], key: str = "occurred_at") -> datetime:
    return datetime.fromisoformat(event[key])


def _processing_time(client: httpx.Client, case_id: str, run_id: str | None) -> dict[str, Any] | None:
    """
    How long the system worked on the case, from its audit trail: each document's upload until it was extracted,
    its approval until detection finished, and the analysis run that is scored, overlaps counted once. The time an
    officer took to review and approve sits between these and isn't counted. None when the case has no analysis.
    """
    events = sorted(_check(client.get("/audit/events", params={"case_id": case_id, "limit": 1000}),
                           "reading the case's audit trail"), key=lambda e: e["occurred_at"])
    by_document: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("document_id"):
            by_document.setdefault(event["document_id"], []).append(event)
    spans = []
    for trail in by_document.values():
        for start_type, end_type in (("document.uploaded", "document.analyzed"), ("document.approved", "document.detected")):
            start = next((e for e in trail if e["type"] == start_type), None)
            end = start and next((e for e in trail if e["type"] == end_type and e["occurred_at"] >= start["occurred_at"]), None)
            if start and end:
                spans.append((_at(start), _at(end)))
    runs = [e for e in events if e["type"] == "case.analysis.completed" and e.get("outcome") == "success"]
    run = next((e for e in reversed(runs) if (e.get("detail") or {}).get("run_id") == run_id), runs[-1] if runs else None)
    if run is None:
        return None
    analysis = (_at(run, "started_at") if run.get("started_at") else _at(run), _at(run))
    spans.append(analysis)
    busy, end = 0.0, None
    for start, finish in sorted(spans):
        if end is None or start > end:
            busy += (finish - start).total_seconds()
            end = finish
        elif finish > end:
            busy += (finish - end).total_seconds()
            end = finish
    return {
        "busy_seconds": round(busy, 1),
        "wall_clock_seconds": round((analysis[1] - min(s for s, _ in spans)).total_seconds(), 1),
        "analysis_seconds": round((analysis[1] - analysis[0]).total_seconds(), 1),
    }


def fetch_case(client: httpx.Client, number: str, labels: dict[str, Any], owners: dict[str, list[str]]) -> tuple[str, dict]:
    item = _check(client.get(f"/cases/by-number/{number}"), f"looking up {number}")
    api_case_id = item["id"]
    documents = _check(client.get("/documents", params={"case_id": api_case_id}), "listing the case's documents")
    case_id, votes = _which_case([d["filename"] for d in documents], owners)
    if case_id is None:
        raise RuntimeError(f"{number}: none of its {len(documents)} files is in the dataset")
    notes: list[str] = []
    if votes < len(documents):
        strays = [d["filename"] for d in documents if case_id not in owners.get(d["filename"], [])]
        notes.append(f"files that aren't {case_id}'s, left out: {', '.join(strays)}")

    # The newest copy of each filename: a re-upload replaces the copy before it.
    newest: dict[str, dict[str, Any]] = {}
    for document in sorted(documents, key=lambda d: d.get("uploaded_at") or ""):
        if case_id not in owners.get(document["filename"], []):
            continue
        if document["filename"] in newest:
            notes.append(f"{document['filename']} uploaded more than once: the newest copy is scored")
        newest[document["filename"]] = document

    records, excluded = [], []
    for name, document in sorted(newest.items()):
        full = _check(client.get(f"/documents/{document['id']}"), "reading a document")
        expected = DATASET / case_id / name
        signed = _check(client.get(f"/documents/{document['id']}/download"), "getting a download link")
        download = httpx.get(signed["signed_url"], timeout=120)
        if download.status_code != 200:
            # Not a different document: the file couldn't be read (an expired link, storage down). Comparing the
            # error body with the PDF would leave a good document out of the scores without saying why.
            raise RuntimeError(f"couldn't download {name} (HTTP {download.status_code}: {download.text[:150]}); "
                               "restart the API if the link had expired, then fetch again")
        uploaded = download.content
        if not expected.exists() or not _same_document(uploaded, expected):
            notes.append(f"{name} doesn't print what the dataset's file prints (another build, or the clean copy of a "
                         "forged document): left out of the scores")
            excluded.append(name)
            continue
        events = _events(client, document["id"])
        original_type, fields, edited_lists, actions = _undo_officer(
            _plain(full.get("extracted_fields")), full.get("document_type"), events)
        report = None
        if full["status"] == "detected":
            response = client.get(f"/documents/{document['id']}/detection/report")
            report = response.json() if response.status_code == 200 else None
        else:
            notes.append(f"not scanned: {name}: status {full['status']}")
        if actions:
            notes.append(f"officer step on {name}: {'; '.join(actions)} (undone for scoring)")
        records.append({
            "id": full["id"], "filename": name,
            "document_type": original_type,
            "document_type_after_officer": full.get("document_type"),
            "status": full["status"],
            "classification_confidence": full.get("classification_confidence"),
            "extraction_confidence": full.get("extraction_confidence"),
            "missing_required_fields": full.get("missing_required_fields") or [],
            "failure_reason": full.get("failure_reason"),
            "extracted_fields": fields,
            "officer_edited_fields": edited_lists,
            "detection_report": report,
        })

    analysis = None
    response = client.get(f"/cases/{api_case_id}/analysis")
    if response.status_code == 200:
        body = response.json()
        risk = body.get("risk") or {}
        if body.get("stale"):
            notes.append("the case analysis is out of date: documents changed after it ran; run it again for a current level")
        analysis = {
            "run_id": body.get("run_id"), "status": body.get("status"), "error": body.get("error"),
            "risk": {**{k: risk.get(k) for k in ("state", "level", "score", "points", "reasons")},
                     "contributing_findings": risk.get("contributing_findings") or []},
            "cross_document_findings": body.get("findings") or [],
        }
    else:
        notes.append("the case hasn't been analysed, so it has no risk level")

    return case_id, {
        "case_id": case_id, "seed": 0, "source": f"fetched from {number}",
        "api_case_id": api_case_id, "greenlink_case_id": item.get("greenlink_case_id") or number,
        "documents": records, "excluded_documents": excluded, "analysis": analysis, "notes": notes,
        "processing_time": _processing_time(client, api_case_id, (analysis or {}).get("run_id")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cases", nargs="+", help="case numbers as the app shows them, e.g. GLDB-6")
    parser.add_argument("--api", default=os.environ.get("METRICS_API", "http://localhost:8000"))
    parser.add_argument("--reset", action="store_true",
                        help="delete every earlier result first, so only the cases fetched now are scored")
    args = parser.parse_args()
    username, password = os.environ.get("METRICS_USERNAME"), os.environ.get("METRICS_PASSWORD")
    if not username or not password:
        parser.error("set METRICS_USERNAME and METRICS_PASSWORD (backend/.env works)")

    labels = json.loads((DATASET / "labels.json").read_text())["cases"]
    owners = _dataset_files(labels)
    results_dir = DATASET / "results"
    if args.reset and results_dir.exists():
        cleared = sorted(p.stem for p in results_dir.glob("*.json"))
        for path in results_dir.glob("*.json"):
            path.unlink()
        print(f"Cleared earlier results: {', '.join(cleared) or 'none'}")
    results_dir.mkdir(exist_ok=True)
    failures = []
    with httpx.Client(base_url=args.api.rstrip("/"), timeout=120) as client:
        token = _check(client.post("/auth/login", json={"username": username, "password": password}), "signing in")
        # The session cookie is the only credential (app/auth.py).
        client.headers.update({"Cookie": f"six_session={token['token']}", "X-Requested-With": "metrics"})
        for number in args.cases:
            try:
                case_id, result = fetch_case(client, number, labels, owners)
            except Exception as exc:
                print(f"{number}: FAILED: {exc}")
                failures.append(number)
                continue
            out = DATASET / "results" / f"{case_id}.json"
            if out.exists():
                print(f"{number}: replacing the earlier results for {case_id}")
            out.write_text(json.dumps(result, indent=2, default=str) + "\n")
            level = ((result["analysis"] or {}).get("risk") or {}).get("level")
            print(f"{number} -> {case_id}: {len(result['documents'])} documents, risk {level or 'none'}, "
                  f"{len(result['notes'])} note(s)")
            for note in result["notes"]:
                print("   -", note)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
