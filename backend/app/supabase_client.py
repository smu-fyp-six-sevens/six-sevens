from __future__ import annotations

from supabase import create_client, Client

from .config import settings


def _build_client() -> Client:
    if not settings.SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL is not set")
    if not settings.SUPABASE_PUBLISHABLE_KEY:
        raise RuntimeError("SUPABASE_PUBLISHABLE_KEY is not set")

    return create_client(settings.SUPABASE_URL, settings.SUPABASE_PUBLISHABLE_KEY)


supabase_client: Client = _build_client()
