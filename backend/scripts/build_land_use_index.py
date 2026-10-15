"""Build the URA land-use zoning index the address checks use (registry_lookups/land_use.py).

    python -m scripts.build_land_use_index            # download from data.gov.sg (a few minutes)
    python -m scripts.build_land_use_index FILE       # from an already-downloaded GeoJSON

Without it, the index is built in the background the first time an address check needs it.
Writes backend/data/land_use/ (git-ignored).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from app.fraud_detection.registry_lookups import land_use
from app.fraud_detection.registry_lookups.datagov import dataset_last_updated


def main() -> None:
    started = time.monotonic()
    published = asyncio.run(dataset_last_updated(land_use.LAND_USE_DATASET))
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as handle:
            index = land_use.build_index(json.load(handle), published or "unknown")
        land_use.save_index(index)
    else:
        index = land_use.download_and_build(published)
    print(f"Land-use index: {len(index.zone_of)} polygons in {len(index.zones)} zones, layer updated "
          f"{index.last_updated}, built in {time.monotonic() - started:.0f}s -> {land_use.INDEX_DIR}")


if __name__ == "__main__":
    main()
