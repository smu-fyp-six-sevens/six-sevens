"""
Builds the success-metric dataset: for each seed, a sample of cases with their documents, truth files and labels.

From backend/:
    python -m scripts.metrics.build_dataset --case-ids C001 C011   # unseeded (seed-0): the FYP/Drive documents
    python -m scripts.metrics.build_dataset                         # unseeded, a 10-case sample
    python -m scripts.metrics.build_dataset --seeds 3 --cases 10
    python -m scripts.metrics.build_dataset --seeds 1 --case-ids C002 C024 C001

Writes metrics-testing/seed-<n>/<case>/ (the documents, each with its <file>.truth.json) and
metrics-testing/seed-<n>/labels.json (each case's expected risk level and which documents are forged).

Nothing here calls Claude or Supabase: it only generates files. The seed does two things: it picks which cases are
sampled, and it changes every invented value in their documents (amounts, dates, account numbers), so two seeds
are two different datasets. Facts from the mastersheet (names, UENs, addresses) are the same under every seed.

The sample is stratified by expected risk level (4 High, 2 Medium, 4 Low for 10 cases); a plain random 10 of the
30 would hold about three fraud cases, too few to measure detection on.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import openpyxl

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from doc_gen_scripts.truth import TRUTH_SUFFIX  # noqa: E402

from . import ai_document, frauds  # noqa: E402

warnings.filterwarnings("ignore", message="Data Validation extension is not supported")

DATASET_ROOT = BACKEND.parent / "metrics-testing"
DEFAULT_MASTERSHEET = BACKEND / "doc_gen_scripts" / "v18_document_mastersheet.xlsx"
RISK_COLUMN = "Expected Risk Level"
LEVELS = ("Low", "Medium", "High", "Critical")
# Of every 10 sampled cases. Critical cases are sampled with the High ones (there are only three), so adding the
# level didn't change which cases each seed holds.
SAMPLE_SHARE = {"High": 4, "Medium": 2, "Low": 4}
SAMPLE_GROUP = {"Critical": "High"}
# The system rates a case Critical when high-severity findings sit on this many documents (case_risk.py).
CRITICAL_MIN_FORGED_DOCUMENTS = 3
# An edit that changes how a document looks, not what it says.
COSMETIC = {"Spelling", "Font/Size", "Metadata"}


def expected_level(case_id: str) -> str:
    """
    The risk rubric, from what is injected into the case (never from what the system says about it):
      Low     nothing is forged.
      Medium  one document carries cosmetic edits only (spelling, font, metadata) and no value is changed.
      High    a value is changed (an amount, a date, an identity or address detail), an image is manipulated or
              generated, or two or more documents are forged.
      Critical  three or more documents are forged, the corroboration the system's own Critical rule asks for.
    """
    forged = frauds.frauds_for(case_id)
    has_ai_document = case_id == ai_document.CASE_ID
    if not forged and not has_ai_document:
        return "Low"
    if len(forged) + has_ai_document >= CRITICAL_MIN_FORGED_DOCUMENTS:
        return "Critical"
    cosmetic_only = all(s.dimension in COSMETIC and s.changes is None for f in forged for s in f.steps)
    return "Medium" if len(forged) == 1 and cosmetic_only and not has_ai_document else "High"


def read_labels(mastersheet: Path) -> dict[str, dict[str, Any]]:
    """case id -> its Case Registry labels and the documents the sheet marks as forged."""
    wb = openpyxl.load_workbook(str(mastersheet), read_only=True, data_only=True)
    cases: dict[str, dict[str, Any]] = {}
    rows = wb["Case Registry"].iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]
    for row in rows:
        record = dict(zip(header, row))
        if not record.get("Case ID") or not record.get("Company Name"):
            continue
        cases[str(record["Case ID"])] = {
            "company_name": record["Company Name"],
            "fraud_case": str(record.get("Fraud Case? (Y/N)") or "").strip().upper() == "Y",
            "sheet_risk": (str(record.get(RISK_COLUMN)).strip().title() if record.get(RISK_COLUMN) else None),
            "sheet_forged_documents": [],
        }
    for sheet in wb.worksheets:
        rows = sheet.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in (next(rows, None) or [])]
        if "Filename" not in header or "Fraud Injected? (Y/N)" not in header:
            continue
        case_column = "Case ID" if "Case ID" in header else "Case ID (auto)"
        for row in rows:
            record = dict(zip(header, row))
            case_id = str(record.get(case_column) or "")
            if case_id in cases and str(record.get("Fraud Injected? (Y/N)") or "").strip().upper() == "Y":
                cases[case_id]["sheet_forged_documents"].append(str(record["Filename"]))
    wb.close()
    return cases


def sample_cases(levels: dict[str, str], seed: int, size: int) -> list[str]:
    rng = random.Random(f"metrics-sample|{seed}")
    by_level = {level: sorted(c for c, case_level in levels.items() if SAMPLE_GROUP.get(case_level, case_level) == level)
                for level in SAMPLE_SHARE}
    wanted = {level: min(len(by_level[level]), round(size * SAMPLE_SHARE[level] / 10)) for level in SAMPLE_SHARE}
    wanted["Low"] = min(len(by_level["Low"]), size - wanted["High"] - wanted["Medium"])
    picked = [case for level in ("Low", "Medium", "High") for case in rng.sample(by_level[level], wanted[level])]
    return sorted(picked)


def _generate(case_id: str, seed: int, out_dir: Path, mastersheet: Path) -> list[str]:
    """One case's clean documents and truth files, in its own process so its seed and paths can't leak."""
    env = {
        **os.environ,
        "DOCGEN_SEED": str(seed) if seed else "",  # seed 0: unseeded, the values a plain generator run prints
        "DOCGEN_TRUTH": "1",
        "DOCGEN_OUT_DIR": str(out_dir),
        "DOCGEN_MASTERSHEET": str(mastersheet),
    }
    done = subprocess.run(
        [sys.executable, "-m", "scripts.generate_from_mastersheet", case_id],
        cwd=BACKEND, env=env, capture_output=True, text=True,
    )
    # generate_for_case reports a failed document on stdout and carries on.
    problems = [f"{case_id}: {line.strip()}" for line in done.stdout.splitlines() if "failed" in line.lower()]
    if done.returncode != 0:
        problems.append(f"{case_id}: generation exited {done.returncode}: {done.stderr.strip()[-400:]}")
    return problems


def _documents(case_dir: Path) -> list[dict[str, Any]]:
    documents = []
    for record in sorted(case_dir.glob(f"*{TRUTH_SUFFIX}")):
        truth = json.loads(record.read_text())
        documents.append({
            "filename": truth["filename"],
            "files": truth["files"],
            "document_type": truth["document_type"],
            "fraud_expected": truth["fraud_expected"],
            "dimensions": sorted({t["dimension"] for t in truth["tampering"]}),
            "scored_fields": len(truth["fields"]),
        })
    return documents


def build_seed(seed: int, case_ids: list[str], labels: dict[str, dict[str, Any]], mastersheet: Path,
               root: Path, jobs: int) -> Path:
    seed_dir = root / f"seed-{seed}"
    seed_dir.mkdir(parents=True, exist_ok=True)
    # Only the cases being built are replaced, with their now out-of-date results; every other case, its results and
    # its labels stay, so building one more case never loses a run already fetched or scored.
    for case_id in case_ids:
        if (seed_dir / case_id).exists():
            shutil.rmtree(seed_dir / case_id)
        (seed_dir / "results" / f"{case_id}.json").unlink(missing_ok=True)

    print(f"Seed {seed}: generating {len(case_ids)} case(s): {', '.join(case_ids)}")
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        problems = [p for result in pool.map(lambda c: _generate(c, seed, seed_dir, mastersheet), case_ids)
                    for p in result]

    os.environ["DOCGEN_SEED"] = str(seed) if seed else ""  # the forgeries read seeded values through the same helper
    cases: dict[str, Any] = {}
    for case_id in case_ids:
        case_dir = seed_dir / case_id
        if case_id == ai_document.CASE_ID:
            problems.extend(ai_document.build(case_dir, mastersheet))
        problems.extend(frauds.apply_case(case_dir, case_id))
        documents = _documents(case_dir)
        level = expected_level(case_id)
        sheet = labels[case_id]
        if sheet["sheet_risk"] and sheet["sheet_risk"] != level:
            problems.append(f"{case_id}: the mastersheet says {sheet['sheet_risk']}, the rubric says {level}")
        forged = sorted(d["filename"] for d in documents if d["fraud_expected"])
        cases[case_id] = {
            "company_name": sheet["company_name"],
            "expected_risk": sheet["sheet_risk"] or level,
            "fraud_case": bool(forged),
            "forged_documents": forged,
            "documents": documents,
        }
        print(f"  {case_id}  {cases[case_id]['expected_risk']:<6}  {len(documents)} documents, {len(forged)} forged")

    labels_file = seed_dir / "labels.json"
    kept = json.loads(labels_file.read_text())["cases"] if labels_file.exists() else {}
    payload = {
        "seed": seed,
        "mastersheet": mastersheet.name,
        "risk_rubric": (expected_level.__doc__ or "").strip(),
        "cases": dict(sorted({**kept, **cases}.items())),
        "problems": problems,
    }
    (seed_dir / "labels.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    for problem in problems:
        print("  PROBLEM:", problem)
    return seed_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0],
                        help="0 builds the unseeded documents (the values a plain generator run prints) as seed-0")
    parser.add_argument("--cases", type=int, default=10, help="cases sampled per seed")
    parser.add_argument("--case-ids", nargs="+", help="use exactly these cases instead of sampling")
    parser.add_argument("--mastersheet", type=Path, default=DEFAULT_MASTERSHEET)
    parser.add_argument("--out", type=Path, default=DATASET_ROOT)
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    args = parser.parse_args()

    mastersheet = args.mastersheet.resolve()
    if not mastersheet.exists():
        parser.error(f"mastersheet not found: {mastersheet}")
    labels = read_labels(mastersheet)
    levels = {case_id: record["sheet_risk"] or expected_level(case_id) for case_id, record in labels.items()}
    for seed in args.seeds:
        case_ids = sorted(args.case_ids) if args.case_ids else sample_cases(levels, seed, args.cases)
        unknown = [c for c in case_ids if c not in labels]
        if unknown:
            parser.error(f"not in the mastersheet: {', '.join(unknown)}")
        build_seed(seed, case_ids, labels, mastersheet, args.out.resolve(), args.jobs)


if __name__ == "__main__":
    main()
