"""URA land-use zoning index: the image build, streaming, freshness and startup behaviour."""

from __future__ import annotations

import importlib
import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.config import settings
from app.fraud_detection.external_validation.registry_lookups import land_use, ura
from app.fraud_detection.external_validation.registry_lookups.http import LookupUnavailable
# Imported here, before conftest swaps current_index out for every test.
from app.fraud_detection.external_validation.registry_lookups.land_use import current_index as _real_current_index

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"

_SQUARE = [[[103.0, 1.0], [103.2, 1.0], [103.2, 1.2], [103.0, 1.2], [103.0, 1.0]]]
_WITH_HOLE = [[[104.0, 1.0], [104.4, 1.0], [104.4, 1.4], [104.0, 1.4], [104.0, 1.0]],
              [[104.1, 1.1], [104.3, 1.1], [104.3, 1.3], [104.1, 1.3], [104.1, 1.1]]]
_GEOJSON = {"features": [
    {"geometry": {"type": "Polygon", "coordinates": _SQUARE}, "properties": {"LU_DESC": "BUSINESS 2"}},
    {"geometry": {"type": "MultiPolygon", "coordinates": [_WITH_HOLE]}, "properties": {"LU_DESC": "RESIDENTIAL"}},
]}


# --- the image build must use the module's real name ------------------------------------------------------------


def _land_use_run_step() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    start = text.index("ARG ALLOW_MISSING_LAND_USE")
    # The step is the rest of its build stage, up to the next FROM.
    return text[start:text.index("\nFROM ", start)]


def test_the_dockerfile_imports_the_index_builder_under_a_name_that_exists() -> None:
    # A rename of the package (as when registry_lookups moved under external_validation) must break CI,
    # not the image: the Docker step is a python -c with a hard-coded import.
    match = re.search(r"from ([\w.]+) import (\w+)", _land_use_run_step())
    assert match, "no python import found in the land-use step of backend/Dockerfile"
    module = importlib.import_module(match.group(1))
    assert callable(getattr(module, match.group(2)))


def test_the_build_script_imports_and_runs_the_same_builder() -> None:
    script = importlib.import_module("scripts.build_land_use_index")
    assert script.cli is land_use.cli


def test_the_dockerfile_fails_the_build_unless_a_missing_index_is_explicitly_allowed() -> None:
    step = _land_use_run_step()
    assert "|| echo" not in step  # a failed build used to be swallowed
    assert 'ALLOW_MISSING_LAND_USE" = "true"' in step and "exit 1" in step
    assert re.search(r"ARG ALLOW_MISSING_LAND_USE=false", step)  # fail by default
    # The data.gov.sg key comes in as a BuildKit secret, never a build arg (which stays in image history).
    assert "--mount=type=secret,id=data_gov_sg_api_key" in step
    assert "ARG DATA_GOV_SG_API_KEY" not in DOCKERFILE.read_text(encoding="utf-8")


# --- streaming gives the same index as parsing it whole ----------------------------------------------------------


def test_the_streamed_index_equals_the_in_memory_one(tmp_path: Path) -> None:
    path = tmp_path / "layer.geojson"
    path.write_text(json.dumps(_GEOJSON), encoding="utf-8")
    streamed, whole = land_use.build_index_from_file(path, "2025-12-26"), land_use.build_index(_GEOJSON, "2025-12-26")
    for name in ("zone_of", "bboxes", "ring_start", "point_start", "points"):
        assert np.array_equal(getattr(streamed, name), getattr(whole, name)), name
    assert streamed.zones == whole.zones
    assert streamed.zones_at(104.05, 1.05) == ["RESIDENTIAL"] and streamed.zones_at(104.2, 1.2) == []  # a hole


# --- freshness: timestamps are compared as times, and an unknown date never rebuilds ----------------------------


def test_timestamps_are_compared_as_times_not_strings() -> None:
    # 10:07 at +08:00 is 02:07 UTC: EARLIER than 05:00 UTC, though it sorts later as a string.
    built, published = "2025-12-26T05:00:00+00:00", "2025-12-26T10:07:32+08:00"
    assert published > built  # the trap: string comparison says "newer"
    assert not land_use._is_newer(published, built)
    assert land_use._is_newer("2025-12-26T14:00:00+08:00", built)  # 06:00 UTC: genuinely newer
    assert land_use._is_newer("2026-01-01T00:00:00Z", "2025-12-26T10:07:32+08:00")


@pytest.mark.parametrize("published, built", [(None, "2025-12-26T10:07:32+08:00"), ("2025-12-26T10:07:32+08:00", None),
                                              (None, None), ("not a date", "2025-12-26T10:07:32+08:00"),
                                              ("2025-12-26T10:07:32+08:00", "unknown")])
def test_an_unknown_date_is_never_newer(published, built) -> None:
    assert land_use._is_newer(published, built) is False


