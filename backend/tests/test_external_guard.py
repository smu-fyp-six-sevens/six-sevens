"""
Canary: the autouse guards in conftest.py must stay active. If one is
removed, these fail instead of a test quietly reaching Supabase or Claude.
"""
import pytest

from app import case_repository
from app.config import settings
from app.supabase_client import build_async_supabase_client, service_supabase_client, supabase_client


def test_publishable_client_table_access_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="real Supabase call"):
        supabase_client.table("documents")


def test_secret_key_client_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="real Supabase call"):
        service_supabase_client().table("cases")


def test_a_delete_without_a_mock_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="real Supabase call"):
        case_repository.delete_case("canary-case")


async def test_realtime_connection_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://canary.supabase.invalid")
    monkeypatch.setattr(settings, "SUPABASE_SECRET_KEY", "canary-key")
    with pytest.raises(RuntimeError, match="real Supabase Realtime connection"):
        await build_async_supabase_client()
