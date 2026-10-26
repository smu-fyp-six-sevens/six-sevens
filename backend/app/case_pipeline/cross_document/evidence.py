"""
The case as the analysis sees it: each approved document's fields and
detection findings, and the ledger of every reference id issued in a run.
A verdict may only cite an id the ledger issued (see verify.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from ...fraud_detection.numerical_validation.common import _as_number, _rows
from ...fraud_detection.shared.field_values import decode_field_value
from ...models import APPROVED_STATUSES, DocumentRecord
from ..categories import category_for_finding
from ..models import FindingCategory
from ..trace import RunTrace
from .bias import has_protected_text, is_protected_field
from .leads import lead_for

_NRIC_RE = re.compile(r"\b([STFGM])\d{4}(\d{3}[A-Z])\b", re.IGNORECASE)
_LONG_NUMBER_RE = re.compile(r"\b(\d[\d -]{6,}\d)\b")
_MAX_LIST_PREVIEW = 5
# The most the model reads about a case (about 6k tokens); each document gets an equal share past it.
MAX_DOSSIER_CHARS = 24_000


def mask_identifiers(text: str) -> str:
    """NRIC and long account numbers as the model sees them: enough to tell two apart, not enough to use."""
    text = _NRIC_RE.sub(lambda m: f"{m.group(1).upper()}****{m.group(2)}", text)

    def _mask(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(1))
        return f"****{digits[-3:]}" if len(digits) >= 8 else match.group(1)

    return _LONG_NUMBER_RE.sub(_mask, text)


def signed_amount(row: dict) -> float | None:
    amount = _as_number(row.get("amount"))
    if amount is not None:
        return amount
    credit, debit = _as_number(row.get("credit")), _as_number(row.get("debit"))
    if credit is None and debit is None:
        return None
    return (credit or 0.0) - (debit or 0.0)


def transaction_rows(fields: dict) -> list[dict]:
    return _rows(decode_field_value(fields.get("transactions")))


@dataclass
class DocView:
    key: str
    id: str
    filename: str
    document_type: str
    fields: dict[str, Any]
    findings: list[dict]
    fields_confirmed_by_officer: bool


@dataclass
class Ledger:
    """Every reference id issued in one run, and what each stands for."""

    refs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def issue(
        self,
        doc_key: str,
        name: str,
        value: Any,
        *,
        row: int | None = None,
        source: str = "dossier",
        breach_flag: str | None = None,
        category: FindingCategory | None = None,
    ) -> str:
        ref_id = f"ref:{doc_key}:{name}" + (f":{row}" if row is not None else "")
        existing = self.refs.get(ref_id)
        if existing is None or (breach_flag and not existing.get("breach_flag")):
            self.refs[ref_id] = {
                "doc_key": doc_key, "field": name, "row": row, "value": value,
                "source": source, "breach_flag": breach_flag,
                "category": category.value if category else None,
            }
        return ref_id


@dataclass
class CaseContext:
    case_id: str
    docs: dict[str, DocView]
    # The approved documents themselves, for the case-wide consistency checks.
    records: list[DocumentRecord] = field(default_factory=list)
    ledger: Ledger = field(default_factory=Ledger)
    trace: RunTrace | None = None
    # Model calls spent so far in this run; agents.py refuses to go past its budget.
    model_calls: int = 0
    # What the code-side comparisons already established (deterministic.brief), shown to the planner.
    established: str = ""

    def note(self, step: str, **fields: Any) -> None:
        """Records a step on the run's trace, if it has one."""
        if self.trace is not None:
            self.trace.record(step, **fields)

    def by_id(self, document_id: str) -> DocView | None:
        return next((d for d in self.docs.values() if d.id == document_id), None)

    def resolve(self, key_or_id: str) -> DocView | None:
        return self.docs.get(key_or_id) or self.by_id(key_or_id)


