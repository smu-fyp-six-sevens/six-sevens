import asyncio
import importlib.util
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import audit, auth, events, health, query_stats, realtime_listener, telemetry
from .config import settings
from .events import bus, registry, types
from .events import push as event_push
from .fraud_detection.external_validation.registry_lookups import land_use
from .health import check_db_connection
from .langgraph._anthropic import warm_up
from .routers import audit as audit_router
from .routers import cases, detection, documents, monitoring, upload, ws
from .workers import cases as case_worker
from .workers import detection as detection_worker
from .workers import documents as doc_worker

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

bus.check_transport()
ROLES = settings.ROLES
IN_PROCESS = settings.EVENT_TRANSPORT == "inprocess"
_HANDLERS = {
    "doc-worker": doc_worker.HANDLERS,
    "detection-worker": detection_worker.HANDLERS,
    "case-worker": case_worker.HANDLERS,
    "audit-metrics": audit.HANDLERS,
}
_CALLS_CLAUDE = {"doc-worker", "detection-worker", "case-worker"}

registry.clear()
for _role, _handlers in _HANDLERS.items():
    if _role in ROLES:
        registry.install(_role, _handlers)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting roles %s with %s events", sorted(ROLES), settings.EVENT_TRANSPORT)
    if "api" in ROLES and not settings.AUTH_USERS:
        logger.warning("AUTH_USERS is not set, so nobody can log in. Set it in backend/.env")
    try:
        await asyncio.to_thread(check_db_connection)
        logger.info("Database connectivity check passed at startup")
    except Exception:
        # Deliberately non-fatal: a transient Supabase blip at boot shouldn't
        # crash-loop the whole app. GET /health/db surfaces ongoing status.
        logger.exception("Database connectivity check failed at startup — continuing anyway")

    if "api" in ROLES:
        try:
            await realtime_listener.start()
        except Exception:
            # Also non-fatal: the case status WebSocket then pushes nothing beyond the initial snapshot.
            logger.exception("Realtime listener failed to start — continuing anyway")

    if "detection-worker" in ROLES:
        try:
            await asyncio.to_thread(land_use.log_startup_status)
        except Exception:
            logger.exception("Could not read the land-use index status — continuing anyway")

    if ROLES & _CALLS_CLAUDE:
        try:
            await asyncio.to_thread(warm_up)
        except Exception:
            logger.exception("Claude client warm-up failed — continuing anyway")

    reporter = asyncio.create_task(query_stats.run_reporter())

    yield

    # SIGTERM: uvicorn stops taking requests and waits for those in flight (a push's work runs inside one).
    reporter.cancel()
    await bus.shutdown()
    if "api" in ROLES:
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

_protected = [Depends(auth.bind_actor)]

# Not in _protected: /health, /healthz, /health/db (a probe can't carry an Authorization header),
# /auth/login (it issues the token) and /push (Cloud Run IAM guards it).
app.include_router(health.router)

if "api" in ROLES:
    app.include_router(cases.router, dependencies=_protected)
    app.include_router(upload.router, dependencies=_protected)
    app.include_router(documents.router, dependencies=_protected)
    app.include_router(monitoring.router, dependencies=_protected)
    app.include_router(detection.router, dependencies=_protected)
    app.include_router(detection.cases_router, dependencies=_protected)
    # The WebSocket can't carry an Authorization header, so it checks its own short-lived ticket (routers/ws.py).
    app.include_router(ws.router)

    if IN_PROCESS and importlib.util.find_spec("app.injection") is not None:
        # Dev-only fraud injection: uvicorn mode only; the images leave it out.
        from .routers import injection

        app.include_router(injection.router, dependencies=_protected)

if "audit-metrics" in ROLES:
    app.include_router(audit_router.router, dependencies=_protected)

if not IN_PROCESS and registry.installed_roles():
    app.include_router(event_push.router)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str


if "api" in ROLES:

    @app.post("/auth/login", response_model=LoginResponse)
    async def login(body: LoginRequest) -> LoginResponse:
        username = auth.verify_credentials(body.username, body.password)
        if username is None:
            # The attempted name is recorded; never the password.
            await events.record(events.Event(
                type=types.AUTH_LOGIN, actor_type="user", actor=body.username[:100], outcome="failed",
            ))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )
        await events.record(events.Event(type=types.AUTH_LOGIN, actor_type="user", actor=username))
        return LoginResponse(token=auth.create_token(username), username=username)


@app.get("/message")
def message() -> dict[str, str]:
    return {"message": "Hello from FastAPI"}
