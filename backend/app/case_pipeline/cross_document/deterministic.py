"""
The case-wide comparisons that need no judgement, run on every case analysis whatever the agent proposes:
company name, address, UEN, and a person's ID number and name across documents. Each difference is one finding
(one row for the officer), made in code from the extracted fields. They also go to the agent as facts already
established, so it can reason from them without restating them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...case_match import normalize_uen
from ...fraud_detection.numerical_validation.common import _decode
from ..models import CrossDocFinding, FindingCategory
from .address_matching import find_address_mismatches
from .company_identity import check_company_name_consistency
from .evidence import CaseContext, mask_identifiers
from .identity_pairs import candidate_pairs, compare_pair

_IDENTIFIER = "IDENTIFIER_CONFLICT"
_VARIANCE = {"name": "NAME_MISMATCH", "address": "ADDRESS_MISMATCH", "person": "PERSON_MISMATCH"}
# What the agent is shown of these results: enough to reason from, bounded.
MAX_BRIEF_LINES = 12
_LINE_CHARS = 300


@dataclass
class DeterministicResult:
    findings: list[CrossDocFinding] = field(default_factory=list)
    consistent: list[str] = field(default_factory=list)


def _text(value: object) -> str:
    return mask_identifiers(str(value))[:160]


def _finding(
    context: CaseContext, n: int, summary: str, flag: str, entries: list[tuple[str, str, object]],
) -> CrossDocFinding:
    """entries: (document id, field, value) for each side, issued as refs carrying the flag."""
    citations, document_ids = [], []
    for document_id, name, value in entries:
        view = context.by_id(document_id)
        if view is None:
            continue
        citations.append(context.ledger.issue(view.key, name, value, source="deterministic", breach_flag=flag))
        if view.id not in document_ids:
            document_ids.append(view.id)
    identity = flag == _IDENTIFIER
    return CrossDocFinding(
        id=f"xd-det-{n}",
        hypothesis_id="deterministic",
        summary=summary[:400],
        rationale="Extracted values differ across the case's documents; review the cited documents.",
        severity="high" if identity else "medium",
        category=FindingCategory.IDENTITY_ENTITY_CONFLICT if identity else FindingCategory.CROSS_DOCUMENT_VARIANCE,
        citations=list(dict.fromkeys(citations)),
        document_ids=document_ids,
        breach_flags=[flag],
    )


def _company_names(context: CaseContext, out: DeterministicResult, add) -> None:
    # One finding per pair of differing names, citing every document that carries either name.
    pairs: dict[frozenset[str], dict] = {}
    compared = 0
    for record in context.records:
        result = check_company_name_consistency(record, context.records)
        if result.skipped_reason:
            continue
        compared += 1
        for item in result.discrepancies:
            key = frozenset({item.stated.strip().lower(), item.conflicting.strip().lower()})
            entry = pairs.setdefault(key, {"summary": (
                f'Company name "{_text(item.stated)}" on {record.filename} differs from "{_text(item.conflicting)}" '
                f"on {', '.join(item.filenames[:3])}"
            ), "documents": {}})
            entry["documents"].setdefault(record.id, item.stated)
            for document_id in item.document_ids:
                entry["documents"].setdefault(document_id, item.conflicting)
    for entry in pairs.values():
        add(entry["summary"], _VARIANCE["name"], [(i, "company_name", v) for i, v in entry["documents"].items()])
    if compared >= 2 and not pairs:
        out.consistent.append(f"Company name is the same across {compared} documents")


def _addresses(context: CaseContext, out: DeterministicResult, add) -> None:
    mismatches = find_address_mismatches(r for r in context.records if r.extracted_fields)
    for m in mismatches:
        add(
            f'{m.first.field_name.replace("_", " ")} "{_text(m.first.value)}" in {m.first.filename} differs from '
            f'"{_text(m.second.value)}" in {m.second.filename}',
            _VARIANCE["address"],
            [(m.first.document_id, m.first.field_name, m.first.value), (m.second.document_id, m.second.field_name, m.second.value)],
        )
    if not mismatches and len(context.records) >= 2:
        out.consistent.append("No address differs between documents for the same person or company")


def _uens(context: CaseContext, out: DeterministicResult, add) -> None:
    by_value: dict[str, list[tuple[str, str]]] = {}
    for view in context.docs.values():
        raw = _decode(view.fields.get("uen"))
        normalised = normalize_uen(str(raw)) if raw not in (None, "") else None
        if normalised:
            by_value.setdefault(normalised, []).append((view.id, view.filename))
    values = list(by_value)
    for i, left in enumerate(values):
        for right in values[i + 1:]:
            add(
                f"UEN {_text(left)} on {', '.join(f for _, f in by_value[left][:3])} differs from UEN {_text(right)} "
                f"on {', '.join(f for _, f in by_value[right][:3])}",
                _IDENTIFIER,
                [*((i, "uen", left) for i, _ in by_value[left]), *((i, "uen", right) for i, _ in by_value[right])],
            )
    if len(values) == 1 and len(by_value[values[0]]) >= 2:
        out.consistent.append(f"UEN is the same on {len(by_value[values[0]])} documents")


def _people(context: CaseContext, out: DeterministicResult, add) -> None:
    for left_key, right_key in candidate_pairs(context):
        left, right = context.docs[left_key], context.docs[right_key]
        comparison = compare_pair(left, right)
        for m in comparison.mismatches:
            is_id = m.check == "individual_id"
            what = "ID number" if is_id else "Name"
            add(
                f'{what} "{_text(m.left_value)}" in {left.filename} differs from "{_text(m.right_value)}" in {right.filename}',
                _IDENTIFIER if is_id else _VARIANCE["person"],
                [(left.id, m.left_field, m.left_value), (right.id, m.right_field, m.right_value)],
            )
        for compared, check in ((comparison.ids_compared, "ID number"), (comparison.names_compared, "name")):
            if compared and not any(m.check == ("individual_id" if check == "ID number" else "individual_name")
                                    for m in comparison.mismatches):
                out.consistent.append(f"The individual's {check} is the same on {left.filename} and {right.filename}")


def run_deterministic_checks(context: CaseContext) -> DeterministicResult:
    """All case-wide comparisons, one finding per difference. Never raises: a failed check is skipped."""
    out = DeterministicResult()

    def add(summary: str, flag: str, entries: list[tuple[str, str, object]]) -> None:
        out.findings.append(_finding(context, len(out.findings) + 1, summary, flag, entries))

    for step in (_company_names, _addresses, _uens, _people):
        try:
            step(context, out, add)
        except Exception:
            import logging

            logging.getLogger("classifier_pipeline.case_pipeline.deterministic").exception(
                "Deterministic check %s failed", step.__name__)
    return out


_LABELS = {
    "NAME_MISMATCH": "company name", "ADDRESS_MISMATCH": "address",
    "IDENTIFIER_CONFLICT": "identifier", "PERSON_MISMATCH": "person's name",
}
_MAX_FILES_SHOWN = 4


def _components(edges: list[tuple[str, str]]) -> list[set[str]]:
    """Values joined by a reported difference, so one underlying disagreement is one group."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    groups: dict[str, set[str]] = {}
    for value in list(parent):
        groups.setdefault(find(value), set()).add(value)
    return list(groups.values())


