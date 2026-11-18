"""
A few named accounts (env-configured), gating every route except /health, /health/db,
/auth/login, /metrics and /push. The signed-in username travels in the token and is what the
audit trail records as the actor. No roles, resets or lockouts by design: this is
a small demo deployment, not a product with an account system.

Token = base64url(payload_json) + "." + base64url(hmac_sha256(secret, payload_b64)),
where payload is {"purpose": "login", "sub": <username>, "iat": <unix_ts>, "exp": <unix_ts>} (a WebSocket
ticket is the same with purpose "ws"; _signed and _unsigned make and read both). Verified via constant-time
comparison; no external JWT dependency needed for something this small.

The token travels only in an HttpOnly cookie (SESSION_COOKIE), so page scripts can't read it; a script sends the
token /auth/login returns as that same cookie (no bearer header is accepted). A session ends after
TOKEN_TTL_SECONDS without a request: every request on a token older than RENEW_AFTER_SECONDS gets a fresh cookie (session_cookies), so an officer at work is never
signed out. A request authenticated by the cookie that changes something must carry X-Requested-With, which a
browser only sends cross-origin after a CORS check Kong allows for the frontend alone: that is the CSRF guard.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from contextvars import ContextVar
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from . import passwords
from .config import settings

# Idle limit: a session ends this long after its last request, never in the middle of work.
TOKEN_TTL_SECONDS = 12 * 60 * 60
# How old a token gets before a request swaps it for a fresh one. Kong's per-officer rate limits count per cookie
# value, so renewing on every request would reset them.
RENEW_AFTER_SECONDS = 60 * 60
SESSION_COOKIE = "six_session"
CSRF_HEADER = "x-requested-with"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# The only cookie-authenticated writes that need no CSRF header: signing in (no session yet) and out.
_CSRF_EXEMPT = frozenset({"/auth/login", "/auth/logout"})

# Short-lived, single-use tickets for the case status WebSocket (see
# routers/ws.py). The session cookie belongs to the API's host, and the login
# token must never travel as a query param (access logs capture full request
# URLs). A signed-in client mints one of these via POST /ws/tickets
# and puts only this in the WS URL instead — 10 seconds is generous for
# "mint it, then immediately open the socket" but tight enough that a leaked
# log line is worthless by the time anyone reads it. Signed, not stored, so any
# api instance accepts a ticket another minted (api can scale out). Single use
# per instance: a replay within the 10 s reaching a second instance would pass.
WS_TICKET_TTL_SECONDS = 10
_WS_PURPOSE = "ws"
_used_ws_tickets: dict[str, float] = {}  # nonce -> expiry, so a ticket can't be reused on this instance


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(payload_b64: str) -> str:
    secret = settings.AUTH_SECRET_KEY.encode("utf-8")
    digest = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64encode(digest)


def _signed(payload: dict) -> str:
    """The one way a token or ticket is made: base64url(payload JSON) + "." + its HMAC."""
    payload_b64 = _b64encode(json.dumps(payload).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64)}"


def _unsigned(token: str) -> dict | None:
    """The one way one is read: its payload, or None if it is malformed or its signature doesn't verify."""
    try:
        payload_b64, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(payload_b64)):
            return None
        payload = json.loads(_b64decode(payload_b64))
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


_LOGIN_PURPOSE = "login"


def create_token(username: str) -> str:
    now = int(time.time())
    return _signed({"purpose": _LOGIN_PURPOSE, "sub": username, "iat": now, "exp": now + TOKEN_TTL_SECONDS})


def verify_credentials(username: str, password: str) -> str | None:
    """
    The matching account's username, or None. Exactly one slow hash runs either way (a dummy one for an unknown
    name), so timing doesn't reveal which names exist. Blocking (~tens of ms): call via asyncio.to_thread.
    """
    stored = settings.AUTH_USERS.get(username)
    matched = passwords.verify(password, stored if stored is not None else passwords.DUMMY_HASH)
    return username if matched and stored is not None else None


def _login_payload(token: str) -> dict | None:
    """
    The payload of a valid login token, or None if it is forged, expired, has no username, or is a WebSocket
    ticket (which names its officer too, so only its purpose tells them apart). A token from before tokens carried
    a purpose has none, and still counts.
    """
    payload = _unsigned(token)
    if payload is None or payload.get("purpose", _LOGIN_PURPOSE) != _LOGIN_PURPOSE:
        return None
    try:
        exp = int(payload["exp"])
        username = payload["sub"]
    except (ValueError, KeyError, TypeError):
        return None

    if not isinstance(username, str) or not username or time.time() >= exp:
        return None
    return payload


