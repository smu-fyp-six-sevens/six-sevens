"""
Scores the success metrics from a dataset's truth files and the pipeline's saved results.

From backend/:
    python -m scripts.metrics.score                 # every seed that has results
    python -m scripts.metrics.score --seeds 0

Reads metrics-testing/seed-<n>/ (labels.json, each document's .truth.json, results/<case>.json) and writes
metrics-testing/metrics_report.json and metrics_report.md. Offline: no Claude, no Supabase, free to re-run.

Classification accuracy
    Documents given the right type / documents uploaded. The right type is the one its generator made (a payslip
    is a payslip), or a type the truth file also accepts (a personal bank statement filed as proof of address may
    be called a bank statement). A document split into several files (an NRIC's front and back, a multi-page
    image) is right only if every file is. The type is the classifier's own, read before any officer step.

Extraction accuracy
    Correct fields / truth fields. A truth field is a value the generator printed on the document
    (doc_gen_scripts/truth.py); a field the extractor left out is wrong. On a forged document the truth is the
    forged value, since that is what is printed. Three readings are reported:
      key fields   the fields the extraction schema requires for the type, right value under the right name.
                   This is the headline.
      all fields   every truth field, right value under the right name.
      any name     every truth field, right value under any field name (the extractor read it, but called it
                   something the schema doesn't).
    A table (transactions, shareholders) is one field, correct only when every row is found; its row-level rate
    is reported beside it.

Risk score accuracy
    Cases whose risk level equals the expected level (Low, Medium, High or Critical) / cases. A case with no risk
    level is wrong.

Anomaly detection accuracy
    Forged documents flagged / forged documents. Flagged: detection kept at least one finding above "info" on
    it, or a cross-document finding of the case analysis cites it. Stricter readings are reported beside it
    (the right check fired; the finding names the changed value), and the share of clean documents flagged,
    without which flagging everything would score 100%.

False positive rate
    Clean documents flagged / clean documents, from each document's own scan (a cross-document finding cites the
    genuine documents of a mismatch too, so it doesn't count against them). Beside it, clean (Low) cases the
    system rated above Low. Every false positive is listed with the check and finding that raised it.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.fraud_detection.dates.date_parsing import parse_date, parse_date_parts  # noqa: E402

DATASET_ROOT = BACKEND.parent / "metrics-testing"
TRUTH_SUFFIX = ".truth.json"
LEVELS = ("Low", "Medium", "High", "Critical")

# Beside the schema's required tier: the fields a type is read for when the schema requires none or few.
KEY_EXTRA: dict[str, set[str]] = {
    "proof_of_address": {"company_name", "company_address", "individual_name", "individual_address"},
    "bank_statement": {"company_name", "individual_name"},
    "nric": {"individual_name", "date_of_birth", "individual_address", "date_of_issue"},
    "source_of_wealth": {"account_holder_name"},
}
# Compared as identifiers: letters and digits only.
_ID_FIELD = re.compile(r"(_number|_no|^uen|^tin|^mrz|identification_no|tax_reference_number)$")
# An address or a transaction's description is accepted with a part left off (no "Singapore", or the end of a
# description the statement wrapped onto another line), as long as most of it is there.
_ADDRESS = re.compile(r"address|description")
# A label is accepted inside a longer wording of it ("84 months" in "84 months (7 years)").
_LABEL = re.compile(r"(company_type|nationality|facility_type|loan_tenure|incorporating_act|share_class|country|"
                    r"place_of_birth|position|race|bank_name)")
_FILLER = {"pte", "ltd", "limited", "private", "the"}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------
# Comparing one value
# --------------------------------------------------------------------------

def _unwrap(value: Any) -> Any:
    """An extracted value as stored: possibly {value, confidence}, possibly JSON in a string."""
    if isinstance(value, dict) and "value" in value and ("confidence" in value or "id" in value):
        value = value["value"]
    if isinstance(value, str) and value.strip()[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        negative = bool(re.search(r"\(\s*[^\d)]*\d|-\s*[^\d\s]*\s*\d|\d\s*-\s*$", text)) or "DR" in text.upper().split()
        digits = re.sub(r"[^\d.]", "", text)
        if digits.count(".") > 1 or not re.search(r"\d", digits):
            return None
        try:
            return -float(digits) if negative else float(digits)
        except ValueError:
            return None
    return None


def _tokens(value: Any) -> list[str]:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.findall(r"[a-z0-9]+", text)


def _as_date(value: Any) -> date | None:
    if isinstance(value, str) and _ISO_DATE.match(value.strip()):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return parse_date(value) if isinstance(value, str) else None


def same(name: str, truth: Any, found: Any, *, signed: bool = False) -> bool:
    """Whether an extracted value states the truth value. `name` is the field (or column) it sits under."""
    found = _unwrap(found)
    if found is None or found == "":
        return False
    if isinstance(truth, bool):
        return str(found).strip().lower() in ({"true", "yes", "y"} if truth else {"false", "no", "n"})
    if isinstance(truth, (int, float)):
        number = _number(found)
        if number is None:
            return False
        return abs((number if signed else abs(number)) - (truth if signed else abs(truth))) < 0.006
    text = str(truth).strip()
    if _ISO_DATE.match(text):
        wanted = date.fromisoformat(text)
        got = _as_date(found)
        if got is not None:
            return got == wanted
        # A financial year end is often printed without its year ("31/12").
        parts = parse_date_parts(found) if isinstance(found, str) else None
        return bool(name == "financial_year_end" and parts and parts.day == wanted.day and parts.month == wanted.month)
    if isinstance(found, (list, dict)):
        return any(same(name, truth, item) for item in (found.values() if isinstance(found, dict) else found))
    if name == "sex":
        return str(found).strip()[:1].casefold() == text[:1].casefold()
    if _ID_FIELD.search(name) or name == "year_of_assessment":
        if name == "year_of_assessment":
            return re.sub(r"\D", "", str(found)) == re.sub(r"\D", "", text)
        return "".join(_tokens(found)) == "".join(_tokens(text))
    wanted_tokens, got_tokens = _tokens(text), _tokens(found)
    if wanted_tokens == got_tokens or set(wanted_tokens) == set(got_tokens):
        return True
    if "".join(wanted_tokens) == "".join(got_tokens):
        return True
    a, b = set(wanted_tokens) - _FILLER, set(got_tokens) - _FILLER
    if not a or not b:
        return False
    if a == b:
        return True  # a name with or without "Pte Ltd" punctuation, in any order; a missing word is a miss
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    if _ADDRESS.search(name):
        return small <= large and len(small) / len(large) >= 0.6
    if _LABEL.search(name):
        if a <= b:
            return True
        # "Singaporean" and "Singapore Citizen" are one nationality.
        return name in ("nationality", "country") and any(
            x[:6] == y[:6] for x in a for y in b if len(x) >= 6 and len(y) >= 6)
    return False


def _row_found(name: str, row: Any, candidates: list[Any]) -> bool:
    """A truth row is found when one extracted row states every one of its values, under any column name."""
    if not isinstance(row, dict):
        return any(same(name, row, c) for c in candidates)
    for candidate in candidates:
        cells = list(candidate.values()) if isinstance(candidate, dict) else [candidate]
        if all(
            any(same(column, value, cell, signed=(column == "amount")) for cell in cells)
            for column, value in row.items()
        ):
            return True
    return False


def compare(name: str, truth: Any, found: Any) -> tuple[bool, int, int]:
    """(correct, rows found, rows in truth). Rows are 0 for a field that isn't a table."""
    if isinstance(truth, list):
        found = _unwrap(found)
        if not truth:
            return (isinstance(found, list) and not found), 0, 0
        candidates = found if isinstance(found, list) else []
        hits = sum(1 for row in truth if _row_found(name, row, candidates))
        return hits == len(truth), hits, len(truth)
    return same(name, truth, found), 0, 0


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _extraction_schemas() -> dict[str, dict[str, list[str]]]:
    """
    EXTRACTION_SCHEMAS, read from the source of app/langgraph/extraction_prompts.py and not imported: importing it
    loads the app's settings, and the scorer has to run with no .env at all.
    """
    source = (BACKEND / "app" / "langgraph" / "extraction_prompts.py").read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "EXTRACTION_SCHEMAS":
            return ast.literal_eval(node.value)
    raise RuntimeError("EXTRACTION_SCHEMAS not found in app/langgraph/extraction_prompts.py")


