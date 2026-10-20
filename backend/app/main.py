import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import audit, auth, health, query_stats, realtime_listener, telemetry
from .config import settings
from .fraud_detection.external_validation.registry_lookups import land_use
from .health import check_db_connection
from .langgraph._anthropic import warm_up
from .queue_service import document_queue
from .routers import injection, cases, detection, documents, monitoring, upload, ws

# No handler was ever configured for this app's own loggers, so INFO-level
# calls (pipeline/detection progress) were silently dropped — only
# logger.exception(...) ever reached the console, via logging's "last
# resort" stderr handler, which only fires at WARNING+ and prints bare
# messages with no logger name or timestamp.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# One line per Supabase call drowned everything else; the "perf" summaries (query_stats.py) replace it.
if not settings.LOG_HTTP_REQUESTS:
    logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.AUTH_USERS:
        logger.warning("AUTH_USERS is not set, so nobody can log in. Set it in backend/.env")
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

    try:
        await asyncio.to_thread(land_use.log_startup_status)
    except Exception:
        logger.exception("Could not read the land-use index status — continuing anyway")

    try:
        await asyncio.to_thread(warm_up)
    except Exception:
        logger.exception("Claude client warm-up failed — continuing anyway")

    reporter = asyncio.create_task(query_stats.run_reporter())

    yield

    reporter.cancel()
    await realtime_listener.stop()


app = FastAPI(title="fyp API", lifespan=lifespan)
telemetry.setup(app)


@app.middleware("http")
async def count_queries(request: Request, call_next):
    # Background work a handler starts opens its own scope, so it isn't counted here.
    with query_stats.scope(f"{request.method} {query_stats.label_path(request.url.path)}"):
        return await call_next(request)

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
    username: str


@app.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    username = auth.verify_credentials(body.username, body.password)
    if username is None:
        # The attempted name is recorded; never the password.
        audit.record("auth.login", body.username[:100], outcome="failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    audit.record("auth.login", username)
    return LoginResponse(token=auth.create_token(username), username=username)


@app.get("/message")
def message() -> dict[str, str]:
    return {"message": "Hello from FastAPI"}
