from __future__ import annotations

from .supabase_client import supabase_client


def check_db_connection() -> None:
    """
    Runs a real, lightweight query against Supabase. Raises on any failure
    (network, auth, RLS, missing table, project paused, etc.) — callers
    decide how to report that (HTTP status, log level, startup behavior).
    """
    supabase_client.table("documents").select("id").limit(1).execute()
