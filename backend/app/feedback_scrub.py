"""
Officer feedback with the confidential details taken out, so it can be shown to the agents on other cases.

A note or finding summary can carry whatever the officer typed or the document said: an applicant's name, an NRIC,
an account or phone number, an address. `scrub` replaces each with a plain placeholder, keeping the rest of the
sentence. The names the case itself holds (the company, the applicant) are passed in and removed wherever they appear.
"""

from __future__ import annotations

import re

from .fraud_detection.shared.field_values import decode_field_value

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)
# A company's UEN: 9 or 10 characters, either 8 digits and a letter, or a year-led registry number.
_UEN = re.compile(r"\b(?:\d{8,9}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z])\b", re.IGNORECASE)
# Blocks and streets: "Blk 22 Bedok South Ave 1", "12 Orchard Road", "#05-01".
_BLOCK = re.compile(r"\b(?:blk|block)\s*\d+[A-Z]?(?:\s+[A-Za-z]+){0,5}(?:\s+\d+)?", re.IGNORECASE)
_STREET = re.compile(
    r"\b\d+[A-Z]?\s+(?:[A-Z][A-Za-z']+\s+){0,3}"
    r"(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Lane|Ln|Crescent|Close|Way|Walk|Boulevard|Blvd|Place|Pl|Terrace|Rise|View)\b"
    r"(?:\s+\d+)?",
)
_UNIT = re.compile(r"#\d{1,3}-\d{1,4}[A-Z]?")
_POSTAL = re.compile(r"\b(?:singapore\s+)?\d{6}\b", re.IGNORECASE)
_DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
# Money: with a currency ("S$ 5,000", "SGD 1200"), with thousands separators, or with cents.
_AMOUNT = re.compile(
    r"(?:\b(?:SGD|USD|S\$|US\$)|\$)\s?\d[\d,]*(?:\.\d{1,2})?\b"
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?\b|\b\d+\.\d{2}\b",
    re.IGNORECASE,
)
# Phone numbers and account numbers: runs of digits, spaces and dashes with at least 8 digits.
_NUMBER_RUN = re.compile(r"\+?\d[\d -]{6,}\d")
# Fields that name a person or a company, whatever the document type calls them.
_NAME_KEYS = ("name", "employer", "holder", "director", "shareholder", "payee", "payer", "company", "signatory")
# Words of a company name that say nothing about which company: never scrubbed on their own.
_GENERIC_WORDS = {
    "PTE", "LTD", "PRIVATE", "LIMITED", "LLP", "INC", "CORP", "CORPORATION", "CO", "COMPANY", "HOLDINGS", "GROUP",
    "TRADING", "SERVICES", "ENTERPRISE", "ENTERPRISES", "INTERNATIONAL", "SINGAPORE", "ASIA", "PACIFIC", "BANK",
    "AND", "THE", "OF", "BIN", "BINTE", "BTE", "D/O", "S/O",
}


def _digits(match: re.Match[str]) -> str:
    return "[number]" if sum(ch.isdigit() for ch in match.group(0)) >= 8 else match.group(0)


def _name_parts(names: list[str] | None) -> list[str]:
    """Each name whole, then each distinctive word of it ("Wei" of "Tan Wei Ming"), longest first."""
    whole = {n.strip() for n in names or [] if n and len(n.strip()) >= 3}
    words = {w for n in whole for w in re.split(r"[^\w'/-]+", n) if len(w) >= 3 and w.upper() not in _GENERIC_WORDS}
    return sorted(whole | words, key=len, reverse=True)


def scrub(text: str | None, names: list[str] | None = None) -> str | None:
    """`text` with confidential details replaced by placeholders; None and blank stay None."""
    if text is None or not text.strip():
        return None
    out = text
    # Names first, longest first, so "Acme Holdings Pte Ltd" goes whole rather than leaving "Pte Ltd".
    for name in _name_parts(names):
        out = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", "[name]", out, flags=re.IGNORECASE)
    for pattern, placeholder in (
        (_EMAIL, "[email]"), (_NRIC, "[id]"), (_UEN, "[id]"), (_BLOCK, "[address]"), (_STREET, "[address]"),
        (_UNIT, "[address]"), (_POSTAL, "[address]"), (_DATE, "[date]"), (_AMOUNT, "[amount]"),
    ):
        out = pattern.sub(placeholder, out)
    out = _NUMBER_RUN.sub(_digits, out)
    return out.strip()


def _strings(value: object) -> list[str]:
    """Every string in an extracted value, through lists and objects (a list of people, JSON-encoded or not)."""
    value = decode_field_value(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, dict):
        return [s for key, item in value.items() if key != "id_number" for s in _strings(item)]
    return []


def names_in(fields: dict | None, extra: list[str | None] | None = None) -> list[str]:
    """The names a document's extracted fields hold (any field whose name says it names a person or company), plus `extra`."""
    found: list[str] = [e for e in extra or [] if e]
    for key, value in (fields or {}).items():
        if not any(word in key.lower() for word in _NAME_KEYS):
            continue
        found += _strings(value.get("value") if isinstance(value, dict) and "value" in value else value)
    return found
