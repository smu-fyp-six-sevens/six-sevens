"""deploy/helm/gen_values.py: the chart's role values follow Terraform's role table."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "gen_values.py"


@pytest.fixture
def gen(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("gen_values", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "TERRAFORM", tmp_path)
    monkeypatch.setattr(module, "OUT", tmp_path / "values-roles.yaml")
    return module


def _role(**gke) -> dict:
    return {
        "cpu": "1", "memory": "1Gi", "concurrency": 10, "min": 0, "max": 3, "public": False,
        "env": {}, "secrets": ["SUPABASE_SECRET_KEY"], "gke": {"min": 1, "drain_seconds": 30, **gke},
    }


def _write(path: Path, pull_roles: list[str], consumers: list[str]) -> None:
    (path / "roles.json").write_text(json.dumps({
        "common_env": {"A": "b"},
        "roles": {"api": _role(), "doc-worker": _role(), "case-worker": _role(cpu="2")},
    }))
    (path / "migration.json").write_text(json.dumps({"pull_roles": pull_roles}))
    (path / "subscriptions.json").write_text(json.dumps(consumers))


def _roles(gen) -> dict:
    return json.loads(gen.render().split("\n", 1)[1])["roles"]


def test_a_pulled_role_that_consumes_no_events_stops_generation(gen, tmp_path) -> None:
    _write(tmp_path, ["api"], ["doc-worker", "case-worker"])

    with pytest.raises(SystemExit, match="consume no events"):
        gen.render()


def test_enabled_is_true_for_api_and_for_a_consumer_only_when_listed(gen, tmp_path) -> None:
    _write(tmp_path, ["doc-worker"], ["doc-worker", "case-worker"])

    roles = _roles(gen)

    assert roles["api"]["enabled"] is True and roles["api"]["consumer"] is False
    assert roles["doc-worker"]["enabled"] is True
    assert roles["case-worker"]["enabled"] is False and roles["case-worker"]["consumer"] is True


def test_secret_ids_are_lower_case_and_dashed(gen) -> None:
    assert gen.secret_id("SUPABASE_SECRET_KEY") == "supabase-secret-key"


def test_a_role_lists_its_secrets_with_their_ids(gen, tmp_path) -> None:
    _write(tmp_path, [], ["doc-worker"])

    assert _roles(gen)["api"]["secrets"] == [{"env": "SUPABASE_SECRET_KEY", "id": "supabase-secret-key"}]


def test_gke_sizes_override_cloud_runs(gen, tmp_path) -> None:
    _write(tmp_path, [], ["doc-worker"])

    roles = _roles(gen)

    assert roles["case-worker"]["cpu"] == "2" and roles["api"]["cpu"] == "1"


def test_check_fails_on_a_stale_file(gen, tmp_path, monkeypatch) -> None:
    _write(tmp_path, [], ["doc-worker"])
    gen.OUT.write_text("stale")
    monkeypatch.setattr("sys.argv", ["gen_values.py", "--check"])

    with pytest.raises(SystemExit, match="out of date"):
        gen.main()


def test_check_passes_on_a_file_just_written(gen, tmp_path, monkeypatch, capsys) -> None:
    _write(tmp_path, [], ["doc-worker"])
    monkeypatch.setattr("sys.argv", ["gen_values.py"])
    gen.main()
    monkeypatch.setattr("sys.argv", ["gen_values.py", "--check"])

    gen.main()

    assert "up to date" in capsys.readouterr().out


def test_check_fails_when_the_file_is_missing(gen, tmp_path, monkeypatch) -> None:
    _write(tmp_path, [], ["doc-worker"])
    monkeypatch.setattr("sys.argv", ["gen_values.py", "--check"])

    with pytest.raises(SystemExit, match="out of date"):
        gen.main()
