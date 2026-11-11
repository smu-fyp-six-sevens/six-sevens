"""Build labels from the document mastersheet and score exported detection reports.

From backend/:
    python scripts/evaluate_scrum327.py --labels
    python scripts/evaluate_scrum327.py --reports /path/to/detection-reports.json

Report JSON is an array of objects with ``filename``, ``case_id`` (needed when
the mastersheet reuses a filename), and ``findings``. A finding needs a
``tool`` and may include ``severity``; informational findings do not count as
a fraud flag. This is an offline evaluator: it does not call Claude, Supabase,
or the application database.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import openpyxl

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_MASTERSHEET = BACKEND / "doc_gen_scripts" / "v16_document_mastersheet.xlsx"

# Map the mastersheet's broad fraud dimensions to detection tool identifiers.
# Some dimensions have more than one applicable tool (e.g. image manipulation).
DIMENSION_TOOLS: dict[str, tuple[str, ...]] = {
    "spelling": ("spelling_tool",),
    "font/size": ("font_consistency_tool",),
    "misalignment": ("layout_anomalies_tool",),
    "metadata": ("metadata_tool",),
    "date anomalies": ("date_consistency_tool",),
    "numerical": ("numerical_checks_tool",),
    "image manipulation": ("image_manipulation_tool", "image_tampering_tool"),
    "text overlay": ("layout_anomalies_tool",),
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _dimension_tools(dimension: Any, description: Any) -> tuple[str, ...] | None:
    """Return expected detector(s); None means the sheet label needs curation."""
    raw = _norm(dimension)
    # The current v16 sheet labels an address-vs-mastersheet discrepancy as
    # Metadata. Keep it fraud-positive, but do not score its detector mapping
    # until that label is corrected in the source sheet.
    if raw == "metadata" and "address does not match mastersheet" in _norm(description):
        return None
    return DIMENSION_TOOLS.get(raw)


def load_master_labels(path: Path = DEFAULT_MASTERSHEET) -> list[dict[str, Any]]:
    """Read each filename row with an explicit Y/N fraud label from the workbook."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    labels: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    try:
        for sheet in workbook.worksheets:
            rows = sheet.iter_rows(values_only=True)
            headers = next(rows, None)
            if not headers:
                continue
            columns = {str(name).strip(): i for i, name in enumerate(headers) if name is not None}
            required = {"Filename", "Fraud Injected? (Y/N)"}
            if not required.issubset(columns):
                continue
            for row in rows:
                filename = str(row[columns["Filename"]] or "").strip()
                injected = _norm(row[columns["Fraud Injected? (Y/N)"]]).upper()
                if not filename or injected not in {"Y", "N"}:
                    continue
                dimension = row[columns["Detection Dimension"]] if "Detection Dimension" in columns else None
                description = row[columns["Fraud Description"]] if "Fraud Description" in columns else None
                case_column = next((columns[name] for name in ("Case ID", "Case ID (auto)") if name in columns), None)
                case_id = row[case_column] if case_column is not None else None
                normalized_case_id = _norm(case_id) or None
                key = (filename.casefold(), normalized_case_id)
                if key in seen:
                    raise ValueError(f"Duplicate labelled filename and case in mastersheet: {filename} ({case_id})")
                seen.add(key)
                expected_tools = _dimension_tools(dimension, description) if injected == "Y" else ()
                if injected == "N":
                    label_state = "clean"
                elif not _norm(dimension):
                    label_state = "missing_dimension"
                elif expected_tools is None:
                    label_state = "dimension_needs_curation"
                else:
                    label_state = "mapped"
                labels.append({
                    "filename": filename,
                    "case_id": str(case_id).strip() if normalized_case_id is not None else None,
                    "source_sheet": sheet.title,
                    "fraud_expected": injected == "Y",
                    "dimension": str(dimension).strip() if dimension is not None else None,
                    "description": str(description).strip() if description is not None else None,
                    "expected_tools": list(expected_tools) if expected_tools is not None else None,
                    "label_state": label_state,
                })
    finally:
        workbook.close()
    return labels


