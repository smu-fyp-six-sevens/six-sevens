"""
The case-wide comparisons that need no judgement, run on every case analysis whatever the agent proposes:
company name, address, UEN, and a person's ID number, name and personal details across documents. Each
disagreement is one finding (one row for the officer), made in code from the extracted fields, citing every
document that carries either value. They also go to the agent as facts already established, so it can reason
from them without restating them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from ...identifiers import uen_key
from ...fraud_detection.dates.date_parsing import parse_date
from ...fraud_detection.shared.field_values import decode_field_value
from ...models import counts_as_address_proof
from ..models import CrossDocFinding, FindingCategory
from .address_matching import find_address_mismatches, normalize_address
from .bias import has_protected_text
from .company_identity import check_company_name_consistency
from .evidence import CaseContext, mask_identifiers
from .identity_pairs import candidate_pairs, compare_pair

logger = logging.getLogger("classifier_pipeline.case_pipeline.deterministic")

_IDENTIFIER = "IDENTIFIER_CONFLICT"
_VARIANCE = {"name": "NAME_MISMATCH", "address": "ADDRESS_MISMATCH", "person": "PERSON_MISMATCH"}
# What the agent is shown of these results: enough to reason from, bounded.
MAX_BRIEF_LINES = 12
_LINE_CHARS = 300

# What an officer reads for each kind of disagreement.
_WHY = {
    "id": "The same person should carry the same ID number on every document. A different one can mean a document "
          "belongs to someone else or was altered.",
    "uen": "A company has one UEN. A different one can mean a document belongs to another company or was altered.",
    "name": "A company's name should read the same on every document. A different name can mean a document belongs "
            "to another company or was altered.",
    "address": "A company's address should be the same on every document unless it has formally changed. Check "
               "whether a change of address is on file.",
    "personal_address": "A person's address should be the same on every document unless they have moved. A recent "
                        "proof of address (a bank statement or utility bill dated after the document with the other "
                        "address) showing the current address settles it.",
    "person": "Details for one person should agree across their documents. A difference can mean a document belongs "
              "to someone else or was altered.",
}
_TYPE_LABELS = {
    "certificate_of_incumbency": "certificate of incumbency", "certificate_of_incorporation": "certificate of incorporation",
    "company_constitution": "company constitution", "notice_of_assessment": "notice of assessment",
    "nric": "NRIC", "passport": "passport", "credit_report": "credit report", "bank_statement": "bank statement",
    "payslip": "payslip", "source_of_wealth": "source of wealth document", "list_of_loans": "list of loans",
    "proof_of_address": "proof of address",
}
_PERSON_LABELS = {
    "individual_id": "ID number", "individual_name": "name", "individual_dob": "date of birth",
    "individual_race": "race", "individual_sex": "sex", "individual_nationality": "nationality",
}


@dataclass
class DeterministicResult:
    findings: list[CrossDocFinding] = field(default_factory=list)
    consistent: list[str] = field(default_factory=list)


def _text(value: object) -> str:
    return " ".join(mask_identifiers(str(value)).split())[:160]


def _plain(value: object) -> str:
    return " ".join(str(value).split()).lower()


def _label(context: CaseContext, document_id: str) -> str:
    """"the credit report", or with its file name when the case has several documents of that type."""
    view = context.by_id(document_id)
    if view is None:
        return "another document"
    label = _TYPE_LABELS.get(view.document_type, view.document_type.replace("_", " "))
    same_type = sum(1 for d in context.docs.values() if d.document_type == view.document_type)
    return f"the {label}" + (f" ({view.filename})" if same_type > 1 else "")


def _names(context: CaseContext, document_ids: list[str]) -> str:
    labels = list(dict.fromkeys(_label(context, i) for i in document_ids))
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _sentence(context: CaseContext, what: str, sides: list[tuple[object, list[str]]]) -> str:
    """'The ID number is "A" on the credit report, but "B" on the NRIC and the certificate of incumbency.'"""
    ordered = sorted(sides, key=lambda side: len(side[1]))  # the odd one out first
    return f"{what} is " + ", but ".join(f'"{_text(v)}" on {_names(context, ids)}' for v, ids in ordered) + "."


def _finding(
    context: CaseContext, n: int, summary: str, why: str, flag: str, entries: list[tuple[str, str, object]],
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
    files = ", ".join(view.filename for i in document_ids if (view := context.by_id(i)) is not None)
    identity = flag == _IDENTIFIER
    return CrossDocFinding(
        id=f"xd-det-{n}",
        hypothesis_id="deterministic",
        summary=summary[:400],
        rationale=f"{why} Documents: {files}.",
        severity="high" if identity else "medium",
        category=FindingCategory.IDENTITY_ENTITY_CONFLICT if identity else FindingCategory.CROSS_DOCUMENT_VARIANCE,
        confidence=1.0,
        citations=list(dict.fromkeys(citations)),
        document_ids=document_ids,
        breach_flags=[flag],
    )


def _company_names(context: CaseContext, out: DeterministicResult, add) -> None:
    # One finding per pair of differing names, citing every document that carries either name.
    pairs: dict[frozenset[str], dict[str, tuple[str, list[str]]]] = {}
    compared = 0
    for record in context.records:
        result = check_company_name_consistency(record, context.records)
        if result.skipped_reason:
            continue
        compared += 1
        for item in result.discrepancies:
            key = frozenset({_plain(item.stated), _plain(item.conflicting)})
            sides = pairs.setdefault(key, {})
            for value, ids in ((item.stated, [record.id]), (item.conflicting, item.document_ids)):
                shown, held = sides.setdefault(_plain(value), (value, []))
                held.extend(i for i in ids if i not in held)
    for sides in pairs.values():
        listed = list(sides.values())
        add(
            _sentence(context, "The company name", [(v, ids) for v, ids in listed]), _WHY["name"], _VARIANCE["name"],
            [(i, "company_name", v) for v, ids in listed for i in ids],
        )
    if compared >= 2 and not pairs:
        out.consistent.append(f"Company name is the same across {compared} documents")


# The date a proof of address speaks for: a statement's or bill's period end, its own date, an NRIC's issue date.
_PROOF_DATE_FIELDS = (
    "statement_period_end", "billing_period_end", "bill_date", "statement_date", "document_date", "date_of_issue",
)


def _proof_date(context: CaseContext, document_id: str) -> date | None:
    """When a document that counts as personal proof of address was issued, or None (not a proof, or undated)."""
    view = context.by_id(document_id)
    if view is None or not counts_as_address_proof(view.document_type, view.fields):
        return None
    for name in _PROOF_DATE_FIELDS:
        dated = parse_date(decode_field_value(view.fields.get(name)))
        if dated is not None:
            return dated
    return None


def _previous_address(context: CaseContext, sides: list[tuple[object, list[str]]]) -> tuple | None:
    """
    (old address, its documents, current address, the newer documents) when a person's two addresses read as a
    move rather than a conflict: every document with the old address is a dated proof of address, and a proof of
    address dated after all of them shows the other one. Any doubt (an undated document, a non-proof such as a
    credit report carrying the old address, no newer proof) keeps the difference a finding.
    """
    if len(sides) != 2:
        return None
    for (old_value, old_ids), (current_value, current_ids) in (sides, sides[::-1]):
        old_dates = [_proof_date(context, i) for i in old_ids]
        if not old_dates or any(d is None for d in old_dates):
            continue
        newer = [i for i in current_ids if (d := _proof_date(context, i)) is not None and d > max(old_dates)]
        if newer:
            return old_value, old_ids, current_value, newer
    return None


def _addresses(context: CaseContext, out: DeterministicResult, add) -> None:
    mismatches = find_address_mismatches(r for r in context.records if r.extracted_fields)
    # One finding per pair of differing addresses (ignoring case, punctuation and abbreviations).
    pairs: dict[tuple, dict] = {}
    for m in mismatches:
        key = (m.first.field_name.split("_")[0] == "company", frozenset({normalize_address(m.first.value), normalize_address(m.second.value)}))
        entry = pairs.setdefault(key, {"company": key[0], "sides": {}, "fields": [], "holders": []})
        for record in (m.first, m.second):
            side = entry["sides"].setdefault(normalize_address(record.value), (record.value, []))
            if record.document_id not in side[1]:
                side[1].append(record.document_id)
            entry["fields"].append((record.document_id, record.field_name, record.value))
            if record.holder:
                entry["holders"].append(record.holder)
    for entry in pairs.values():
        # Name whose address it is, so neither the officer nor the evidence request reads it as the company's.
        # The name as printed in mixed case where a document has it, rather than a block-capital copy.
        holder = next((h for h in entry["holders"] if not h.isupper()), entry["holders"][0] if entry["holders"] else "")
        what = "The company's address" if entry["company"] else (f"{_text(holder)}'s address" if holder else "The address")
        if not entry["company"]:
            moved = _previous_address(context, list(entry["sides"].values()))
            if moved is not None:
                old_value, old_ids, current_value, newer_ids = moved
                out.consistent.append(
                    f'{what} "{_text(old_value)}" on {_names(context, old_ids)} is a previous address: '
                    f'{_names(context, newer_ids)}, dated later, shows "{_text(current_value)}"'
                )
                continue
        why = _WHY["address"] if entry["company"] else _WHY["personal_address"]
        add(_sentence(context, what, list(entry["sides"].values())), why, _VARIANCE["address"], entry["fields"])
    if not mismatches and len(context.records) >= 2:
        out.consistent.append("No address differs between documents for the same person or company")


def _uens(context: CaseContext, out: DeterministicResult, add) -> None:
    by_value: dict[str, list[str]] = {}
    for view in context.docs.values():
        raw = decode_field_value(view.fields.get("uen"))
        normalised = uen_key(str(raw)) if raw not in (None, "") else None
        if normalised:
            by_value.setdefault(normalised, []).append(view.id)
    values = list(by_value)
    for i, left in enumerate(values):
        for right in values[i + 1:]:
            add(
                _sentence(context, "The company's UEN", [(left, by_value[left]), (right, by_value[right])]),
                _WHY["uen"], _IDENTIFIER,
                [*((d, "uen", left) for d in by_value[left]), *((d, "uen", right) for d in by_value[right])],
            )
    if len(values) == 1 and len(by_value[values[0]]) >= 2:
        out.consistent.append(f"UEN is the same on {len(by_value[values[0]])} documents")


def _people(context: CaseContext, out: DeterministicResult, add) -> None:
    # One finding per pair of differing values, citing every document that carries either, so a wrong ID that
    # disagrees with an NRIC and with a certificate of incumbency is one problem, not two.
    differences: dict[tuple, dict] = {}
    for left_key, right_key in candidate_pairs(context):
        left, right = context.docs[left_key], context.docs[right_key]
        comparison = compare_pair(left, right)
        for m in comparison.mismatches:
            key = (m.check, frozenset({_plain(m.left_value), _plain(m.right_value)}))
            entry = differences.setdefault(key, {"check": m.check, "values": {}})
            for view, field_name, value in ((left, m.left_field, m.left_value), (right, m.right_field, m.right_value)):
                holder = entry["values"].setdefault(_plain(value), {"shown": value, "docs": {}})
                holder["docs"].setdefault(view.id, field_name)
        for compared, check, wanted in (
            (comparison.ids_compared, "ID number", "individual_id"), (comparison.names_compared, "name", "individual_name"),
        ):
            if compared and not any(m.check == wanted for m in comparison.mismatches):
                out.consistent.append(
                    f"The {check} is the same on {_names(context, [left.id])} and {_names(context, [right.id])}")
    for entry in differences.values():
        label = _PERSON_LABELS.get(entry["check"], "name")
        # An ID number or a date of birth that differs is a different identity; the other details are a variance.
        hard = entry["check"] in ("individual_id", "individual_dob")
        sides = list(entry["values"].values())
        add(
            _sentence(context, f"The person's {label}" if label != "ID number" else "The ID number",
                      [(side["shown"], list(side["docs"])) for side in sides]),
            _WHY["id"] if entry["check"] == "individual_id" else _WHY["person"],
            _IDENTIFIER if hard else _VARIANCE["person"],
            [(i, field_name, side["shown"]) for side in sides for i, field_name in side["docs"].items()],
        )
    out.consistent[:] = list(dict.fromkeys(out.consistent))


_MISMATCH_FLAGS = frozenset({"NAME_MISMATCH", "ADDRESS_MISMATCH", "IDENTIFIER_CONFLICT", "PERSON_MISMATCH"})


def restates_a_code_finding(code_findings: list[CrossDocFinding], finding: CrossDocFinding) -> bool:
    """
    Whether an agent's finding only says again what a code comparison already found: every flag it carries is
    a mismatch flag, and a code finding with that flag cites one of the same documents.
    """
    flags = set(finding.breach_flags)
    if not flags or not flags <= _MISMATCH_FLAGS:
        return False
    documents = set(finding.document_ids)
    return all(
        any(flag in code.breach_flags and documents & set(code.document_ids) for code in code_findings)
        for flag in flags
    )


def run_deterministic_checks(context: CaseContext) -> DeterministicResult:
    """All case-wide comparisons, one finding per difference. Never raises: a failed check is skipped."""
    out = DeterministicResult()

    def add(summary: str, why: str, flag: str, entries: list[tuple[str, str, object]]) -> None:
        out.findings.append(_finding(context, len(out.findings) + 1, summary, why, flag, entries))

    for step in (_company_names, _addresses, _uens, _people):
        try:
            step(context, out, add)
        except Exception:
            logger.exception("Deterministic check %s failed", step.__name__)
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
    display: dict[str, str] = {}
    for finding in result.findings:
        entries = [context.ledger.refs[c] for c in finding.citations if c in context.ledger.refs]
        flag = finding.breach_flags[0] if finding.breach_flags else ""
        if len(entries) < 2 or flag not in _LABELS or has_protected_text(finding.summary):
            continue  # a difference in a personal detail is code's finding alone; the agent is never told it
        # Values that differ only in case, spacing or line breaks are one value.
        shown = {_plain(_text(e["value"])): _text(e["value"]) for e in reversed(entries)}
        values = [_plain(_text(e["value"])) for e in entries]
        display.update(shown)
        for entry, value in zip(entries, values):
            view = context.docs.get(entry["doc_key"])
            holders.setdefault(flag, {}).setdefault(value, set()).add(view.filename if view else entry["doc_key"])
        edges.setdefault(flag, []).extend((values[0], other) for other in values[1:])
    differs = []
    for flag, flag_edges in edges.items():
        for group in _components(flag_edges):
            ordered = sorted(group, key=lambda v: (-len(holders[flag][v]), v))
            parts = [f'{_holders(holders[flag][v])} "{display[v]}"' for v in ordered]
            differs.append(f"- DIFFERS ({_LABELS[flag]}): " + "; ".join(parts))
    lines = [line[:_LINE_CHARS] for line in differs[:MAX_BRIEF_LINES]]
    if len(differs) > MAX_BRIEF_LINES:
        lines.append(f"- ... and {len(differs) - MAX_BRIEF_LINES} more differences")
    lines += [f"- SAME: {c}"[:_LINE_CHARS] for c in result.consistent[:MAX_BRIEF_LINES]]
    return "\n".join(lines)
