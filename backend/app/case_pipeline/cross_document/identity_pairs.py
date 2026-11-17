"""
The same individual named on two documents: which pairs of documents name one, and where the pair disagrees.
Moved out of the per-document scan: comparing two documents is cross-document work, and a mismatch is one
fact about the pair, reported once. Linking uses case_match's rules (a person is linked only to their single
best match on the other document).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...identifiers import COMPANY_ONLY_DOCUMENT_TYPES, Person, id_key, individual_names_match, linked_people
from ...fraud_detection.numerical_validation.common import parse_field_date
from ...fraud_detection.shared.field_values import decode_field_value
from .evidence import CaseContext, DocView

# Every document that names a person is compared with every other (an NRIC, a credit report, a payslip, the
# certificate of incumbency's officers...). Only a constitution, which lists shareholders, is left out.
_SKIPPED_TYPES = COMPANY_ONLY_DOCUMENT_TYPES
_MAX_PAIRS = 12


@dataclass
class PersonMismatch:
    check: str  # "individual_id" or "individual_name"
    left_field: str
    right_field: str
    left_value: str
    right_value: str


@dataclass
class PairComparison:
    linked: int = 0
    # How many linked people both documents give an ID number / a name for.
    ids_compared: int = 0
    names_compared: int = 0
    mismatches: list[PersonMismatch] = field(default_factory=list)


def _comparable(view: DocView) -> bool:
    return view.document_type not in _SKIPPED_TYPES and bool(view.fields)


def _links(left: DocView, right: DocView) -> list[tuple[Person, Person]]:
    if not (_comparable(left) and _comparable(right)):
        return []
    return linked_people(dict(left.fields), dict(right.fields))


def candidate_pairs(context: CaseContext) -> list[tuple[str, str]]:
    """Pairs of documents that both name the same individual, in document order. No comparison result."""
    views = list(context.docs.values())
    pairs = [
        (left.key, right.key)
        for i, left in enumerate(views) for right in views[i + 1:]
        if _links(left, right)
    ]
    return pairs[:_MAX_PAIRS]


# Personal details two documents can both state about the one person. Compared only by code, only when both state
# them, and never shown to a model (see bias.py). A country of birth is not a nationality, so "country" is not one.
_ATTRIBUTES = (
    ("individual_dob", "date_of_birth"), ("individual_race", "race"),
    ("individual_sex", "sex"), ("individual_nationality", "nationality"),
)


def _stated(view: DocView, name: str) -> str | None:
    value = decode_field_value(view.fields.get(name))
    return str(value).strip() if value not in (None, "") and not isinstance(value, (list, dict)) else None


def _same_attribute(check: str, a: str, b: str) -> bool | None:
    """Whether two statements of one personal detail agree; None when they cannot be compared."""
    if check == "individual_dob":
        first, second = parse_field_date(a), parse_field_date(b)
        return None if first is None or second is None else first == second
    if check == "individual_sex":
        return a[:1].lower() == b[:1].lower()
    plain = [re.sub(r"[^a-z]", "", v.lower()) for v in (a, b)]
    if check == "individual_nationality":  # "Singaporean" and "SINGAPORE" are the same
        return plain[0][:4] == plain[1][:4] if min(map(len, plain)) >= 4 else plain[0] == plain[1]
    return plain[0] == plain[1]


def compare_pair(left: DocView, right: DocView) -> PairComparison:
    """The people the two documents share and the differences in their ID numbers, names and personal details."""
    result = PairComparison()
    links = _links(left, right)
    if len(links) == 1:  # a document's own personal details are only comparable when it names one person
        for check, name in _ATTRIBUTES:
            a, b = _stated(left, name), _stated(right, name)
            if a is not None and b is not None and _same_attribute(check, a, b) is False:
                result.mismatches.append(PersonMismatch(check, name, name, a, b))
    for a, b in links:
        result.linked += 1
        id_a, id_b = id_key(a.id_number), id_key(b.id_number)
        if id_a and id_b:
            result.ids_compared += 1
            if id_a != id_b:
                result.mismatches.append(
                    PersonMismatch("individual_id", a.id_field, b.id_field, a.id_number or "", b.id_number or ""))
        if a.name and b.name:
            result.names_compared += 1
            if not individual_names_match(a.name, b.name):
                result.mismatches.append(PersonMismatch("individual_name", a.name_field, b.name_field, a.name, b.name))
    return result