EXTRACTION_SCHEMAS = _extraction_schemas()


def _key_fields(document_type: str) -> set[str]:
    schema = EXTRACTION_SCHEMAS.get(document_type, {})
    return {*schema.get("required", []), *schema.get("required_when_individual", []), *KEY_EXTRA.get(document_type, set())}


def load_seed(seed_dir: Path) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """(labels by case, results by case, cases with no results yet)."""
    labels = json.loads((seed_dir / "labels.json").read_text())["cases"]
    results, missing = {}, []
    for case_id in sorted(labels):
        path = seed_dir / "results" / f"{case_id}.json"
        if path.exists():
            results[case_id] = json.loads(path.read_text())
        else:
            missing.append(case_id)
    return labels, results, missing


def _truths(case_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(case_dir.glob(f"*{TRUTH_SUFFIX}"))]


# --------------------------------------------------------------------------
# The three metrics
# --------------------------------------------------------------------------

def score_extraction(truth: dict[str, Any], documents: list[dict[str, Any]]) -> dict[str, Any]:
    """One document. `documents`: the result records of the file(s) it was uploaded as."""
    key = _key_fields(truth["document_type"])
    acceptable = {truth["document_type"], *truth.get("also_acceptable", [])}
    predicted_types = [d.get("document_type") for d in documents]
    rows = []
    # A list field an officer edited can't be put back as the agents left it (the audit keeps only the changed rows),
    # so it isn't scored (scripts/metrics/fetch_cases.py).
    officer_edited = {name for d in documents for name in d.get("officer_edited_fields") or []}
    for name, value in truth["fields"].items():
        if name in officer_edited:
            continue
        alternatives = [value, *(truth.get("accept_also") or {}).get(name, [])]
        strict, any_name, hit_rows, total_rows = False, False, 0, 0
        for document in documents:
            fields = document.get("extracted_fields") or {}
            for alternative in alternatives:
                correct, hits, total = compare(name, alternative, fields.get(name))
                strict = strict or correct
                hit_rows, total_rows = max(hit_rows, hits), max(total_rows, total)
                any_name = any_name or correct or any(
                    compare(name, alternative, other)[0] for other_name, other in fields.items() if other_name != name
                )
        rows.append({
            "field": name, "key": name in key, "correct": strict, "correct_any_name": any_name,
            "is_table": isinstance(value, list) and bool(value), "rows_found": hit_rows, "rows_total": total_rows,
            "expected": value if not isinstance(value, list) else f"[{len(value)} rows]",
            "extracted": None if strict else next(
                (_unwrap((d.get("extracted_fields") or {}).get(name)) for d in documents
                 if name in (d.get("extracted_fields") or {})), None),
        })
    return {
        "filename": truth["filename"],
        "document_type": truth["document_type"],
        "classified_as": predicted_types,
        "classified_correctly": bool(predicted_types) and all(t in acceptable for t in predicted_types),
        "acceptable_types": sorted(acceptable),
        "classification_confidence": min((d.get("classification_confidence") or 0.0 for d in documents), default=None),
        "forged": truth["fraud_expected"],
        "uploaded": bool(documents),
        "not_scored_officer_edited": sorted(officer_edited & set(truth["fields"])),
        "fields": rows,
    }


