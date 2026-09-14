"""Split mixed label+value spans so injection can keep the printed name.

OCR often returns ``Account No. XXXX-0472`` as one span. The prefix stays;
only the value is replaced. A standalone header such as ``Opening Balance``
is injectable as its own field.
"""

from __future__ import annotations

import re

_STOPWORDS = frozenset({"a", "an", "and", "for", "of", "the", "to", "your"})

# Words that typically appear in form/table labels, not in payload values.
_LABEL_WORDS = frozenset(
    {
        "account",
        "available",
        "average",
        "balance",
        "branch",
        "closing",
        "code",
        "customer",
        "date",
        "debit",
        "deposit",
        "deposits",
        "description",
        "holder",
        "information",
        "name",
        "no",
        "number",
        "opening",
        "page",
        "period",
        "statement",
        "total",
        "trans",
        "transaction",
        "type",
        "value",
        "withdrawals",
    }
) | _STOPWORDS

# Longest-first so " No. " wins over " No." and " Number: " over " Number ".
_SEPARATORS = (
    " Number: ",
    " Number ",
    " No. ",
    " NO. ",
    " No.",
    " Type: ",
    " Type ",
    " Name: ",
    " Name ",
    ": ",
    ":",
)

_LABEL_ONLY_PATTERNS = (
    re.compile(r"^ocbc(\s+bank)?$", re.I),
    re.compile(r"^statement\s+of\s+account$", re.I),
    re.compile(r"^page\s+\d+\s+of\s+\d+$", re.I),
    re.compile(r"^information$", re.I),
    re.compile(r"^check\s+your\s+statement$", re.I),
    re.compile(r"^updating\s+your\s+personal\s+particulars$", re.I),
    re.compile(r"^ocbc\s+promotion", re.I),
    re.compile(r"^revision\s+of\s+interest", re.I),
    re.compile(r"^(opening|average|closing)\s+balance$", re.I),
    re.compile(r"^available\s+balance$", re.I),
    re.compile(r"^(transaction|description|debit|deposit|balance)(\s*\$+)?$", re.I),
    re.compile(r"^dau?e(s)?(\s+\S+)?$", re.I),
    re.compile(r"^date(\s+\S+)?$", re.I),
    re.compile(r"^account\s+(no\.?|number|type|name)$", re.I),
    re.compile(r"^customer\s+name$", re.I),
    re.compile(r"^branch(\s+code|\s+name)?$", re.I),
)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]+", text.lower())


def _looks_like_label(text: str) -> bool:
    tokens = _tokens(text)
    if not tokens:
        return False
    content = [token for token in tokens if token not in _STOPWORDS]
    if not content:
        return False
    return all(token in _LABEL_WORDS for token in tokens)


def _is_label_phrase(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if any(pattern.match(stripped) for pattern in _LABEL_ONLY_PATTERNS):
        return True
    if re.search(r"\d", stripped) and not re.match(r"^page\s+\d", stripped, re.I):
        return False
    return _looks_like_label(stripped)


def split_label_and_value(text: str) -> tuple[str, str]:
    """Split a field into ``(label_prefix, value)``.

    ``label_prefix`` includes the separator and trailing spaces so
    ``label + value == text``. Label-only fields return ``(text, "")``;
    value-only fields return ``("", text)``.
    """
    if not text:
        return ("", "")
    if _is_label_phrase(text):
        return (text, "")

    lowered = text.lower()
    for sep in _SEPARATORS:
        idx = lowered.find(sep.lower())
        if idx < 0:
            continue
        label_end = idx + len(sep)
        while label_end < len(text) and text[label_end].isspace():
            label_end += 1
        label = text[:label_end]
        value = text[label_end:]
        if value.strip() and _looks_like_label(label):
            return (label, value)

    return ("", text)


def field_value_text(text: str) -> str:
    """The injectable value portion, or empty for a label-only field."""
    _, value = split_label_and_value(text)
    return value


def is_label_only(text: str) -> bool:
    return not field_value_text(text).strip()


def replace_value(text: str, new_value: str) -> str:
    """Keep a leading label prefix and substitute the value.

    Header-only fields have no value portion, so the whole string is replaced.
    """
    label, value = split_label_and_value(text)
    if not value.strip():
        return new_value
    return label + new_value


def require_value(field_name: str, text: str) -> str:
    """Return the value portion, or the full text for a header-only field."""
    value = field_value_text(text)
    if value.strip():
        return value
    return text
