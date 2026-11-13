from __future__ import annotations

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from .supabase_client import supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


def check_db_connection() -> None:
    """
    Runs a real, lightweight query against Supabase. Raises on any failure
    (network, auth, RLS, missing table, project paused, etc.) — callers
    decide how to report that (HTTP status, log level, startup behavior).
    """
    supabase_client.table("documents").select("id").limit(1).execute()


@router.get("/health")
@router.get("/healthz")
def health_check() -> dict[str, str]:
    """Liveness only — does not touch the database. See /health/db for that."""
    return {"status": "ok"}


@router.get("/health/db")
def health_check_db() -> JSONResponse:
    """
    Readiness check: does a real query against Supabase succeed right now?
    Suitable for a Cloud Run readiness probe once deployed there — a failure
    here should stop new traffic being routed to this instance, not restart
    it (the underlying cause is almost never fixed by a restart).
    """
    try:
        check_db_connection()
    except Exception:
        # The error text stays in the log: the route is open, and it can name hosts or keys.
        logger.exception("Database health check failed")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "detail": "The database could not be reached."},
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ok"})
