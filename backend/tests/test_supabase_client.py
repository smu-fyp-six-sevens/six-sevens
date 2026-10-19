from unittest.mock import AsyncMock, patch

import pytest

from app import supabase_client as supabase_client_module
from app.supabase_client import _build_client, build_async_supabase_client, service_supabase_client


def test_build_client_requires_supabase_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "")

    with pytest.raises(RuntimeError, match="SUPABASE_URL is not set"):
        _build_client()


def test_build_client_requires_supabase_publishable_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_PUBLISHABLE_KEY", "")

    with pytest.raises(RuntimeError, match="SUPABASE_PUBLISHABLE_KEY is not set"):
        _build_client()


@pytest.mark.asyncio
async def test_build_async_supabase_client_requires_supabase_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "")

    with pytest.raises(RuntimeError, match="SUPABASE_URL is not set"):
        await build_async_supabase_client()


@pytest.mark.asyncio
async def test_build_async_supabase_client_requires_supabase_secret_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_SECRET_KEY", "")

    with pytest.raises(RuntimeError, match="SUPABASE_SECRET_KEY is not set"):
        await build_async_supabase_client()


@pytest.mark.asyncio
async def test_build_async_supabase_client_uses_the_secret_key_not_the_publishable_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_SECRET_KEY", "secret-key")
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_PUBLISHABLE_KEY", "publishable-key")

    with patch("app.supabase_client.acreate_client", new=AsyncMock()) as mock_acreate_client:
        await build_async_supabase_client()

    # The Realtime subscription needs the secret key; the publishable one would be refused by RLS.
    mock_acreate_client.assert_awaited_once_with("https://example.supabase.co", "secret-key")


def test_service_supabase_client_returns_the_cached_instance_without_rebuilding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supabase_client_module, "_service_client", "already-built-client")

    with patch("app.supabase_client.create_client") as mock_create_client:
        result = service_supabase_client()

    assert result == "already-built-client"
    mock_create_client.assert_not_called()


def test_service_supabase_client_requires_supabase_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supabase_client_module, "_service_client", None)
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "")

    with pytest.raises(RuntimeError, match="SUPABASE_URL is not set"):
        service_supabase_client()


def test_service_supabase_client_requires_supabase_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supabase_client_module, "_service_client", None)
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_SECRET_KEY", "")

    with pytest.raises(RuntimeError, match="SUPABASE_SECRET_KEY is not set"):
        service_supabase_client()


def test_service_supabase_client_builds_once_and_caches_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supabase_client_module, "_service_client", None)
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_SECRET_KEY", "secret-key")

    with patch("app.supabase_client.create_client", side_effect=[object(), object()]) as mock_create_client:
        first = service_supabase_client()
        second = service_supabase_client()

    assert first is second
    mock_create_client.assert_called_once()
    assert mock_create_client.call_args.args[:2] == ("https://example.supabase.co", "secret-key")
