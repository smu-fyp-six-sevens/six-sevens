# supabase_client.py
from __future__ import annotations

import httpx
from supabase import create_client, Client, ClientOptions

from .config import settings


def _build_client() -> Client:
    if not settings.SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL is not set")
    if not settings.SUPABASE_PUBLISHABLE_KEY:
        raise RuntimeError("SUPABASE_PUBLISHABLE_KEY is not set")

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
    # nothing to do with permissions). Matches dify_client.py's own
    # timeout=30.0 for the same class of external-call latency.
    http_client = httpx.Client(http2=False, timeout=30.0)
    return create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_PUBLISHABLE_KEY,
        options=ClientOptions(httpx_client=http_client),
    )


supabase_client: Client = _build_client()
