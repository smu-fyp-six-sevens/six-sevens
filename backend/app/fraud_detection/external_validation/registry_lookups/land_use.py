"""URA Master Plan land-use zoning: what a location is zoned for (RESIDENTIAL, COMMERCIAL, BUSINESS 2, WHITE, ...).

Source: URA's "Amendment to Master Plan 2019 Land Use layer" on data.gov.sg, a 175 MB GeoJSON. It is turned into a
~12 MB index under backend/data/land_use/, built by streaming (ijson) so the build peaks at ~200 MB.

* Deployed: the Docker image builds the index and sets LAND_USE_RUNTIME_REBUILD=false; a new map arrives with the next deploy.
* Locally: built in the background on first use, and rebuilt when data.gov.sg dates the layer later;
  `python -m scripts.build_land_use_index` builds it ahead of time. Until it exists, zoning is skipped with the reason.

Zoning is planned use, looked up at the postal code's map point, so a site on a zone boundary can read as its
neighbour: findings from it are signals or inconsistencies, never impossibilities.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
import numpy as np

from ....config import settings
from ....cpu_pool import run_cpu
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
# Download limits. Measured (Oct 2026): the 175 MB layer downloaded in 21 s on one run and 104 s on a slower
# one, and parses in ~10 s. The whole-download deadline is 300 s, ~3x the slowest run; a connection that goes
# quiet fails after 60 s.
_CONNECT_TIMEOUT_SECONDS = 30.0
_READ_TIMEOUT_SECONDS = 60.0
_DOWNLOAD_DEADLINE_SECONDS = 300


@dataclass(frozen=True)
class LandUseIndex:
    zones: list[str]
    zone_of: np.ndarray       # polygon -> index into zones
    bboxes: np.ndarray        # polygon -> (min lon, min lat, max lon, max lat), float32
    ring_start: np.ndarray    # polygon -> first ring (polygons' rings are contiguous)
    point_start: np.ndarray   # ring -> first point (rings' points are contiguous)
    points: np.ndarray        # (lon, lat), float32: about a metre in Singapore, plenty for zoning
    last_updated: str | None  # the catalogue's date for the layer when built; None if it gave none

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


def build_index(geojson: dict, last_updated: str | None) -> LandUseIndex:
    """The index from a parsed GeoJSON (tests and small files; the real layer is streamed, see
    build_index_from_file)."""
    return _build_from_features(geojson.get("features", []), last_updated)


def build_index_from_file(path: Path, last_updated: str | None) -> LandUseIndex:
    """The index from a GeoJSON file, parsed one feature at a time so the 175 MB layer is never in memory."""
    import ijson

    with open(path, "rb") as handle:
        return _build_from_features(ijson.items(handle, "features.item", use_float=True), last_updated)


def _build_from_features(features: Iterable[dict], last_updated: str | None) -> LandUseIndex:
    zones: list[str] = []
    zone_ids: dict[str, int] = {}
    zone_of, bboxes, ring_start, point_start, points = [], [], [0], [0], []
    for feature in features:
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
            # float32 from the start: it's what the index stores, and it halves the build's memory.
            ring_points = [np.asarray(ring, dtype=np.float32)[:, :2] for ring in rings if len(ring) >= 4]
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
        points=np.concatenate(points) if points else np.zeros((0, 2), dtype=np.float32), last_updated=last_updated,
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


def _parse_time(value: str | None) -> datetime | None:
    """An ISO timestamp as an aware datetime (a naive one is taken as UTC); None if absent or unreadable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_newer(published: str | None, built: str | None) -> bool:
    """Whether the catalogue's date is later than the index's. Timestamps are compared as times, not
    strings (they carry different UTC offsets); if either is unknown nothing can be said, so no."""
    published_at, built_at = _parse_time(published), _parse_time(built)
    return bool(published_at and built_at and published_at > built_at)


def _catalogue_last_updated(client: httpx.Client, headers: dict | None) -> str | None:
    """When data.gov.sg says the layer was last updated, or None if it gives no date."""
    response = client.get(_METADATA_URL.format(dataset=LAND_USE_DATASET), headers=headers)
    response.raise_for_status()
    return (response.json().get("data") or {}).get("lastUpdatedAt") or None


