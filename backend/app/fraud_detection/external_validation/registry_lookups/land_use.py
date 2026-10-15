"""URA Master Plan land-use zoning: what a location is zoned for.

Source: URA's "Amendment to Master Plan 2019 Land Use layer" on data.gov.sg (the latest
version of the Master Plan's land-use polygons, kept up to date by URA). Each polygon has a
zone, e.g. RESIDENTIAL, COMMERCIAL, BUSINESS 2, WHITE. What each zone allows is URA's
Master Plan Written Statement (Table 1, "Zoning Interpretation").

The layer is a ~175 MB GeoJSON, too big to fetch per check, so it's turned into a compact
index (numpy arrays, ~12 MB) under backend/data/land_use/.

* Deployed (Docker): the image build makes the index (backend/Dockerfile) and sets
  LAND_USE_RUNTIME_REBUILD=false, so a running server never downloads or builds it; a new map
  arrives with the next deploy.
* Locally: the index is built in a background thread the first time a lookup needs it, and
  rebuilt when data.gov.sg's catalogue says the layer has been updated; lookups meanwhile use
  the index already on disk, or are skipped (with the reason) when there is none yet. Or run
  `python -m scripts.build_land_use_index` to build it ahead of time.

Zoning is planned use, not actual use, and it's looked up at the postal code's map point, so a
building beside a zone boundary can land in the neighbouring zone: the checks using it
report signals and inconsistencies, never impossibilities.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from ....config import settings
from .datagov import dataset_last_updated
from .http import LookupUnavailable

logger = logging.getLogger("fraud_detection.registry_lookups")

SOURCE = "URA Master Plan land use (data.gov.sg)"
LAND_USE_DATASET = "d_c0e06af6b1a36e6a82223f67c1e17fbd"  # "Amendment to Master Plan 2019 Land Use layer"
_POLL_DOWNLOAD_URL = "https://api-open.data.gov.sg/v1/public/api/datasets/{dataset}/poll-download"
_METADATA_URL = "https://api-production.data.gov.sg/v2/public/api/datasets/{dataset}/metadata"
INDEX_DIR = Path(__file__).resolve().parents[4] / "data" / "land_use"
_INDEX_FILE = INDEX_DIR / "land_use_index.npz"
_META_FILE = INDEX_DIR / "land_use_index.json"
# How often a lookup may ask the catalogue whether the layer has been updated.
_FRESHNESS_CHECK_SECONDS = 24 * 3600


@dataclass(frozen=True)
class LandUseIndex:
    zones: list[str]
    zone_of: np.ndarray       # polygon -> index into zones
    bboxes: np.ndarray        # polygon -> (min lon, min lat, max lon, max lat), float32
    ring_start: np.ndarray    # polygon -> first ring (polygons' rings are contiguous)
    point_start: np.ndarray   # ring -> first point (rings' points are contiguous)
    points: np.ndarray        # (lon, lat), float32: about a metre in Singapore, plenty for zoning
    last_updated: str

    def zones_at(self, lon: float, lat: float) -> list[str]:
        """Every zone whose polygon contains the point (normally exactly one)."""
        b = self.bboxes
        candidates = np.nonzero((b[:, 0] <= lon) & (lon <= b[:, 2]) & (b[:, 1] <= lat) & (lat <= b[:, 3]))[0]
        found = []
        for polygon in candidates:
            inside = False
            for ring in range(self.ring_start[polygon], self.ring_start[polygon + 1]):
                pts = self.points[self.point_start[ring]:self.point_start[ring + 1]]
                x0, y0 = pts[:-1, 0], pts[:-1, 1]
                x1, y1 = pts[1:, 0], pts[1:, 1]
                straddles = (y0 > lat) != (y1 > lat)
                with np.errstate(divide="ignore", invalid="ignore"):
                    x_cross = x0 + (lat - y0) * (x1 - x0) / (y1 - y0)
                # Even-odd rule across every ring, so holes cancel out.
                inside ^= bool(np.count_nonzero(straddles & (lon < x_cross)) % 2)
            if inside:
                found.append(self.zones[int(self.zone_of[polygon])])
        return found


def build_index(geojson: dict, last_updated: str) -> LandUseIndex:
    zones: list[str] = []
    zone_ids: dict[str, int] = {}
    zone_of, bboxes, ring_start, point_start, points = [], [], [0], [0], []
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        zone = str((feature.get("properties") or {}).get("LU_DESC") or "").strip().upper()
        if not zone:
            continue
        if geometry.get("type") == "Polygon":
            polygons = [geometry["coordinates"]]
        elif geometry.get("type") == "MultiPolygon":
            polygons = geometry["coordinates"]
        else:
            continue
        zone_id = zone_ids.setdefault(zone, len(zones))
        if zone_id == len(zones):
            zones.append(zone)
        for rings in polygons:
            ring_points = [np.asarray(ring, dtype=np.float64)[:, :2] for ring in rings if len(ring) >= 4]
            if not ring_points:
                continue
            outer = ring_points[0]
            zone_of.append(zone_id)
            bboxes.append((outer[:, 0].min(), outer[:, 1].min(), outer[:, 0].max(), outer[:, 1].max()))
            for ring in ring_points:
                points.append(ring)
                point_start.append(point_start[-1] + len(ring))
            ring_start.append(ring_start[-1] + len(ring_points))
    return LandUseIndex(
        zones=zones, zone_of=np.asarray(zone_of, dtype=np.uint8), bboxes=np.asarray(bboxes, dtype=np.float32),
        ring_start=np.asarray(ring_start, dtype=np.int64), point_start=np.asarray(point_start, dtype=np.int64),
        points=(np.concatenate(points) if points else np.zeros((0, 2))).astype(np.float32), last_updated=last_updated,
    )


def save_index(index: LandUseIndex) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _INDEX_FILE.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, zone_of=index.zone_of, bboxes=index.bboxes, ring_start=index.ring_start,
                        point_start=index.point_start, points=index.points)
    tmp.replace(_INDEX_FILE)
    _META_FILE.write_text(json.dumps({"zones": index.zones, "last_updated": index.last_updated,
                                      "dataset": LAND_USE_DATASET, "built_at": datetime.now().isoformat()}))


def load_index() -> LandUseIndex | None:
    if not (_INDEX_FILE.exists() and _META_FILE.exists()):
        return None
    meta = json.loads(_META_FILE.read_text())
    with np.load(_INDEX_FILE) as arrays:
        return LandUseIndex(zones=meta["zones"], last_updated=meta["last_updated"],
                            **{name: arrays[name] for name in ("zone_of", "bboxes", "ring_start", "point_start", "points")})


def download_and_build(last_updated: str | None = None) -> LandUseIndex:
    """Download the layer from data.gov.sg and build and save its index (minutes; ~700 MB while parsing)."""
    headers = {"x-api-key": settings.DATA_GOV_SG_API_KEY} if settings.DATA_GOV_SG_API_KEY else None
    with httpx.Client(timeout=600, headers=headers) as client:
        if last_updated is None:
            metadata = client.get(_METADATA_URL.format(dataset=LAND_USE_DATASET)).json()
            last_updated = (metadata.get("data") or {}).get("lastUpdatedAt")
        poll = client.get(_POLL_DOWNLOAD_URL.format(dataset=LAND_USE_DATASET)).json()
        url = (poll.get("data") or {}).get("url")
        if not url:
            raise LookupUnavailable(f"data.gov.sg gave no download link for {LAND_USE_DATASET}: {poll}")
        response = client.get(url)
        response.raise_for_status()
    index = build_index(response.json(), last_updated or datetime.now().isoformat())
    save_index(index)
    return index


_lock = threading.Lock()
_state: dict[str, Any] = {"index": None, "loaded": False, "building": False, "checked_at": 0.0}


def _build_in_background(last_updated: str | None) -> None:
    def run() -> None:
        try:
            index = download_and_build(last_updated)
            with _lock:
                _state["index"] = index
            logger.info("Land-use index built (layer updated %s)", index.last_updated)
        except Exception:  # a failed build leaves the old index in place; the next check retries
            logger.exception("Building the land-use index failed")
        finally:
            with _lock:
                _state["building"] = False

    with _lock:
        if _state["building"]:
            return
        _state["building"] = True
    threading.Thread(target=run, name="land-use-index", daemon=True).start()


async def current_index() -> LandUseIndex:
    """The land-use index, loading it from disk once; raises LookupUnavailable while there is none yet.
    Starts a background (re)build when there is no index or data.gov.sg has a newer layer, unless
    settings.LAND_USE_RUNTIME_REBUILD is off (the Docker image, which has the index built in)."""
    with _lock:
        if not _state["loaded"]:
            _state["index"] = load_index()
            _state["loaded"] = True
        index: LandUseIndex | None = _state["index"]
    if not settings.LAND_USE_RUNTIME_REBUILD:
        if index is None:
            raise LookupUnavailable("the URA land-use index isn't included in this deployment (built at image build "
                                    "time); zoning is checked once a deploy includes it")
        return index
    with _lock:
        due = time.monotonic() - _state["checked_at"] > _FRESHNESS_CHECK_SECONDS
        if due:
            _state["checked_at"] = time.monotonic()
    if index is None or due:
        try:
            published = await dataset_last_updated(LAND_USE_DATASET)
        except LookupUnavailable:
            published = None
        if index is None or (published and published > index.last_updated):
            _build_in_background(published)
    if index is None:
        raise LookupUnavailable("the URA land-use index is being built (first use); zoning is checked once it's ready")
    return index


async def zone_at(lon: float, lat: float) -> str | None:
    """The zone at a point, or None if it's in no polygon (or several disagreeing ones, at a boundary)."""
    found = set((await current_index()).zones_at(lon, lat))
    return found.pop() if len(found) == 1 else None
