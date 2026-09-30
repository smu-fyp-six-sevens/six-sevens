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
async def test_build_async_supabase_client_builds_with_url_and_secret_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr("app.supabase_client.settings.SUPABASE_SECRET_KEY", "secret-key")

    with patch(
        "app.supabase_client.acreate_client", new=AsyncMock(return_value="async-client")
    ) as mock_acreate_client:
        result = await build_async_supabase_client()

    mock_acreate_client.assert_awaited_once_with("https://example.supabase.co", "secret-key")
    assert result == "async-client"


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

    with patch("app.supabase_client.create_client", return_value="new-service-client") as mock_create_client:
        result = service_supabase_client()

    assert result == "new-service-client"
    mock_create_client.assert_called_once()
    assert supabase_client_module._service_client == "new-service-client"

    monkeypatch.setattr(supabase_client_module, "_service_client", None)
