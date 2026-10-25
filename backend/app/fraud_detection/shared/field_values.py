"""Every text value in a document's extracted fields, nested ones included.

Extraction sometimes returns a field as a list or as rows of objects (a shareholder table:
[{"name": ..., "address": ..., "identification_no": ...}]), and sometimes as a JSON string
of one. Checks that look for a kind of value anywhere in the document (NRICs, addresses)
use this so a value inside a list or row is found like a top-level one.

Each value comes with its path ("shareholders[1].address") for the finding's field, and
its key ("address"): the name of the field or column it sits under, which says what it is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator

# Deeper nesting than a table of rows inside a field isn't produced by extraction.
_MAX_DEPTH = 4


@dataclass(frozen=True)
class TextValue:
    path: str
    key: str
    text: str
    top_level: bool


def decode_field_value(value: Any) -> Any:
    """An extracted value that is itself JSON (extraction sometimes encodes it twice), decoded once."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _decoded(value: Any) -> Any:
    """Like decode_field_value, but a text value stays text ("123" isn't turned into a number)."""
    decoded = decode_field_value(value)
    return decoded if isinstance(decoded, (str, list, dict)) else value


def _walk(value: Any, path: str, key: str, depth: int) -> Iterator[TextValue]:
    value = _decoded(value)
    if isinstance(value, str):
        if value.strip():
            yield TextValue(path=path, key=key, text=value, top_level=depth == 0)
    elif depth >= _MAX_DEPTH:
        return
    elif isinstance(value, dict):
        for child_key, child in value.items():
            yield from _walk(child, f"{path}.{child_key}", str(child_key), depth + 1)
    elif isinstance(value, (list, tuple)):
        # A list's items are what its field names: the items of individual_address are addresses.
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]", key, depth + 1)


def text_values(fields: dict[str, Any]) -> list[TextValue]:
    """Every non-empty text value, top-level fields first (in field order), then nested ones."""
    found = [v for name, value in fields.items() for v in _walk(value, name, name, 0)]
    return sorted(found, key=lambda v: not v.top_level)


# Same as the frontend's field labels (utils/fieldLabels.ts): Title Case, these short words in capitals.
_ACRONYMS = frozenset({"id", "uen", "tin", "nric", "fin", "gst", "bic"})
_PATH_STEP_RE = re.compile(r"\[(\d+)\]|\.([^.\[]+)")


def _humanize(key: str) -> str:
    return " ".join(w.upper() if w.lower() in _ACRONYMS else w.capitalize() for w in key.split("_") if w)


def _singular(label: str) -> str:
    if label.endswith("ies"):
        return label[:-3] + "y"
    return label[:-1] if label.endswith("s") and not label.endswith("ss") else label


def _row_name(row: Any) -> str | None:
    """Who a table row is about: its name column, if it has one."""
    if not isinstance(row, dict):
        return None
    name = next((v for k, v in row.items() if "name" in str(k).lower() and isinstance(v, str) and v.strip()), None)
    return name.strip() if name else None


def also_printed_in(fields: dict[str, Any], paths: list[str]) -> str:
    """'. Also printed in A and B' for a value's other locations, or '' if there are none."""
    labels = [describe_path(fields, p) for p in paths]
    if not labels:
        return ""
    listed = labels[0] if len(labels) == 1 else f"{'; '.join(labels[:-1])} and {labels[-1]}"
    return f". Also printed in {listed}"


def describe_path(fields: dict[str, Any], path: str) -> str:
    """A path as an officer would say it, in the frontend's field-label style:
    "shareholders[1].identification_no" -> "Shareholder 2 (NG BEE LENG), Identification No",
    "individual_address[0]" -> "Individual Address 1", "nric_number" -> "NRIC Number"."""
    top = re.match(r"[^.\[]+", path)
    if top is None:
        return path
    label = _humanize(top.group(0))
    value = _decoded(fields.get(top.group(0)))
    for index, key in _PATH_STEP_RE.findall(path[top.end():]):
        if index:
            position = int(index)
            value = _decoded(value[position]) if isinstance(value, list) and position < len(value) else None
            name = _row_name(value)
            label = f"{_singular(label)} {position + 1}" + (f" ({name})" if name else "")
        else:
            value = _decoded(value.get(key)) if isinstance(value, dict) else None
            label = f"{label}, {_humanize(key)}"
    return label