def _severity_counts(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    findings = (report or {}).get("findings") or []
    return [f for f in findings if isinstance(f, dict)
            and str(f.get("severity") or "").strip().lower() not in ("", "info", "informational")]


def _mentions(blob: str, value: Any) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        shapes = {f"{value:,.2f}", f"{value:.2f}", f"{value:,.0f}", f"{value:.0f}"}
        return any(shape in blob for shape in shapes)
    text = str(value)
    shapes = {text.casefold()}
    if _ISO_DATE.match(text):
        day = date.fromisoformat(text)
        shapes |= {day.strftime(f).casefold() for f in ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%Y-%m-%d")} | {str(day.year)}
    unit = re.search(r"#\s*\w+\s*-\s*\w+", text)
    if unit:
        shapes = {unit.group(0).casefold()}
    return any(shape in blob for shape in shapes)


def score_detection(truth: dict[str, Any], documents: list[dict[str, Any]], analysis: dict[str, Any] | None) -> dict[str, Any]:
    """One document: was it flagged, by the right check, naming the changed value."""
    ids = {d["id"] for d in documents}
    findings = [f for d in documents for f in _severity_counts(d.get("detection_report"))]
    cross = [f for f in ((analysis or {}).get("cross_document_findings") or [])
             if ids & set(f.get("document_ids") or [])]
    tools = {str(f.get("tool")) for f in findings} | ({"cross_document_analysis"} if cross else set())
    blob = json.dumps([findings, cross], default=str).casefold()
    entries = []
    for entry in truth.get("tampering") or []:
        entries.append({
            "dimension": entry["dimension"],
            "right_check": bool(tools & set(entry["expected_tools"])),
            "names_value": (_mentions(blob, entry.get("forged")) or _mentions(blob, entry.get("original")))
            if entry.get("field") else None,
        })
    forged = truth["fraud_expected"]
    return {
        "filename": truth["filename"],
        "forged": forged,
        "scanned": any(d.get("detection_report") is not None for d in documents),
        # A cross-document finding cites every document in the mismatch, the genuine ones included, so on a clean
        # document only its own scan's findings count as a false positive.
        "flagged": bool(findings or cross) if forged else bool(findings),
        "cited_by_cross_document_finding": bool(cross),
        "tools_fired": sorted(tools),
        # What flagged it, kept for a clean document so each false positive can be traced to its check.
        "own_findings": [] if forged else [
            {"tool": f.get("tool"), "severity": f.get("severity"), "summary": str(f.get("summary") or "")[:200]}
            for f in findings
        ],
        "tampering": entries,
    }


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return grouped


def _rate(hit: int, total: int) -> float | None:
    return round(hit / total, 4) if total else None


def score_seed(seed_dir: Path) -> dict[str, Any]:
    labels, results, missing = load_seed(seed_dir)
    extraction, detection, risk, notes = [], [], [], []
    for case_id, result in results.items():
        by_name = {d["filename"]: d for d in result["documents"]}
        # Uploaded, but not the dataset's file (fetch_cases.py): no truth describes it, so it isn't scored at all.
        excluded = set(result.get("excluded_documents") or [])
        analysis = result.get("analysis")
        for truth in _truths(seed_dir / case_id):
            if excluded & set(truth["files"]):
                continue
            documents = [by_name[name] for name in truth["files"] if name in by_name]
            extraction.append({"case_id": case_id, **score_extraction(truth, documents)})
            detection.append({"seed": int(seed_dir.name.split("-")[-1]), "case_id": case_id,
                              **score_detection(truth, documents, analysis)})
        level = ((analysis or {}).get("risk") or {}).get("level")
        risk.append({
            "seed": int(seed_dir.name.split("-")[-1]),
            "case_id": case_id,
            "expected": labels[case_id]["expected_risk"],
            "system": level,
            "system_level": level,
            "score": ((analysis or {}).get("risk") or {}).get("score"),
        })
        notes.extend(f"{case_id}: {note}" for note in result.get("notes") or [])
    return {
        "seed": int(seed_dir.name.split("-")[-1]),
        "cases_scored": sorted(results),
        "cases_without_results": missing,
        "extraction": extraction,
        "detection": detection,
        "risk": risk,
        "run_notes": notes,
    }


def summarise(seeds: list[dict[str, Any]]) -> dict[str, Any]:
    fields = [(s["seed"], d, f) for s in seeds for d in s["extraction"] for f in d["fields"]]

    def accuracy(rows: list[tuple[int, dict, dict]], column: str = "correct") -> dict[str, Any]:
        return {"correct": sum(1 for *_, f in rows if f[column]), "total": len(rows),
                "accuracy": _rate(sum(1 for *_, f in rows if f[column]), len(rows))}

    key_rows = [r for r in fields if r[2]["key"]]
    tables = [f for *_, f in fields if f["is_table"]]
    by_type: dict[str, list] = defaultdict(list)
    for row in key_rows:
        by_type[row[1]["document_type"]].append(row)
    documents = [{"seed": s["seed"], **d} for s in seeds for d in s["extraction"]]
    # A document that never reached the system has no type to judge; it's listed under documents_not_uploaded.
    uploaded = [d for d in documents if d["uploaded"]]

    forged = [d for s in seeds for d in s["detection"] if d["forged"]]
    # Only clean documents that were scanned can be falsely flagged; one never scanned is listed as a gap instead.
    clean = [d for s in seeds for d in s["detection"] if not d["forged"] and d["scanned"]]
    clean_unscanned = [d for s in seeds for d in s["detection"] if not d["forged"] and not d["scanned"]]
    entries = [e for d in forged for e in d["tampering"]]
    valued = [e for e in entries if e["names_value"] is not None]
    by_dimension: dict[str, list] = defaultdict(list)
    for entry in entries:
        by_dimension[entry["dimension"]].append(entry)

    cases = [c for s in seeds for c in s["risk"]]
    confusion = Counter((c["expected"], c["system_level"] or "not scored") for c in cases)

    return {
        "extraction": {
            "key_fields": accuracy(key_rows),
            "all_fields": accuracy(fields),
            "all_fields_any_name": accuracy(fields, "correct_any_name"),
            "table_rows": {"found": sum(f["rows_found"] for f in tables), "total": sum(f["rows_total"] for f in tables),
                           "rate": _rate(sum(f["rows_found"] for f in tables), sum(f["rows_total"] for f in tables))},
            "key_fields_by_seed": {str(s["seed"]): accuracy([r for r in key_rows if r[0] == s["seed"]]) for s in seeds},
            "key_fields_by_document_type": {t: accuracy(rows) for t, rows in sorted(by_type.items())},
            "key_fields_on_forged_documents": accuracy([r for r in key_rows if r[1]["forged"]]),
            "classification": {"correct": sum(1 for d in uploaded if d["classified_correctly"]), "total": len(uploaded),
                               "accuracy": _rate(sum(1 for d in uploaded if d["classified_correctly"]), len(uploaded))},
            "classification_by_document_type": {
                t: {"correct": sum(1 for d in rows if d["classified_correctly"]), "total": len(rows),
                    "accuracy": _rate(sum(1 for d in rows if d["classified_correctly"]), len(rows))}
                for t, rows in sorted(_group(uploaded, "document_type").items())
            },
            "misclassified": [
                {"case_id": d["case_id"], "seed": d["seed"], "filename": d["filename"], "expected": d["document_type"],
                 "classified_as": d["classified_as"], "confidence": d["classification_confidence"]}
                for d in uploaded if not d["classified_correctly"]
            ],
            "documents_not_uploaded": [f"{d['case_id']}/{d['filename']}" for d in documents if not d["uploaded"]],
        },
        "risk": {
            "correct": sum(1 for c in cases if c["expected"] == c["system_level"]),
            "total": len(cases),
            "accuracy": _rate(sum(1 for c in cases if c["expected"] == c["system_level"]), len(cases)),
            "by_seed": {str(s["seed"]): _rate(sum(1 for c in s["risk"] if c["expected"] == c["system_level"]),
                                              len(s["risk"])) for s in seeds},
            "confusion": {f"{expected} -> {system}": n for (expected, system), n in sorted(confusion.items())},
            "wrong": [c for c in cases if c["expected"] != c["system_level"]],
            "clean_cases_rated_above_low": {
                "count": sum(1 for c in cases if c["expected"] == "Low" and c["system_level"] not in (None, "Low")),
                "total": sum(1 for c in cases if c["expected"] == "Low"),
                "rate": _rate(sum(1 for c in cases if c["expected"] == "Low" and c["system_level"] not in (None, "Low")),
                              sum(1 for c in cases if c["expected"] == "Low")),
            },
        },
        "detection": {
            "forged_documents_flagged": {"flagged": sum(1 for d in forged if d["flagged"]), "total": len(forged),
                                         "accuracy": _rate(sum(1 for d in forged if d["flagged"]), len(forged))},
            "by_seed": {str(s["seed"]): _rate(sum(1 for d in s["detection"] if d["forged"] and d["flagged"]),
                                              sum(1 for d in s["detection"] if d["forged"])) for s in seeds},
            "forgeries_caught_by_the_right_check": {
                "caught": sum(1 for e in entries if e["right_check"]), "total": len(entries),
                "rate": _rate(sum(1 for e in entries if e["right_check"]), len(entries))},
            "forgeries_where_a_finding_names_the_changed_value": {
                "named": sum(1 for e in valued if e["names_value"]), "total": len(valued),
                "rate": _rate(sum(1 for e in valued if e["names_value"]), len(valued))},
            "right_check_by_dimension": {
                d: {"caught": sum(1 for e in rows if e["right_check"]), "total": len(rows)}
                for d, rows in sorted(by_dimension.items())},
            "clean_documents_flagged": {"flagged": sum(1 for d in clean if d["flagged"]), "total": len(clean),
                                        "rate": _rate(sum(1 for d in clean if d["flagged"]), len(clean))},
            "false_positives": [
                {"seed": d["seed"], "case_id": d["case_id"], "filename": d["filename"], **finding}
                for d in clean if d["flagged"] for finding in d["own_findings"]
            ],
            "missed": [f"{d['seed']}: {d['case_id']}/{d['filename']}" for d in forged if not d["flagged"]],
            "clean_documents_never_scanned": [f"{d['seed']}: {d['case_id']}/{d['filename']}" for d in clean_unscanned],
            "forged_documents_never_scanned": [f"{d['seed']}: {d['case_id']}/{d['filename']}" for d in forged if not d["scanned"]],
        },
    }


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def _confidence(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def markdown(summary: dict[str, Any], seeds: list[dict[str, Any]]) -> str:
    e, r, d = summary["extraction"], summary["risk"], summary["detection"]
    # Seed columns and the per-seed table only when more than one seed is scored; the unseeded set is one dataset.
    multi = len(seeds) > 1
    cases = [c for s in seeds for c in s["cases_scored"]]
    seed_label = (lambda seed: f"seed {seed}: ") if multi else (lambda seed: "")

    def seedless(name: str) -> str:
        """The scorer lists documents as "<seed>: <case>/<file>"; drop the seed when there's only one."""
        return name if multi else name.split(": ", 1)[-1]

    lines = [
        "# Success metrics",
        "",
        (f"Seeds {', '.join(str(s['seed']) for s in seeds)}; {len(cases)} case run(s) scored." if multi
         else f"{len(cases)} case(s) scored: {', '.join(cases)}."),
        "",
        "| Metric | Result | Count |",
        "|---|---|---|",
        f"| Classification accuracy | {_pct(e['classification']['accuracy'])} | "
        f"{e['classification']['correct']} of {e['classification']['total']} documents |",
        f"| Extraction accuracy (key fields) | {_pct(e['key_fields']['accuracy'])} | "
        f"{e['key_fields']['correct']} of {e['key_fields']['total']} fields |",
        f"| Risk score accuracy | {_pct(r['accuracy'])} | {r['correct']} of {r['total']} cases |",
        f"| Anomaly detection accuracy | {_pct(d['forged_documents_flagged']['accuracy'])} | "
        f"{d['forged_documents_flagged']['flagged']} of {d['forged_documents_flagged']['total']} forged documents |",
        f"| False positive rate (clean documents flagged) | {_pct(d['clean_documents_flagged']['rate'])} | "
        f"{d['clean_documents_flagged']['flagged']} of {d['clean_documents_flagged']['total']} scanned clean documents |",
        f"| False positive rate (clean cases rated above Low) | {_pct(r['clean_cases_rated_above_low']['rate'])} | "
        f"{r['clean_cases_rated_above_low']['count']} of {r['clean_cases_rated_above_low']['total']} clean cases |",
        "",
        "## Classification",
        "",
        "| Document type | Accuracy | Count |",
        "|---|---|---|",
        *[f"| {t} | {_pct(v['accuracy'])} | {v['correct']} of {v['total']} |"
          for t, v in e["classification_by_document_type"].items()],
        *(["", "| Case | Document | Expected | Classified as | Confidence |", "|---|---|---|---|---|",
           *[f"| {seed_label(m['seed'])}{m['case_id']} | {m['filename']} | {m['expected']} | "
             f"{', '.join(str(t) for t in m['classified_as']) or 'no type'} | "
             f"{_confidence(m['confidence'])} |"
             for m in e["misclassified"]]] if e["misclassified"] else ["", "Every document was given the right type."]),
        "",
        "## Extraction",
        "",
        "| Reading | Accuracy | Count |",
        "|---|---|---|",
        f"| Key fields, right name | {_pct(e['key_fields']['accuracy'])} | {e['key_fields']['correct']} of {e['key_fields']['total']} |",
        f"| All truth fields, right name | {_pct(e['all_fields']['accuracy'])} | {e['all_fields']['correct']} of {e['all_fields']['total']} |",
        f"| All truth fields, any name | {_pct(e['all_fields_any_name']['accuracy'])} | "
        f"{e['all_fields_any_name']['correct']} of {e['all_fields_any_name']['total']} |",
        f"| Table rows found | {_pct(e['table_rows']['rate'])} | {e['table_rows']['found']} of {e['table_rows']['total']} |",
        f"| Key fields on forged documents | {_pct(e['key_fields_on_forged_documents']['accuracy'])} | "
        f"{e['key_fields_on_forged_documents']['correct']} of {e['key_fields_on_forged_documents']['total']} |",
        "",
        "| Document type | Key-field accuracy | Count |",
        "|---|---|---|",
        *[f"| {t} | {_pct(v['accuracy'])} | {v['correct']} of {v['total']} |"
          for t, v in e["key_fields_by_document_type"].items()],
        "",
        "## Risk",
        "",
        "| Expected -> system | Cases |",
        "|---|---|",
        *[f"| {pair} | {n} |" for pair, n in r["confusion"].items()],
        "",
        "## Anomaly detection",
        "",
        "| Reading | Result | Count |",
        "|---|---|---|",
        f"| Forged documents flagged | {_pct(d['forged_documents_flagged']['accuracy'])} | "
        f"{d['forged_documents_flagged']['flagged']} of {d['forged_documents_flagged']['total']} |",
        f"| Forgeries caught by the right check | {_pct(d['forgeries_caught_by_the_right_check']['rate'])} | "
        f"{d['forgeries_caught_by_the_right_check']['caught']} of {d['forgeries_caught_by_the_right_check']['total']} |",
        f"| Forgeries where a finding names the changed value | "
        f"{_pct(d['forgeries_where_a_finding_names_the_changed_value']['rate'])} | "
        f"{d['forgeries_where_a_finding_names_the_changed_value']['named']} of "
        f"{d['forgeries_where_a_finding_names_the_changed_value']['total']} |",
        f"| Clean documents flagged (false positives) | {_pct(d['clean_documents_flagged']['rate'])} | "
        f"{d['clean_documents_flagged']['flagged']} of {d['clean_documents_flagged']['total']} |",
        "",
        "| Forgery type | Caught by the right check |",
        "|---|---|",
        *[f"| {dim} | {v['caught']} of {v['total']} |" for dim, v in d["right_check_by_dimension"].items()],
    ]
    if multi:
        lines += [
            "", "## By seed", "",
            "| Seed | Extraction (key fields) | Risk | Anomaly detection |",
            "|---|---|---|---|",
            *[f"| {s['seed']} | {_pct(e['key_fields_by_seed'][str(s['seed'])]['accuracy'])} | "
              f"{_pct(r['by_seed'][str(s['seed'])])} | {_pct(d['by_seed'][str(s['seed'])])} |" for s in seeds],
        ]
    if r["wrong"]:
        lines += ["", "## Cases with the wrong risk level", "", "| Case | Expected | System |", "|---|---|---|",
                  *[f"| {seed_label(c['seed'])}{c['case_id']} | {c['expected']} | {c['system'] or 'not scored'} |"
                    for c in r["wrong"]]]
    if d["missed"]:
        lines += ["", "## Forged documents not flagged", "", *[f"- {seedless(name)}" for name in d["missed"]]]
    if d["false_positives"]:
        by_tool = Counter(f["tool"] for f in d["false_positives"])
        lines += ["", "## False positives", "",
                  "Findings above \"info\" on clean documents, from each document's own scan.", "",
                  "| Check | Findings |", "|---|---|", *[f"| {tool} | {n} |" for tool, n in by_tool.most_common()], "",
                  "| Case | Document | Check | Severity | Finding |", "|---|---|---|---|---|",
                  *[f"| {seed_label(f['seed'])}{f['case_id']} | {f['filename']} | {f['tool']} | {f['severity']} | "
                    f"{f['summary'].replace('|', '/')} |" for f in d["false_positives"]]]
    gaps = [f"{seed_label(s['seed'])}no results for {', '.join(s['cases_without_results'])}"
            for s in seeds if s["cases_without_results"]]
    notes = [n for s in seeds for n in s["run_notes"]]
    unscanned = [f"not scanned, so not checked: {seedless(name)}" for name in
                 d["clean_documents_never_scanned"] + d["forged_documents_never_scanned"]]
    if gaps or notes or e["documents_not_uploaded"] or unscanned:
        lines += ["", "## Gaps in the run", "", *[f"- {g}" for g in gaps], *[f"- {u}" for u in unscanned],
                  *[f"- no result for {name}" for name in e["documents_not_uploaded"]], *[f"- {n}" for n in notes]]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, nargs="+", help="default: every seed folder with results")
    parser.add_argument("--dataset", type=Path, default=DATASET_ROOT)
    args = parser.parse_args()

    folders = sorted(
        (args.dataset / f"seed-{s}" for s in args.seeds) if args.seeds
        else (p for p in args.dataset.glob("seed-*") if (p / "results").is_dir())
    )
    seeds = [score_seed(folder) for folder in folders if (folder / "labels.json").exists()]
    seeds = [s for s in seeds if s["cases_scored"]]
    if not seeds:
        raise SystemExit("No results to score. Run scripts.metrics.run_pipeline first.")
    summary = summarise(seeds)
    (args.dataset / "metrics_report.json").write_text(
        json.dumps({"summary": summary, "seeds": seeds}, indent=2, default=str) + "\n")
    report = markdown(summary, seeds)
    (args.dataset / "metrics_report.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
