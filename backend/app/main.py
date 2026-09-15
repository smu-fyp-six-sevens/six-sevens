import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import require_auth
from .config import settings
from .health import check_db_connection
from .queue_service import document_queue
from .routers import auth, extraction, injection, cases, detection, documents, monitoring, upload

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    document_queue.start()
    try:
        await asyncio.to_thread(check_db_connection)
        logger.info("Database connectivity check passed at startup")
    except Exception:
        # Deliberately non-fatal: a transient Supabase blip at boot shouldn't
        # crash-loop the whole app. GET /health/db surfaces ongoing status.
        logger.exception("Database connectivity check failed at startup — continuing anyway")
    yield


app = FastAPI(title="fyp API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_origin_regex=settings.ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_protected = [Depends(require_auth)]

app.include_router(auth.router)
app.include_router(cases.router, dependencies=_protected)
app.include_router(upload.router, dependencies=_protected)
app.include_router(documents.router, dependencies=_protected)
app.include_router(monitoring.router, dependencies=_protected)
app.include_router(detection.router, dependencies=_protected)
app.include_router(injection.router, dependencies=_protected)
app.include_router(extraction.router, dependencies=_protected)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Liveness only — does not touch the database. See /health/db for that."""
    return {"status": "ok"}


@app.get("/health/db")
def health_check_db() -> JSONResponse:
    """
    Readiness check: does a real query against Supabase succeed right now?
    Suitable for a Cloud Run readiness probe once deployed there — a failure
    here should stop new traffic being routed to this instance, not restart
    it (the underlying cause is almost never fixed by a restart).
    """
    try:
        check_db_connection()
    except Exception as exc:
        logger.exception("Database health check failed")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "detail": str(exc)},
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ok"})


@app.get("/message")
def message() -> dict[str, str]:
    return {"message": "Hello from FastAPI"}