def _verify_token(token: str) -> str | None:
    """The username the token was issued to, or None (see _login_payload)."""
    payload = _login_payload(token)
    return payload["sub"] if payload is not None else None


def set_session_cookie(response: Response, token: str) -> None:
    # Host-only (no Domain): sent to the API's own host and nowhere else.
    response.set_cookie(
        SESSION_COOKIE, token, max_age=TOKEN_TTL_SECONDS, path="/", httponly=True,
        secure=settings.AUTH_COOKIE_SECURE, samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, secure=settings.AUTH_COOKIE_SECURE, samesite="strict",
    )


def _needs_renewal(token: str) -> str | None:
    """The username to issue a fresh token to, when a valid token is old enough to swap; else None."""
    payload = _login_payload(token)
    if payload is None:
        return None
    try:
        issued = int(payload.get("iat") or 0)
    except (TypeError, ValueError):
        issued = 0
    return payload["sub"] if time.time() - issued >= RENEW_AFTER_SECONDS else None


async def session_cookies(request: Request, call_next):
    """
    HTTP middleware: refuses a cookie-authenticated write without the CSRF header, and renews an aging session
    cookie on the way out.
    """
    cookie = request.cookies.get(SESSION_COOKIE)
    if (
        cookie and request.method not in _SAFE_METHODS
        and request.url.path not in _CSRF_EXEMPT and not request.headers.get(CSRF_HEADER)
    ):
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": "Missing X-Requested-With header"})
    response = await call_next(request)
    if cookie and response.status_code < 400 and request.url.path not in _CSRF_EXEMPT:
        username = _needs_renewal(cookie)
        if username is not None:
            set_session_cookie(response, create_token(username))
    return response


def create_ws_ticket(username: str | None = None, case_id: str | None = None) -> str:
    """
    Called from POST /ws/tickets, which sits behind require_auth — only reachable signed in already. Signed like the login token but with its own purpose, so neither works as the other.
    The route names the officer and the case: a leaked ticket opens only that case, and says whose it was.
    """
    return _signed({
        "purpose": _WS_PURPOSE, "nonce": secrets.token_urlsafe(16), "exp": time.time() + WS_TICKET_TTL_SECONDS,
        "sub": username, "case_id": case_id,
    })


def consume_ws_ticket(ticket: str, case_id: str | None = None) -> str | None:
    """
    For a genuine, unexpired ticket not yet used on this instance, and made for this case if it names one: the
    officer it was made for ("unknown" when it names none). Otherwise None.
    """
    payload = _unsigned(ticket)
    if payload is None or payload.get("purpose") != _WS_PURPOSE:
        return None
    try:
        nonce, expires_at = str(payload["nonce"]), float(payload["exp"])
    except (ValueError, KeyError, TypeError):
        return None
    if payload.get("case_id") is not None and payload["case_id"] != case_id:
        return None
    now = time.time()
    for used, expiry in list(_used_ws_tickets.items()):
        if expiry <= now:
            _used_ws_tickets.pop(used, None)
    if now >= expires_at or nonce in _used_ws_tickets:
        return None
    _used_ws_tickets[nonce] = expires_at
    return str(payload.get("sub") or "unknown")


def require_auth(
    # Out of the schema: it's the browser's own credential, not something a client fills in.
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE, include_in_schema=False),
) -> str:
    """
    Rejects a request without a valid session cookie; returns the username. The cookie is the only credential: a
    script logs in and sends the token it gets back as that cookie (deploy/loadtest/run.py).
    """
    # Called directly (tests), an omitted argument is FastAPI's marker, not None.
    session = session if isinstance(session, str) else None
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")

    username = _verify_token(session)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return username


# A handler takes `user: CurrentUser` to learn who is acting; the actor is always read from the token.
CurrentUser = Annotated[str, Depends(require_auth)]


# The signed-in officer for the current request, so events record who acted without every handler passing it.
_actor: ContextVar[str | None] = ContextVar("actor", default=None)


async def bind_actor(user: CurrentUser) -> str:
    """Router-level dependency: require_auth, then remember the user. Async, so the value reaches the handler."""
    _actor.set(user)
    return user


def current_actor() -> str | None:
    return _actor.get()
