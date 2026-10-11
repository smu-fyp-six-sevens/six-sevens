"""HDB Property Information (data.gov.sg, published by HDB): what an HDB block is used for.

Each block has residential / commercial flags. Only HDB blocks are listed: a private
building isn't, so its use stays unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .datagov import datastore_search

SOURCE = "HDB Property Information (data.gov.sg)"
HDB_PROPERTY_DATASET = "d_17f5382f26140b1fdae0ba2ef6239d2f"


@dataclass(frozen=True)
class HdbBlock:
    block: str
    street: str
    residential: bool
    commercial: bool


def street_tokens(street: str) -> list[str]:
    return re.sub(r"[^A-Z0-9 ]", "", street.upper()).split()


def abbreviates(short: str, full: str) -> bool:
    """HDB abbreviates street words (ST, AVE, CTRL, NTH, C'WEALTH): the short form's
    letters appear in order in the full word, starting with the same letter."""
    if short == full:
        return True
    if not short or not full or short[0] != full[0] or short.isdigit() or full.isdigit():
        return False
    rest = iter(full[1:])
    return all(ch in rest for ch in short[1:])


def same_street(hdb_street: str, road_name: str) -> bool:
    short, full = street_tokens(hdb_street), street_tokens(road_name)
    return len(short) == len(full) and all(abbreviates(s, f) for s, f in zip(short, full))


async def hdb_block(block: str, road_name: str) -> HdbBlock | None:
    for row in await datastore_search(HDB_PROPERTY_DATASET, {"blk_no": block.upper()}, limit=100):
        if same_street(str(row.get("street", "")), road_name):
            return HdbBlock(
                block=str(row.get("blk_no")), street=str(row.get("street")),
                residential=row.get("residential") == "Y", commercial=row.get("commercial") == "Y",
            )
    return None
