"""
How a raw extracted value becomes something comparable, and who a document names.

A leaf module: it imports no matching or cross-document code. The case-match gate (is this upload
for this case?) and the cross-document checks (do the case's documents agree?) both build their keys
here, so a value is turned into a key one way only. What to do with a match or a difference stays
with each caller. The company-name key is the lenient one (suffix and "&" ignored, never typo-tolerant);
the cross-document name comparison is stricter on purpose and lives in cross_document/company_identity.py.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import NamedTuple

from .models import DocumentType

_INDIVIDUAL_NAME_FIELDS: tuple[str, ...] = ("individual_name", "account_holder_name")
# nric_number, not passport_number: the incumbency lists each person's NRIC,
# which a passport also carries (see mapper._collect_identity_state).
_INDIVIDUAL_ID_FIELDS: tuple[str, ...] = ("nric_number", "id_number")
# Role-prefixed person keys — the certificate of incumbency's extracted
# fields list each officer and shareholder as director_name,
# secretary_name, shareholder_1_name, shareholder_2_name, ... each with a
# matching <prefix>_id_number. The role list keeps out other *_name keys
# that aren't people (company_name, audit_firm_name, bank_name, ...).
_ROLE_NAME_KEY = re.compile(
    r"^(?P<prefix>(?:director|secretary|shareholder|officer|member|partner|manager)"
    r"(?:_\d+)?)_name$"
)
_ROLE_ID_SUFFIXES: tuple[str, ...] = ("_id_number", "_id", "_nric_number")
# Array fields listing several people, each an object with name/id keys —
# in case extraction returns officers/shareholders that way instead.
_PEOPLE_LIST_FIELDS: tuple[str, ...] = ("officers", "shareholders")
# The key each list entry's ID number comes under. Extraction has used
# several over time — the current incumbency schema returns
# "identification_no" (the ACRA table's "Identification No." column) — so
# accept any of them; missing it silently drops every officer's ID.
_LIST_ENTRY_ID_KEYS: tuple[str, ...] = ("id_number", "identification_no", "nric_number", "id")

# Individuals aren't checked on a constitution: its subscribers are the
# people who formed the company, who may no longer be officers or
# shareholders on the current certificate of incumbency. Its UEN/company
# is still checked.
COMPANY_ONLY_DOCUMENT_TYPES: set[str] = {DocumentType.COMPANY_CONSTITUTION.value}


# Trailing legal-form words dropped before comparing company names, so
# "ACME Pte. Ltd." and "ACME Private Limited" compare equal. Only stripped
# from the END of the name — "CO" in the middle of a name is meaningful.
_COMPANY_SUFFIXES: set[str] = {
    "PTE", "PRIVATE", "LTD", "LIMITED", "LLP", "LLC", "INC", "INCORPORATED",
    "CORP", "CORPORATION", "CO", "COMPANY", "PLC", "BHD", "SDN", "S",
}

# Singapore UEN formats: business (8 digits + letter), local company
# (9 digits + letter), and others (T/S/R + 2 digits + 2 letters + 4 digits +
# letter). Used to pull the UEN out of a value like "UEN: 201912345K".
_UEN_PATTERN = re.compile(r"\d{8,9}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z]")


def text_values(value: object) -> list[str]:
    """
    Every non-empty string in an extracted value. extracted_fields is LLM
    output, not a validated schema — a field can come back as a string, a
    list of strings, or (for an incumbency listing several people in flat
    fields) one "A; B" joined string.
    """
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[;\n]", value) if part.strip()]
    if isinstance(value, list):
        return [s for item in value if isinstance(item, str) for s in text_values(item)]
    return []


def alnum(value: object) -> str:
    """Letters and digits only, upper case: "s1234-567a" -> "S1234567A". Empty for a non-string."""
    return re.sub(r"[^A-Z0-9]", "", value.upper()) if isinstance(value, str) else ""


def uen_key(value: object) -> str | None:
    """A UEN with spaces, punctuation and any label dropped: "UEN: 2019-12345K" -> "201912345K"."""
    cleaned = alnum(value)
    found = _UEN_PATTERN.search(cleaned)
    return found.group(0) if found else cleaned or None


def id_key(value: object) -> str | None:
    """An ID number (NRIC, passport...) with spaces and punctuation dropped; None when nothing is left."""
    return alnum(value) or None


def name_tokens(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return re.sub(r"[^A-Z0-9]+", " ", value.upper()).split()


def company_tokens(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    tokens = name_tokens(value.upper().replace("&", " AND "))
    while tokens and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return tokens


def company_names_match(a: object, b: object) -> bool:
    """
    Fuzzy, but deliberately not typo-tolerant: "ACME Trading Pte Ltd" matches
    "ACME Pte Ltd" (one name is the other plus extra words, same leading
    word), but "ACNE Pte Ltd" does not — a one-letter difference in a
    company name is exactly what a document from a different company looks
    like, so edit-distance matching would defeat the point of this check.
    """
    tokens_a, tokens_b = company_tokens(a), company_tokens(b)
    if not tokens_a or not tokens_b:
        return False
    if tokens_a == tokens_b or "".join(tokens_a) == "".join(tokens_b):
        return True
    shorter, longer = sorted((tokens_a, tokens_b), key=len)
    return shorter[0] == longer[0] and set(shorter) <= set(longer)


def individual_names_match(a: object, b: object) -> bool:
    """Same words in any order — "Tan Wei Ming" matches "Wei Ming Tan"."""
    tokens_a, tokens_b = name_tokens(a), name_tokens(b)
    return bool(tokens_a) and sorted(tokens_a) == sorted(tokens_b)


def _name_similarity(a: object, b: object) -> float:
    """Similarity used only to link a name-only document to an identified person.

    Exact matching remains the decision rule. A near name can link two records
    for comparison, but any difference is then sent to manual review.
    """
    tokens_a, tokens_b = name_tokens(a), name_tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return SequenceMatcher(None, " ".join(sorted(tokens_a)), " ".join(sorted(tokens_b))).ratio()



class Person(NamedTuple):
    name: str | None
    id_number: str | None
    name_field: str
    id_field: str



def _role_prefixed_people(fields: dict) -> list[Person]:
    """
    People stored as flat, role-prefixed keys — how the certificate of
    incumbency actually comes back from extraction: director_name +
    director_id_number, secretary_name + secretary_id_number,
    shareholder_1_name + shareholder_1_id_number, shareholder_2_..., etc.
    """
    people: list[Person] = []
    for key, value in fields.items():
        found = _ROLE_NAME_KEY.match(key)
        if not found or not isinstance(value, str) or not value.strip():
            continue
        prefix = found.group("prefix")
        id_field = next(
            (f"{prefix}{suffix}" for suffix in _ROLE_ID_SUFFIXES if fields.get(f"{prefix}{suffix}")),
            f"{prefix}_id_number",
        )
        raw_id = fields.get(id_field)
        people.append(
            Person(value.strip(), raw_id if isinstance(raw_id, str) else None, key, id_field)
        )
    return people


def _list_field_people(fields: dict) -> list[Person]:
    """People stored as an array of {name, id_number} objects."""
    people: list[Person] = []
    for list_field in _PEOPLE_LIST_FIELDS:
        entries = fields.get(list_field)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") if isinstance(entry.get("name"), str) else None
            id_key = next((k for k in _LIST_ENTRY_ID_KEYS if isinstance(entry.get(k), str) and entry[k]), None)
            id_number = entry[id_key] if id_key else None
            if name or id_number:
                people.append(
                    Person(name, id_number, f"{list_field}.name", f"{list_field}.{id_key or 'id_number'}")
                )
    return people


def _individual_field_people(fields: dict) -> list[Person]:
    """
    People in the generic individual_name/id_number fields most document
    types use. A name list and an ID list of the same length are paired up
    by position; otherwise each is recorded on its own.
    """
    for name_field in _INDIVIDUAL_NAME_FIELDS:
        names = text_values(fields.get(name_field))
        if not names:
            continue
        id_field, ids = next(
            ((f, text_values(fields.get(f))) for f in _INDIVIDUAL_ID_FIELDS if fields.get(f)),
            (_INDIVIDUAL_ID_FIELDS[0], []),
        )
        if len(ids) == len(names):
            return [Person(n, i, name_field, id_field) for n, i in zip(names, ids)]
        return [
            *(Person(n, None, name_field, id_field) for n in names),
            *(Person(None, i, name_field, id_field) for i in ids),
        ]

    # IDs with no name at all, e.g. the back of an NRIC.
    for id_field in _INDIVIDUAL_ID_FIELDS:
        ids = text_values(fields.get(id_field))
        if ids:
            return [Person(None, i, _INDIVIDUAL_NAME_FIELDS[0], id_field) for i in ids]
    return []


def document_people(fields: dict) -> list[Person]:
    """
    Every distinct individual a document names, from whichever shape the
    extraction used. The same person often appears several times — on the
    incumbency sample, the director is also shareholder_1 and the
    individual_name — so entries are de-duplicated by ID number, or by name
    for an entry with no ID.
    """
    people: list[Person] = []
    seen_ids: set[str] = set()
    seen_names: set[tuple[str, ...]] = set()
    for person in (
        # Lists first: they're the complete set (every officer/shareholder,
        # with IDs), so a flat duplicate without an ID is recognised and dropped.
        *_list_field_people(fields),
        *_role_prefixed_people(fields),
        *_individual_field_people(fields),
    ):
        id_number = id_key(person.id_number)
        name = tuple(sorted(name_tokens(person.name)))
        if id_number in seen_ids or (id_number is None and name and name in seen_names):
            continue
        if id_number:
            seen_ids.add(id_number)
        if name:
            seen_names.add(name)
        people.append(person)
    return people


def _person_link_score(a: Person, b: Person) -> float:
    """Whether two extracted person records could refer to the same person.

    Equal IDs are strongest. Exact names also link records, including when
    their IDs conflict so the ID difference can be reported. When one side has
    no ID, a close name can link the records for review; it never makes the
    values a match.
    """
    id_a, id_b = id_key(a.id_number), id_key(b.id_number)
    if id_a and id_b:
        if id_a == id_b:
            return 1.0
        return 1.0 if individual_names_match(a.name, b.name) else 0.0

    if individual_names_match(a.name, b.name):
        return 1.0
    return _name_similarity(a.name, b.name)


def _unique_person_pairs(
    left: list[Person], right: list[Person], *, min_score: float = 0.86
) -> list[tuple[Person, Person]]:
    """Link only reciprocal, unambiguous best candidates across two documents."""
    scores = [[_person_link_score(a, b) for b in right] for a in left]
    left_best: dict[int, int] = {}
    right_best: dict[int, int] = {}

    for i, row in enumerate(scores):
        best = max(row, default=0.0)
        if best < min_score or row.count(best) != 1:
            continue
        left_best[i] = row.index(best)

    for j in range(len(right)):
        column = [scores[i][j] for i in range(len(left))]
        best = max(column, default=0.0)
        if best < min_score or column.count(best) != 1:
            continue
        right_best[j] = column.index(best)

    return [
        (left[i], right[j])
        for i, j in left_best.items()
        if right_best.get(j) == i
    ]




def linked_people(left_fields: dict, right_fields: dict) -> list[tuple[Person, Person]]:
    """The people two documents both name, linked only when each is the other's single best match."""
    return _unique_person_pairs(document_people(left_fields), document_people(right_fields))


def name_key(value: object) -> str | None:
    """A person's name as the same words in any order: "Wei Ming, Tan" and "TAN WEI MING" share a key."""
    return " ".join(sorted(name_tokens(value))) or None


def mentions_company(text: object, company: object) -> bool:
    """
    Whether free text (a statement line) names the company, its legal suffix written any way: "ACME Private Limited"
    is named in "TRANSFER TO ACME PTE LTD".
    """
    wanted = company_tokens(company)
    words = name_tokens(text.upper().replace("&", " AND ")) if isinstance(text, str) else []
    if not wanted:
        return False
    return any(words[i:i + len(wanted)] == wanted for i in range(len(words) - len(wanted) + 1))


def company_key(value: object) -> str | None:
    """A company's name without its legal suffix: "ACME Pte. Ltd." and "Acme Private Limited" share a key."""
    return " ".join(company_tokens(value)) or None
