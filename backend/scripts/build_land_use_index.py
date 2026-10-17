"""Build the URA land-use zoning index the address checks use (external_validation/registry_lookups/land_use.py).

    python -m scripts.build_land_use_index            # download from data.gov.sg (about a minute)
    python -m scripts.build_land_use_index FILE       # from an already-downloaded GeoJSON

Without it, a local server builds the index in the background the first time an address check needs it.
Writes backend/data/land_use/ (git-ignored). Exits non-zero if the index can't be built.
"""

from __future__ import annotations

from app.fraud_detection.external_validation.registry_lookups.land_use import cli

if __name__ == "__main__":
    cli()