def _download(client: httpx.Client, url: str, destination: Path, deadline: float) -> None:
    """Stream the layer to disk, giving up when the whole download passes its deadline."""
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with open(destination, "wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                if time.monotonic() > deadline:
                    raise LookupUnavailable(f"downloading the land-use layer took longer than {_DOWNLOAD_DEADLINE_SECONDS}s")
                out.write(chunk)


def download_and_build(last_updated: str | None = None) -> LandUseIndex:
    """Download the layer from data.gov.sg and build and save its index.

    Streamed end to end: the 175 MB GeoJSON goes to a temporary file and is parsed one feature at a time,
    so the build peaks at about 200 MB of memory (measured) and takes about a minute. A stalled connection
    fails after _READ_TIMEOUT_SECONDS, and the whole download after _DOWNLOAD_DEADLINE_SECONDS.
    last_updated is the catalogue's date for the layer; it stays None when the catalogue gives none.
    """
    headers = {"x-api-key": settings.DATA_GOV_SG_API_KEY} if settings.DATA_GOV_SG_API_KEY else None
    timeout = httpx.Timeout(_CONNECT_TIMEOUT_SECONDS, read=_READ_TIMEOUT_SECONDS)
    deadline = time.monotonic() + _DOWNLOAD_DEADLINE_SECONDS
    with httpx.Client(timeout=timeout) as client, tempfile.TemporaryDirectory(prefix="land-use-") as tmp:
        if last_updated is None:
            last_updated = _catalogue_last_updated(client, headers)
        poll = client.get(_POLL_DOWNLOAD_URL.format(dataset=LAND_USE_DATASET), headers=headers)
        poll.raise_for_status()
        url = (poll.json().get("data") or {}).get("url")
        if not url:
            raise LookupUnavailable(f"data.gov.sg gave no download link for {LAND_USE_DATASET}: {poll.text[:200]}")
        geojson = Path(tmp) / "land_use.geojson"
        _download(client, url, geojson, deadline)  # the download link is pre-signed: no API key sent to it
        index = build_index_from_file(geojson, last_updated)
    save_index(index)
    return index


def cli(argv: list[str] | None = None) -> None:
    """Build the index: `python -m scripts.build_land_use_index [FILE]`, and the Docker image build.
    With FILE, from an already-downloaded GeoJSON; otherwise from data.gov.sg. Fails loudly (non-zero
    exit) when it can't."""
    args = sys.argv[1:] if argv is None else argv
    started = time.monotonic()
    if args:
        headers = {"x-api-key": settings.DATA_GOV_SG_API_KEY} if settings.DATA_GOV_SG_API_KEY else None
        try:
            with httpx.Client(timeout=_CONNECT_TIMEOUT_SECONDS) as client:
                published = _catalogue_last_updated(client, headers)
        except httpx.HTTPError:
            published = None  # a local file build doesn't need the catalogue
        index = build_index_from_file(Path(args[0]), published)
        save_index(index)
    else:
        index = download_and_build()
    print(f"Land-use index: {len(index.zone_of)} polygons in {len(index.zones)} zones, layer updated "
          f"{index.last_updated or 'unknown'}, built in {time.monotonic() - started:.0f}s -> {INDEX_DIR}")


_lock = threading.Lock()
# checked_at: when the catalogue was last asked whether the layer is newer (None: not yet, so the first lookup asks).
_state: dict[str, Any] = {"index": None, "loaded": False, "building": False, "checked_at": None}


def _loaded_index() -> LandUseIndex | None:
    with _lock:
        if not _state["loaded"]:
            _state["index"] = load_index()
            _state["loaded"] = True
        return _state["index"]


def log_startup_status() -> None:
    """One line at server start saying whether zoning has an index, so a missing one is visible."""
    index = _loaded_index()
    if index is not None:
        logger.info("URA land-use index loaded: %d polygons, %d zones, layer updated %s",
                    len(index.zone_of), len(index.zones), index.last_updated or "unknown")
    elif settings.LAND_USE_RUNTIME_REBUILD:
        logger.warning("URA land-use index MISSING: zoning checks are skipped until it is built "
                       "(started in the background on the first address check)")
    else:
        logger.warning("URA land-use index MISSING: zoning checks are skipped, and this deployment never builds "
                       "it itself: rebuild the image (backend/Dockerfile builds it)")


def _build_in_background(last_updated: str | None) -> None:
    def run() -> None:
        try:
            index = download_and_build(last_updated)
            with _lock:
                _state["index"] = index
            logger.info("Land-use index built (layer updated %s)", index.last_updated or "unknown")
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
    Starts a background (re)build when there is no index, or data.gov.sg dates the layer later than the
    index (an unknown date never triggers one), unless settings.LAND_USE_RUNTIME_REBUILD is off (the
    Docker image, which has the index built in)."""
    index = _loaded_index()
    if not settings.LAND_USE_RUNTIME_REBUILD:
        if index is None:
            raise LookupUnavailable("the URA land-use index isn't included in this deployment (built at image build "
                                    "time); zoning is checked once a deploy includes it")
        return index
    with _lock:
        checked_at = _state["checked_at"]
        due = checked_at is None or time.monotonic() - checked_at > _FRESHNESS_CHECK_SECONDS
        if due:
            _state["checked_at"] = time.monotonic()
    if index is None or due:
        try:
            published = await dataset_last_updated(LAND_USE_DATASET)
        except LookupUnavailable:
            published = None
        if index is None or _is_newer(published, index.last_updated):
            _build_in_background(published)
    if index is None:
        raise LookupUnavailable("the URA land-use index is being built (first use); zoning is checked once it's ready")
    return index


async def zone_at(lon: float, lat: float) -> str | None:
    """The zone at a point, or None if it's in no polygon (or several disagreeing ones, at a boundary).
    The polygon search runs on the CPU pool, off the event loop."""
    index = await current_index()
    found = set(await run_cpu(index.zones_at, lon, lat))
    return found.pop() if len(found) == 1 else None
