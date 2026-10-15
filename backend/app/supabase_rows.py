from __future__ import annotations

from typing import Any, cast


def row(mapping: Any) -> dict:
    """postgrest types a row as a loose JSON union; every row we read is a dict we wrote."""
    return cast(dict, mapping)
