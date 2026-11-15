"""
A stand-in ACRA register for the synthetic test companies, used only when settings.SYNTHETIC_ACRA_REGISTRY is on.

The test companies' UENs are made up, so data.gov.sg's register has none of them and every document would be
flagged REG.UEN_EXISTS ("not in ACRA's register"). With the setting on, acra.lookup_entity answers those UENs from
synthetic_acra_registry.json instead: each company as the mastersheet records it (name, incorporation date,
registered address), live. The other register checks still run against that record, so a document with a forged
name, incorporation date or address is still caught. A UEN not in the file is looked up on data.gov.sg as usual.

The file is built from the mastersheet by `python -m scripts.metrics.synthetic_registry` (from backend/).
"""

from __future__ import annotations

import functools
import json
import logging
from pathlib import Path
from typing import Any

from ....identifiers import uen_key

logger = logging.getLogger(__name__)

REGISTRY_FILE = Path(__file__).with_name("synthetic_acra_registry.json")


@functools.lru_cache(maxsize=1)
def _entities() -> dict[str, dict[str, Any]]:
    if not REGISTRY_FILE.exists():
        logger.warning("SYNTHETIC_ACRA_REGISTRY is on, but %s is missing; looking UENs up on data.gov.sg", REGISTRY_FILE.name)
        return {}
    records = json.loads(REGISTRY_FILE.read_text())["entities"]
    logger.warning("SYNTHETIC_ACRA_REGISTRY is on: %d test UEN(s) are answered from %s, not ACRA",
                   len(records), REGISTRY_FILE.name)
    return {key: record for record in records if (key := uen_key(record["uen"]))}


def lookup(uen: str) -> dict[str, Any] | None:
    key = uen_key(uen)
    return _entities().get(key) if key else None
