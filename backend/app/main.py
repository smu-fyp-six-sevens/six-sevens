import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import auth, health, realtime_listener, telemetry
from .config import settings
from .health import check_db_connection
from .queue_service import document_queue
from .routers import extraction, injection, cases, detection, documents, monitoring, upload, ws

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

    try:
        await realtime_listener.start()
    except Exception:
        # Also non-fatal, same reasoning as above — the case status
        # WebSocket degrades to "never pushes anything beyond the initial
        # snapshot on connect" rather than taking the whole app down, e.g.
        # if SUPABASE_SECRET_KEY isn't configured in a given environment yet.
        logger.exception("Realtime listener failed to start — continuing anyway")

    yield

    await realtime_listener.stop()


app = FastAPI(title="fyp API", lifespan=lifespan)
telemetry.setup(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_origin_regex=settings.ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_protected = [Depends(auth.require_auth)]

# Not in _protected: /health, /health/db (see app/health.py — a Cloud Run
# liveness/readiness probe can't carry an Authorization header) and
# /auth/login below (it's what issues the token in the first place).
app.include_router(health.router)
app.include_router(cases.router, dependencies=_protected)
app.include_router(upload.router, dependencies=_protected)
app.include_router(documents.router, dependencies=_protected)
app.include_router(monitoring.router, dependencies=_protected)
app.include_router(detection.router, dependencies=_protected)
app.include_router(detection.cases_router, dependencies=_protected)
app.include_router(injection.router, dependencies=_protected)
app.include_router(extraction.router, dependencies=_protected)
# Not in _protected as a whole: the WebSocket route can't carry an
# Authorization header from the browser, so it checks its own short-lived
# ticket query param instead (see routers/ws.py) — its sibling POST
# /ws/tickets route applies require_auth itself, directly on that route.
app.include_router(ws.router)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str


@app.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    if not auth.verify_credentials(body.username, body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    return LoginResponse(token=auth.create_token())


@app.get("/message")
def message() -> dict[str, str]:
    return {"message": "Hello from FastAPI"}