def _finding_tools(report: dict[str, Any]) -> set[str]:
    tools = set()
    for finding in report.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        severity = _norm(finding.get("severity"))
        if severity in {"", "info", "informational"}:
            continue
        tool = str(finding.get("tool") or "").strip()
        if tool:
            tools.add(tool)
    return tools


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def evaluate(labels: list[dict[str, Any]], reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute document-level classification and mapped-dimension metrics."""
    labels_by_name: dict[str, list[dict[str, Any]]] = {}
    for label in labels:
        labels_by_name.setdefault(label["filename"].casefold(), []).append(label)
    report_by_key: dict[tuple[str, str | None], dict[str, Any]] = {}
    for report in reports:
        filename = str(report.get("filename") or "").strip()
        if not filename:
            raise ValueError("Every report must include filename")
        case_id = _norm(report.get("case_id")) or None
        key = (filename.casefold(), case_id)
        if key in report_by_key:
            raise ValueError(f"Duplicate report for {filename} (case {case_id or 'unspecified'})")
        report_by_key[key] = report

    counts = Counter()
    dimension_counts = Counter()
    evaluated = 0
    dimension_evaluated = 0
    unscored = []
    false_positives = []
    missed = []
    matched_report_keys: set[tuple[str, str | None]] = set()
    for label in labels:
        filename_key = label["filename"].casefold()
        case_id = _norm(label.get("case_id")) or None
        exact_key = (filename_key, case_id)
        ambiguous_keys = [key for key in report_by_key if key[0] == filename_key and key[1] is None]
        key = exact_key if exact_key in report_by_key else (
            ambiguous_keys[0] if len(labels_by_name[filename_key]) == 1 and ambiguous_keys else None
        )
        report = report_by_key.get(key) if key is not None else None
        if report is None:
            unscored.append(label["filename"])
            continue
        matched_report_keys.add(key)
        evaluated += 1
        predicted_tools = _finding_tools(report)
        predicted_flag = bool(predicted_tools)
        expected_flag = bool(label["fraud_expected"])
        if expected_flag and predicted_flag:
            counts["tp"] += 1
        elif expected_flag:
            counts["fn"] += 1
            missed.append(label["filename"])
        elif predicted_flag:
            counts["fp"] += 1
            false_positives.append(label["filename"])
        else:
            counts["tn"] += 1

        expected_tools = label.get("expected_tools")
        if expected_tools is None:
            continue
        dimension_evaluated += 1
        expected = set(expected_tools)
        dimension_counts["tp"] += len(expected & predicted_tools)
        dimension_counts["fp"] += len(predicted_tools - expected)
        dimension_counts["fn"] += len(expected - predicted_tools)

    tp, fp, fn, tn = (counts[k] for k in ("tp", "fp", "fn", "tn"))
    dtp, dfp, dfn = (dimension_counts[k] for k in ("tp", "fp", "fn"))
    unscored_limit = 20
    return {
        "documents": {"labelled": len(labels), "evaluated": evaluated, "unscored": len(unscored)},
        "document_metrics": {
            "accuracy": _ratio(tp + tn, evaluated),
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn),
            "false_positive_rate": _ratio(fp, fp + tn),
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "true_negative": tn,
        },
        "dimension_metrics": {
            "mapped_documents_evaluated": dimension_evaluated,
            "precision": _ratio(dtp, dtp + dfp),
            "recall": _ratio(dtp, dtp + dfn),
            "true_positive": dtp,
            "false_positive": dfp,
            "false_negative": dfn,
        },
        "label_quality": dict(Counter(label["label_state"] for label in labels)),
        "missed_fraud_documents": missed,
        "false_positive_documents": false_positives,
        "unscored_documents_sample": unscored[:unscored_limit],
        "unscored_documents_omitted": max(0, len(unscored) - unscored_limit),
        "unmatched_reports": sorted(
            report.get("filename", "") for key, report in report_by_key.items() if key not in matched_report_keys
        ),
        "notes": [
            "Document-level metrics compare expected fraud Y/N with any non-info finding.",
            "Dimension metrics count only mapped master-sheet dimensions; review label_quality before interpreting them.",
            "Metrics describe the supplied reports only and are not a live-model result unless those reports came from live runs.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mastersheet", type=Path, default=DEFAULT_MASTERSHEET)
    parser.add_argument("--labels", action="store_true", help="print the mastersheet evaluation labels as JSON")
    parser.add_argument("--reports", type=Path, help="JSON array of exported detection reports; score against labels")
    parser.add_argument("--output", type=Path, help="write JSON to this path instead of stdout")
    args = parser.parse_args()
    if args.labels == bool(args.reports):
        parser.error("choose exactly one of --labels or --reports")

    labels = load_master_labels(args.mastersheet)
    if args.labels:
        result: Any = {"source": args.mastersheet.name, "label_count": len(labels), "labels": labels}
    else:
        reports = json.loads(args.reports.read_text())
        if not isinstance(reports, list):
            parser.error("--reports must contain a JSON array")
        result = evaluate(labels, reports)
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
