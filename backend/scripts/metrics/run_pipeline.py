"""
Puts a built dataset through the running system and saves what it produced, for scripts/metrics/score.py.

This is the step that costs money and writes to Supabase: every document is classified, extracted and scanned by
the real agents, and every case is analysed. Run it yourself, against an API you choose.

From backend/:
    METRICS_PASSWORD=... python -m scripts.metrics.run_pipeline --seed 1 --username alice
    METRICS_PASSWORD=... python -m scripts.metrics.run_pipeline --seed 1 --username alice --case-ids C002
    ... --api https://your-gateway.example            (default http://localhost:8000)

For each case in metrics-testing/seed-<n>/labels.json it:
  1. creates a case under the company's own name (a changed name would trip the name-consistency checks);
  2. uploads the case's documents and waits for classification and extraction;
  3. saves each document's type and extracted fields as extracted, before anything is approved;
  4. does what an officer does to make a document approvable, accepting the agents' answers as they are:
     confirms the type the classifier gave, and saves the fields unchanged where some were low-confidence
     (the extraction score is taken in step 3, before this, so it measures the agents alone);
  5. approves every document, which starts detection, and waits for the reports;
  6. runs the case analysis and waits for the risk level;
  7. writes metrics-testing/seed-<n>/results/<case>.json.

A case with a results file is skipped, so a run that stops half way can be started again; --force redoes it.
Nothing is deleted from the system afterwards: results/<case>.json holds each case's id for cleaning up.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[2]
# METRICS_USERNAME / METRICS_PASSWORD / METRICS_API may sit in backend/.env beside the app's own settings.
load_dotenv(BACKEND / ".env")
DATASET_ROOT = BACKEND.parent / "metrics-testing"
TRUTH_SUFFIX = ".truth.json"
IN_FLIGHT = {"uploaded", "queued", "processing", "extracting"}
SCANNING = {"approved", "detecting"}
UPLOAD_BATCH = 20  # settings.MAX_BULK_FILES


class Stalled(RuntimeError):
    pass


def _wait(what: str, done, *, timeout: float, every: float = 5.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        result = done()
        if result is not None:
            return result
        if time.monotonic() > deadline:
            raise Stalled(f"timed out after {timeout:.0f}s waiting for {what}")
        time.sleep(every)


def _check(response: httpx.Response, what: str) -> Any:
    if response.status_code >= 400:
        raise RuntimeError(f"{what} failed: {response.status_code} {response.text[:300]}")
    return response.json() if response.content else None


def _documents(client: httpx.Client, case_id: str) -> list[dict[str, Any]]:
    return _check(client.get("/documents", params={"case_id": case_id}), "listing the case's documents")


def _plain_fields(fields: dict[str, Any] | None) -> dict[str, Any]:
    """GET /documents/{id} wraps each field as {value, confidence}; the scorer wants the values."""
    return {
        name: (value["value"] if isinstance(value, dict) and "value" in value else value)
        for name, value in (fields or {}).items()
    }


def _accept_as_extracted(client: httpx.Client, record: dict[str, Any], reason: str, notes: list[str]) -> list[str]:
    """
    Clears the gate the system refused approval for (document_service.approval_refusal), as an officer accepting the
    agents' work would: confirms the classifier's own type, or saves the fields exactly as extracted. Nothing is
    corrected.
    """
    steps: list[str] = []
    document = _check(client.get(f"/documents/{record['id']}"), "reading a document")
    if "Confirm the document type" in reason and document.get("document_type"):
        response = client.patch(f"/documents/{record['id']}/document-type",
                                json={"document_type": document["document_type"]})
        if response.status_code >= 400:
            notes.append(f"type not confirmed: {record['filename']}: {response.status_code} {response.text[:150]}")
            return steps
        steps.append("confirmed the classifier's type")
        # Confirming can send the document back through extraction; save fields only once it has settled.
        document = _wait(
            "a document to settle after its type was confirmed",
            lambda: (lambda d: d if d["status"] not in IN_FLIGHT else None)(
                _check(client.get(f"/documents/{record['id']}"), "reading a document")),
            timeout=600,
        )
    if "Low-confidence fields" in reason or "Confirm the document type" in reason:
        fields = _plain_fields(document.get("extracted_fields"))
        unsure = [n for n, v in (document.get("extracted_fields") or {}).items()
                  if isinstance(v, dict) and v.get("confidence") is not None and v["confidence"] < 0.9]
        if fields and unsure:
            response = client.patch(f"/documents/{record['id']}/extracted-fields", json={"extracted_fields": fields})
            if response.status_code >= 400:
                notes.append(f"fields not saved: {record['filename']}: {response.status_code} {response.text[:150]}")
                return steps
            steps.append(f"saved the fields unchanged ({', '.join(unsure)} low-confidence)")
    return steps


def run_case(client: httpx.Client, case_dir: Path, label: dict[str, Any], timeouts: argparse.Namespace) -> dict[str, Any]:
    notes: list[str] = []
    case = _check(client.post("/cases", json={"company_name": label["company_name"], "reviews_required": False}),
                  "creating the case")
    case_id = case["id"]
    print(f"  case {case.get('greenlink_case_id') or case_id}")

    files = sorted(p for p in case_dir.iterdir() if p.is_file() and not p.name.endswith(TRUTH_SUFFIX)
                   and not p.name.startswith("."))
    for start in range(0, len(files), UPLOAD_BATCH):
        batch = files[start:start + UPLOAD_BATCH]
        handles = [open(p, "rb") for p in batch]
        try:
            uploaded = _check(client.post(
                "/documents/upload-batch", data={"case_id": case_id},
                files=[("files", (p.name, h, mimetypes.guess_type(p.name)[0] or "application/octet-stream"))
                       for p, h in zip(batch, handles)],
            ), "uploading the documents")
        finally:
            for handle in handles:
                handle.close()
        for item in uploaded.get("results") or uploaded.get("items") or []:
            if not item.get("success"):
                notes.append(f"upload refused: {item.get('filename')}: {item.get('error')}")

    def extracted() -> list[dict[str, Any]] | None:
        documents = _documents(client, case_id)
        return documents if documents and not any(d["status"] in IN_FLIGHT for d in documents) else None

    _wait("classification and extraction", extracted, timeout=timeouts.extraction)

    # Extraction as the agents left it: read before approval, so nothing an officer step changes is scored.
    records: dict[str, dict[str, Any]] = {}
    for document in _documents(client, case_id):
        full = _check(client.get(f"/documents/{document['id']}"), "reading a document")
        records[full["id"]] = {
            "id": full["id"],
            "filename": full["filename"],
            "document_type": full.get("document_type"),
            "status_after_extraction": full["status"],
            "classification_confidence": full.get("classification_confidence"),
            "extraction_confidence": full.get("extraction_confidence"),
            "missing_required_fields": full.get("missing_required_fields") or [],
            "failure_reason": full.get("failure_reason"),
            "extracted_fields": _plain_fields(full.get("extracted_fields")),
            "detection_report": None,
        }

    approvable = [r["id"] for r in records.values() if r["status_after_extraction"] == "analyzed"]
    attempts = 3
    for attempt in range(attempts):
        if not approvable:
            break
        approved = _check(client.post("/documents/bulk-approve", json={"document_ids": approvable}),
                          "approving the documents")
        approvable = []
        for failure in approved.get("failed") or []:
            record = records[failure["document_id"]]
            if attempt < attempts - 1:
                record["officer_steps"] = [*record.get("officer_steps", []),
                                           *_accept_as_extracted(client, record, failure["reason"], notes)]
                approvable.append(record["id"])
            else:
                notes.append(f"not approved: {record['filename']}: {failure['reason']}")
        if approvable:
            # Confirming a type can send a document back through processing; wait for it to settle.
            pending = set(approvable)
            _wait("documents to settle after the officer steps",
                  lambda: True if not any(d["status"] in IN_FLIGHT for d in _documents(client, case_id)
                                          if d["id"] in pending) else None,
                  timeout=timeouts.extraction)
    for record in records.values():
        if record["status_after_extraction"] != "analyzed":
            notes.append(f"not approved: {record['filename']}: left extraction as {record['status_after_extraction']}")
        if record.get("officer_steps"):
            notes.append(f"officer step on {record['filename']}: {'; '.join(record['officer_steps'])}")

    def scanned() -> list[dict[str, Any]] | None:
        documents = _documents(client, case_id)
        return documents if not any(d["status"] in SCANNING for d in documents) else None

    for document in _wait("detection", scanned, timeout=timeouts.detection):
        record = records.get(document["id"])
        if record is None:
            continue
        record["status"] = document["status"]
        if document["status"] == "detected":
            response = client.get(f"/documents/{document['id']}/detection/report")
            if response.status_code == 200:
                record["detection_report"] = response.json()
            else:
                notes.append(f"no detection report: {record['filename']}: {response.status_code}")
        else:
            notes.append(f"not scanned: {record['filename']}: status {document['status']}")

    analysis: dict[str, Any] | None = None
    started = client.post(f"/cases/{case_id}/analysis/run")
    if started.status_code not in (202, 409):  # 409: one is already running, which is fine
        notes.append(f"analysis not started: {started.status_code} {started.text[:200]}")
    else:
        if started.status_code == 409:
            notes.append(f"analysis start answered 409: {started.text[:200]}")

        def analysed() -> dict[str, Any] | None:
            response = client.get(f"/cases/{case_id}/analysis")
            if response.status_code != 200:
                return None
            body = response.json()
            return body if body.get("status") in ("complete", "failed") and not body.get("stale") else None

        try:
            analysis = _wait("the case analysis", analysed, timeout=timeouts.analysis)
        except Stalled as exc:
            notes.append(str(exc))

    risk = (analysis or {}).get("risk") or {}
    return {
        "api_case_id": case_id,
        "greenlink_case_id": case.get("greenlink_case_id"),
        "documents": list(records.values()),
        "analysis": None if analysis is None else {
            "run_id": analysis.get("run_id"),
            "status": analysis.get("status"),
            "error": analysis.get("error"),
            "risk": {
                "state": risk.get("state"),
                "level": risk.get("level"),
                "score": risk.get("score"),
                "points": risk.get("points"),
                "reasons": risk.get("reasons"),
                "contributing_findings": risk.get("contributing_findings") or [],
            },
            "cross_document_findings": analysis.get("findings") or [],
        },
        "notes": notes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--api", default=os.environ.get("METRICS_API", "http://localhost:8000"))
    parser.add_argument("--username", default=os.environ.get("METRICS_USERNAME"))
    parser.add_argument("--case-ids", nargs="+", help="only these cases from the seed's sample")
    parser.add_argument("--dataset", type=Path, default=DATASET_ROOT)
    parser.add_argument("--force", action="store_true", help="run a case again even if it has a results file")
    parser.add_argument("--extraction", type=float, default=1800, help="seconds to wait for extraction, per case")
    parser.add_argument("--detection", type=float, default=3600, help="seconds to wait for detection, per case")
    parser.add_argument("--analysis", type=float, default=1800, help="seconds to wait for the case analysis")
    args = parser.parse_args()

    password = os.environ.get("METRICS_PASSWORD")
    if not args.username or not password:
        parser.error("give --username (or METRICS_USERNAME) and set METRICS_PASSWORD")

    seed_dir = args.dataset / f"seed-{args.seed}"
    labels = json.loads((seed_dir / "labels.json").read_text())["cases"]
    results_dir = seed_dir / "results"
    results_dir.mkdir(exist_ok=True)
    case_ids = args.case_ids or sorted(labels)

    with httpx.Client(base_url=args.api.rstrip("/"), timeout=120) as client:
        token = _check(client.post("/auth/login", json={"username": args.username, "password": password}),
                       "signing in")["token"]
        # The session cookie is the only credential; writes with it need the CSRF header (app/auth.py).
        client.headers.update({"Cookie": f"six_session={token}", "X-Requested-With": "metrics"})
        failures = []
        for case_id in case_ids:
            if case_id not in labels:
                print(f"{case_id}: not in seed {args.seed}'s sample, skipped")
                continue
            out = results_dir / f"{case_id}.json"
            if out.exists() and not args.force:
                print(f"{case_id}: already has results, skipped (use --force to run it again)")
                continue
            print(f"{case_id}: {labels[case_id]['company_name']}")
            try:
                result = run_case(client, seed_dir / case_id, labels[case_id], args)
            except Exception as exc:  # one case stopping must not lose the others
                print(f"  FAILED: {exc}")
                failures.append(case_id)
                continue
            out.write_text(json.dumps({"case_id": case_id, "seed": args.seed, **result}, indent=2, default=str) + "\n")
            level = ((result["analysis"] or {}).get("risk") or {}).get("level")
            print(f"  done: risk {level or 'not scored'}, {len(result['notes'])} note(s)")
            for note in result["notes"]:
                print("    -", note)
    if failures:
        print(f"\nDid not finish: {', '.join(failures)}. Run again to retry them.")
        sys.exit(1)


if __name__ == "__main__":
    main()