@contextmanager
def _state(monkeypatch, *, index, published, rebuild: bool = True):
    """A server whose land-use index is `index` and whose catalogue says the layer was updated `published`."""
    started = []
    monkeypatch.setattr(land_use, "_state", {"index": None, "loaded": False, "building": False, "checked_at": None})
    monkeypatch.setattr(land_use, "load_index", lambda: index)
    monkeypatch.setattr(land_use, "dataset_last_updated", AsyncMock(return_value=published))
    monkeypatch.setattr(land_use, "_build_in_background", lambda last_updated: started.append(last_updated))
    monkeypatch.setattr(settings, "LAND_USE_RUNTIME_REBUILD", rebuild)
    yield started


def _index(last_updated: str | None):
    return land_use.build_index(_GEOJSON, last_updated)


async def test_a_newer_layer_starts_a_rebuild_and_the_first_lookup_checks_even_on_a_fresh_machine(monkeypatch) -> None:
    with _state(monkeypatch, index=_index("2025-12-26T10:07:32+08:00"), published="2026-03-01T00:00:00+08:00") as started:
        await _real_current_index()
    assert started == ["2026-03-01T00:00:00+08:00"]


async def test_an_older_or_same_layer_or_an_unknown_date_does_not_rebuild(monkeypatch) -> None:
    for built, published in [("2025-12-26T10:07:32+08:00", "2025-12-26T10:07:32+08:00"),
                             ("2025-12-26T10:07:32+08:00", "2025-06-01T00:00:00+08:00"),
                             ("2025-12-26T05:00:00+00:00", "2025-12-26T10:07:32+08:00"),  # the offset trap
                             (None, "2026-03-01T00:00:00+08:00"),  # the index has no date: nothing to compare
                             ("2025-12-26T10:07:32+08:00", None)]:  # the catalogue has no date
        with _state(monkeypatch, index=_index(built), published=published) as started:
            await _real_current_index()
        assert started == [], (built, published)


async def test_a_missing_index_is_built_whatever_the_catalogue_says(monkeypatch) -> None:
    for published in ("2026-03-01T00:00:00+08:00", None):
        with _state(monkeypatch, index=None, published=published) as started:
            with pytest.raises(LookupUnavailable, match="being built"):
                await _real_current_index()
        assert started == [published]


async def test_a_deployment_never_rebuilds_even_for_a_newer_layer(monkeypatch) -> None:
    with _state(monkeypatch, index=_index("2020-01-01T00:00:00+08:00"), published="2026-03-01T00:00:00+08:00", rebuild=False) as started:
        assert await _real_current_index() is not None
    assert started == []


# --- the zone search runs on the CPU pool -------------------------------------------------------------------------


async def test_the_polygon_search_runs_on_the_cpu_pool(monkeypatch) -> None:
    calls = []
    fake = SimpleNamespace(zones_at=lambda lon, lat: ["RESIDENTIAL"])

    async def run_cpu(fn, *args):
        calls.append((fn, args))
        return fn(*args)

    monkeypatch.setattr(land_use, "current_index", AsyncMock(return_value=fake))
    monkeypatch.setattr(land_use, "run_cpu", run_cpu)
    assert await land_use.zone_at(103.8, 1.3) == "RESIDENTIAL"
    assert calls == [(fake.zones_at, (103.8, 1.3))]


async def test_a_point_in_two_disagreeing_zones_is_no_zone(monkeypatch) -> None:
    fake = SimpleNamespace(zones_at=lambda lon, lat: ["RESIDENTIAL", "COMMERCIAL"])
    monkeypatch.setattr(land_use, "current_index", AsyncMock(return_value=fake))
    assert await land_use.zone_at(103.8, 1.3) is None


# --- startup status ------------------------------------------------------------------------------------------------


def test_startup_says_whether_the_index_is_loaded_or_missing(monkeypatch, caplog) -> None:
    logger = "fraud_detection.registry_lookups"

    def run(index, rebuild):
        monkeypatch.setattr(land_use, "_state", {"index": None, "loaded": False, "building": False, "checked_at": None})
        monkeypatch.setattr(land_use, "load_index", lambda: index)
        monkeypatch.setattr(settings, "LAND_USE_RUNTIME_REBUILD", rebuild)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=logger):
            land_use.log_startup_status()
        return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == logger]

    loaded = run(_index("2025-12-26T10:07:32+08:00"), rebuild=False)
    assert loaded == [(logging.INFO, "URA land-use index loaded: 2 polygons, 2 zones, layer updated 2025-12-26T10:07:32+08:00")]
    undated = run(_index(None), rebuild=False)
    assert "layer updated unknown" in undated[0][1]
    missing_deployed = run(None, rebuild=False)
    assert missing_deployed[0][0] == logging.WARNING and "MISSING" in missing_deployed[0][1] and "rebuild the image" in missing_deployed[0][1]
    missing_local = run(None, rebuild=True)
    assert missing_local[0][0] == logging.WARNING and "started in the background" in missing_local[0][1]
    assert "started in the background" not in missing_deployed[0][1]


