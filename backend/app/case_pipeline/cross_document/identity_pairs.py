"""
The same individual named on two documents: which pairs of documents name one, and where the pair disagrees.
Moved out of the per-document scan: comparing two documents is cross-document work, and a mismatch is one
fact about the pair, reported once. Linking uses case_match's rules (a person is linked only to their single
best match on the other document).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...case_match import COMPANY_ONLY_DOCUMENT_TYPES, Person, individual_names_match, linked_people, normalize_id
from ...models import DocumentType
from .evidence import CaseContext, DocView

# The certificate of incumbency is the case's reference: its people are compared by the case-match check.
_SKIPPED_TYPES = COMPANY_ONLY_DOCUMENT_TYPES | {DocumentType.CERTIFICATE_OF_INCUMBENCY.value}
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


def compare_pair(left: DocView, right: DocView) -> PairComparison:
    """The people the two documents share and the differences in their ID numbers or names."""
    result = PairComparison()
    for a, b in _links(left, right):
        result.linked += 1
        id_a, id_b = normalize_id(a.id_number), normalize_id(b.id_number)
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
