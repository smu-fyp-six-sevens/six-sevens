# supabase_client.py
from __future__ import annotations

import httpx
from supabase import acreate_client, create_client, AsyncClient, Client, ClientOptions

from .config import settings
from .query_stats import HTTPX_EVENT_HOOKS


def _build_client() -> Client:
    if not settings.SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL is not set")
    if not settings.SUPABASE_SECRET_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY is not set")

    # postgrest-py's default httpx client hardcodes http2=True, which isn't
    # safe under concurrent access from multiple OS threads — batch upload
    # inserts several documents concurrently via asyncio.to_thread, and
    # multiplexed HTTP/2 streams on one shared connection corrupt under
    # concurrent thread access (intermittent httpcore.ReadError). Supplying
    # our own HTTP/1.1 client makes httpx hand each concurrent request its
    # own pooled connection instead, which is thread-safe.
    #
    # timeout=30.0: httpx's unset default is 5s for connect/read/write,
    # which is tight for uploading an actual file's bytes to Storage under
    # any real network variance — a plain 5s read timeout surfaced as
    # "Failed to store the uploaded file. Check Supabase bucket access."
    # (a misleading message for what was really just httpcore.ReadTimeout,
    # nothing to do with permissions).
    http_client = httpx.Client(http2=False, timeout=30.0, event_hooks=HTTPX_EVENT_HOOKS)
    return create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SECRET_KEY,
        options=ClientOptions(httpx_client=http_client),
    )


supabase_client: Client = _build_client()

_service_client: Client | None = None


def service_supabase_client() -> Client:
    """
    Backend-only client, a second instance on the same secret key as supabase_client above (which bypasses
    row-level security, so no grant on the anon role is needed).
    """
    global _service_client
    if _service_client is not None:
        return _service_client

    if not settings.SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL is not set")
    if not settings.SUPABASE_SECRET_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY is not set")

    http_client = httpx.Client(http2=False, timeout=30.0, event_hooks=HTTPX_EVENT_HOOKS)
    _service_client = create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SECRET_KEY,
        options=ClientOptions(httpx_client=http_client),
    )
    return _service_client


async def build_async_supabase_client() -> AsyncClient:
    """
    Separate from supabase_client above (which is sync, built eagerly at
    import time, and used everywhere via asyncio.to_thread) — this is only
    for realtime_listener.py's Realtime subscription, which needs the
    async client and can't be constructed at import time since acreate_client
    itself is a coroutine. Called once from main.py's lifespan.

    Uses SUPABASE_SECRET_KEY (bypasses RLS) —
    this subscription is a trusted, backend-only context, never exposed to
    the browser.
    """
    if not settings.SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL is not set")
    if not settings.SUPABASE_SECRET_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY is not set")

    return await acreate_client(settings.SUPABASE_URL, settings.SUPABASE_SECRET_KEY)