# --- the download --------------------------------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes = b"", payload: dict | None = None):
        self._body, self._payload, self.text = body, payload or {}, json.dumps(payload or {})

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload

    def iter_bytes(self, size: int):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeClient:
    """data.gov.sg as httpx.Client sees it: a catalogue, a download link, and the (pre-signed) file."""

    def __init__(self, *, catalogue: dict, body: bytes, requests: list):
        self._catalogue, self._body, self.requests = catalogue, body, requests

    def get(self, url, headers=None):
        self.requests.append(("get", url, headers))
        if "metadata" in url:
            return _FakeResponse(payload=self._catalogue)
        return _FakeResponse(payload={"data": {"url": "https://s3.example/layer.geojson"}})

    def stream(self, method, url, **kwargs):
        self.requests.append(("stream", url, kwargs.get("headers")))
        return _FakeResponse(body=self._body)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def isolated_index_dir(tmp_path, monkeypatch):
    """So a build in a test can never overwrite the real index under backend/data/."""
    monkeypatch.setattr(land_use, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(land_use, "_INDEX_FILE", tmp_path / "land_use_index.npz")
    monkeypatch.setattr(land_use, "_META_FILE", tmp_path / "land_use_index.json")
    return tmp_path


def _download_with(monkeypatch, *, catalogue: dict, key: str = ""):
    requests: list = []
    body = json.dumps(_GEOJSON).encode()
    monkeypatch.setattr(land_use.httpx, "Client", lambda **kw: _FakeClient(catalogue=catalogue, body=body, requests=requests))
    monkeypatch.setattr(settings, "DATA_GOV_SG_API_KEY", key)
    return requests


def test_a_missing_catalogue_date_is_stored_as_none_not_a_made_up_one(monkeypatch, isolated_index_dir) -> None:
    _download_with(monkeypatch, catalogue={"data": {}})
    index = land_use.download_and_build()
    assert index.last_updated is None
    assert json.loads((isolated_index_dir / "land_use_index.json").read_text())["last_updated"] is None
    reloaded = land_use.load_index()
    assert reloaded is not None and reloaded.last_updated is None  # and it round-trips


def test_the_catalogue_date_is_kept_and_the_api_key_goes_only_to_data_gov_sg(monkeypatch, isolated_index_dir) -> None:
    requests = _download_with(monkeypatch, catalogue={"data": {"lastUpdatedAt": "2025-12-26T10:07:32+08:00"}}, key="secret")
    index = land_use.download_and_build()
    assert index.last_updated == "2025-12-26T10:07:32+08:00" and len(index.zone_of) == 2
    api = [headers for kind, url, headers in requests if kind == "get"]
    assert api and all(h == {"x-api-key": "secret"} for h in api)
    downloads = [headers for kind, url, headers in requests if kind == "stream"]
    assert downloads == [None]  # the pre-signed link needs no key and must not get it


def test_a_download_past_its_deadline_fails_instead_of_hanging(tmp_path) -> None:
    client: Any = _FakeClient(catalogue={}, body=b"x" * (3 << 20), requests=[])  # stands in for httpx.Client
    with pytest.raises(LookupUnavailable, match="took longer"):
        land_use._download(client, "https://s3.example/layer.geojson", tmp_path / "layer.geojson", deadline=0.0)


def test_a_failed_build_exits_non_zero(monkeypatch) -> None:
    # What makes the Docker step fail the image build: the CLI raises, so python -c exits non-zero.
    def boom():
        raise LookupUnavailable("data.gov.sg gave no download link")
    monkeypatch.setattr(land_use, "download_and_build", boom)
    with pytest.raises(LookupUnavailable):
        land_use.cli([])


# --- URA's answer cache is bounded ---------------------------------------------------------------------------------


async def test_the_ura_answer_cache_drops_its_oldest_answer_when_full(monkeypatch) -> None:
    monkeypatch.setattr(settings, "URA_API_KEY", "key")
    monkeypatch.setattr(ura, "_MAX_ANSWERS", 3)
    monkeypatch.setattr(ura, "_answers", {})
    today = datetime.now(ura._SGT).date().isoformat()
    monkeypatch.setattr(ura, "_token", {"day": today, "value": "token"})
    monkeypatch.setattr(ura, "get", AsyncMock(return_value={"isResiUse": "NA"}))
    for block in ("1", "2", "3", "4", "5"):
        await ura.approved_residential_use(block, "MARINA BOULEVARD")
    assert list(ura._answers) == [("3", "MARINA BOULEVARD", None), ("4", "MARINA BOULEVARD", None), ("5", "MARINA BOULEVARD", None)]
