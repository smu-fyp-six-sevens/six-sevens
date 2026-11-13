from unittest.mock import AsyncMock, patch

import pytest

from app import supabase_client as supabase_client_module
from app.supabase_client import _build_client, build_async_supabase_client, service_supabase_client


def test_build_client_requires_supabase_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "")

    with pytest.raises(RuntimeError, match="SUPABASE_URL is not set"):
        _build_client()


def test_build_client_requires_supabase_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_SECRET_KEY", "")

    with pytest.raises(RuntimeError, match="SUPABASE_SECRET_KEY is not set"):
        _build_client()


async def test_build_async_supabase_client_requires_supabase_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "")

    with pytest.raises(RuntimeError, match="SUPABASE_URL is not set"):
        await build_async_supabase_client()


async def test_build_async_supabase_client_requires_supabase_secret_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_SECRET_KEY", "")

    with pytest.raises(RuntimeError, match="SUPABASE_SECRET_KEY is not set"):
        await build_async_supabase_client()


async def test_build_async_supabase_client_uses_the_secret_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_SECRET_KEY", "secret-key")

    with patch("app.supabase_client.acreate_client", new=AsyncMock()) as mock_acreate_client:
        await build_async_supabase_client()

    # The Realtime subscription needs the secret key.
    mock_acreate_client.assert_awaited_once_with("https://example.supabase.co", "secret-key")


def test_the_service_client_is_the_one_shared_client() -> None:
    # One connection pool: the "service" name is kept for its callers, but there is no second client.
    assert service_supabase_client() is supabase_client_module.supabase_client
