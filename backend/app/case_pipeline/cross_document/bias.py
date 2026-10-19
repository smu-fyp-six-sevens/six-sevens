"""
What a case analysis may never reason from: a person's protected attributes
and anything that stands in for them. Enforced at four points, so no single
one has to be perfect:

1. input: these fields never appear in the dossier or in any tool result;
2. prompt: the system prompt forbids them outright (agents.py);
3. proposals: a hypothesis whose text touches one is dropped before any check runs;
4. verdicts: a verdict whose text touches one is discarded (verify.py).

Over-blocking is the intended failure: a dropped hypothesis costs a little, a
biased finding costs a person unfairly.
"""

from __future__ import annotations

import re

# A field is protected when any underscore-separated part of its name is one of these.
_PROTECTED_FIELD_PARTS = frozenset({
    "sex", "gender", "race", "ethnic", "ethnicity", "nationality", "citizenship", "religion",
    "marital", "spouse", "dob", "birth", "birthplace", "age", "disability", "salutation", "mrz",
})

_PROTECTED_TEXT_RE = re.compile(
    r"\b("
    r"race|racial|racially|ethnic\w*|religio\w*|gender\w*|sex|sexes|sexual\w*|male|female|males|females|"
    r"man|men|woman|women|boy|girl|nationalit\w*|citizen\w*|immigra\w*|"
    r"age|aged|elderly|birth\w*|born|dob|marital\w*|married|unmarried|spouse|pregnan\w*|disabl\w*|handicap\w*|"
    r"chinese|malay|indian|eurasian|caucasian|asian|african|muslim|christian|buddhist|hindu|sikh|"
    r"jewish|catholic"
    r")\b"
    # A person described by where they are from ("foreign currency" and "foreign exchange" stay allowed).
    r"|\bforeign(?:er|ers)?\b(?!\s+(?:currency|exchange|currencies|transfer|remittance|account))"
    r"|\bnon-?(?:resident|citizen|national)s?\b|\bexpat\w*|\bimmigrant\w*|\bmigrant\w*"
    # What a name or an appearance is said to imply about a person.
    r"|name\s+(?:suggests|implies|sounds|indicates|looks|appears)"
    r"|(?:sounds|looks)\s+like\s+(?:a|an)\s"
    r"|typical(?:ly)?\s+(?:a\s+)?(?:male|female|chinese|malay|indian)",
    re.IGNORECASE,
)


def is_protected_field(name: str) -> bool:
    return any(part in _PROTECTED_FIELD_PARTS for part in re.split(r"[^a-z0-9]+", str(name).lower()) if part)


def has_protected_text(text: str) -> bool:
    return bool(_PROTECTED_TEXT_RE.search(text or ""))


WITHHELD = "(details withheld: concerns a personal attribute)"


def withhold_if_protected(text: str) -> str:
    """What any model is shown of a finding: the text, or a note that it concerns a personal attribute."""
    return WITHHELD if has_protected_text(text) else text