def _holders(files: set[str]) -> str:
    names = sorted(files)
    shown = ", ".join(names[:_MAX_FILES_SHOWN]) + (f" +{len(names) - _MAX_FILES_SHOWN} more" if len(names) > _MAX_FILES_SHOWN else "")
    return f"{shown} {'has' if len(names) == 1 else 'have'}"


def brief(result: DeterministicResult, context: CaseContext) -> str:
    """
    What the agent is told was already checked, bounded. Differences are grouped by value, one line for each
    underlying disagreement ("a.pdf has X; b.pdf and c.pdf have Y"), not one line for each pair of documents.
    """
    edges: dict[str, list[tuple[str, str]]] = {}
    holders: dict[str, dict[str, set[str]]] = {}
    for finding in result.findings:
        entries = [context.ledger.refs[c] for c in finding.citations if c in context.ledger.refs]
        flag = finding.breach_flags[0] if finding.breach_flags else ""
        if len(entries) < 2 or flag not in _LABELS:
            continue
        values = [_text(e["value"]) for e in entries]
        for entry, value in zip(entries, values):
            view = context.docs.get(entry["doc_key"])
            holders.setdefault(flag, {}).setdefault(value, set()).add(view.filename if view else entry["doc_key"])
        edges.setdefault(flag, []).extend((values[0], other) for other in values[1:])
    differs = []
    for flag, flag_edges in edges.items():
        for group in _components(flag_edges):
            ordered = sorted(group, key=lambda v: (-len(holders[flag][v]), v))
            parts = [f'{_holders(holders[flag][v])} "{v}"' for v in ordered]
            differs.append(f"- DIFFERS ({_LABELS[flag]}): " + "; ".join(parts))
    lines = [line[:_LINE_CHARS] for line in differs[:MAX_BRIEF_LINES]]
    if len(differs) > MAX_BRIEF_LINES:
        lines.append(f"- ... and {len(differs) - MAX_BRIEF_LINES} more differences")
    lines += [f"- SAME: {c}"[:_LINE_CHARS] for c in result.consistent[:MAX_BRIEF_LINES]]
    return "\n".join(lines)
