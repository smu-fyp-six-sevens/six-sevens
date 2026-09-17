import pytest

from app.supabase_client import _build_client


def test_build_client_requires_supabase_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "")

    with pytest.raises(RuntimeError, match="SUPABASE_URL is not set"):
        _build_client()


def test_build_client_requires_supabase_publishable_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_PUBLISHABLE_KEY", "")

    with pytest.raises(RuntimeError, match="SUPABASE_PUBLISHABLE_KEY is not set"):
        _build_client()