def build_context(
    case_id: str,
    documents: list[DocumentRecord],
    reports: dict[str, dict[str, Any]],
) -> CaseContext:
    docs: dict[str, DocView] = {}
    records: list[DocumentRecord] = []
    counters: dict[str, int] = {}
    for document in sorted(documents, key=lambda d: (d.document_type or "", d.filename)):
        if document.status not in APPROVED_STATUSES:
            continue
        records.append(document)
        doc_type = document.document_type or "document"
        counters[doc_type] = counters.get(doc_type, 0) + 1
        key = f"{doc_type}_{counters[doc_type]}"
        report = reports.get(document.id) or {}
        findings = [
            {**f, "category": category_for_finding(f).value}
            for f in report.get("findings") or []
        ]
        docs[key] = DocView(
            key=key, id=document.id, filename=document.filename, document_type=doc_type,
            fields=dict(document.extracted_fields or {}), findings=findings,
            fields_confirmed_by_officer=document.document_type == "unrecognised",
        )
    return CaseContext(case_id=case_id, docs=docs, records=records)


def transaction_summary(rows: list[dict]) -> dict[str, Any]:
    amounts = [a for a in (signed_amount(r) for r in rows) if a is not None]
    credits = [a for a in amounts if a > 0]
    debits = [-a for a in amounts if a < 0]
    sizes = sorted(abs(a) for a in amounts)
    return {
        "count": len(amounts),
        "credit_count": len(credits),
        "debit_count": len(debits),
        "total_credits": round(sum(credits), 2),
        "total_debits": round(sum(debits), 2),
        "median_size": round(median(sizes), 2) if sizes else None,
        "p90_size": round(sizes[int(0.9 * (len(sizes) - 1))], 2) if sizes else None,
        "max_size": round(sizes[-1], 2) if sizes else None,
        "min_size": round(sizes[0], 2) if sizes else None,
    }


def render_dossier(context: CaseContext, max_chars: int = MAX_DOSSIER_CHARS) -> str:
    """
    The text the hypothesiser reads: scalar fields with their refs, derived transaction facts, detection
    findings. A person's protected attributes are left out (see bias.py), and the whole is capped at max_chars.
    """
    blocks: list[str] = []
    for view in context.docs.values():
        lines = [f"[{view.key}] {view.filename} - {view.document_type}"
                 + (" (fields confirmed by the officer)" if view.fields_confirmed_by_officer else "")]
        for name, value in view.fields.items():
            if is_protected_field(name):
                continue
            decoded = decode_field_value(value)
            if name == "transactions" or (isinstance(decoded, list) and decoded and isinstance(decoded[0], dict)):
                rows = _rows(decoded)
                lines.append(f"  {name}: {len(rows)} row(s)")
                if name == "transactions" and rows:
                    summary = transaction_summary(rows)
                    ref = context.ledger.issue(view.key, "transaction_summary", summary, source="dossier")
                    lines.append(f"    derived: {summary}  {ref}")
                continue
            if isinstance(decoded, list):
                shown = decoded[:_MAX_LIST_PREVIEW]
                text = mask_identifiers(", ".join(str(v) for v in shown))
                if len(decoded) > len(shown):
                    text += f" (+{len(decoded) - len(shown)} more)"
            elif decoded is None or str(decoded).strip() == "":
                continue
            else:
                text = mask_identifiers(str(decoded))
            lines.append(f"  {name} = {text[:200]}  {context.ledger.issue(view.key, name, value)}")
        for finding in view.findings:
            ref = context.ledger.issue(
                view.key, f"finding:{finding.get('id')}", finding.get("summary"),
                source="detection", category=FindingCategory(finding["category"]),
            )
            summary = str(finding.get("summary") or "")
            shown = "(details withheld: concerns a personal attribute)" if has_protected_text(summary) \
                else mask_identifiers(summary)[:200]
            lines.append(f"  finding [{finding.get('severity')}] {finding.get('tool')}: {shown}  {ref}")
            lead = lead_for(finding, context.docs)
            if lead and not shown.startswith("(details withheld"):
                lines.append(f"    {lead}")
        blocks.append("\n".join(lines))
    if blocks and sum(len(b) for b in blocks) > max_chars:
        share = max(200, max_chars // len(blocks))
        blocks = [
            b if len(b) <= share else b[:share].rsplit("\n", 1)[0] + "\n  ... (more fields not shown)"
            for b in blocks
        ]
    return "\n\n".join(blocks)
