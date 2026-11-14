"""
A few named accounts (env-configured), gating every route except /health, /health/db,
/auth/login, /metrics and /push. The signed-in username travels in the bearer token and is what the
audit trail records as the actor. No roles, resets or lockouts by design: this is
a small demo deployment, not a product with an account system.

Token = base64url(payload_json) + "." + base64url(hmac_sha256(secret, payload_b64)),
where payload is {"purpose": "login", "sub": <username>, "exp": <unix_ts>} (a WebSocket ticket is
the same with purpose "ws"; _signed and _unsigned make and read both). Verified via constant-time
comparison; no external JWT dependency needed for something this small.
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

from fastapi import Depends, Header, HTTPException, status

from . import passwords
from .config import settings

TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

# Short-lived, single-use tickets for the case status WebSocket (see
# routers/ws.py) — a browser's native WebSocket API can't set a custom
# Authorization header on the handshake, so the long-lived TOKEN_TTL_SECONDS
# bearer credential can't safely travel as a query param (Cloud Run's default
# access logging captures full request URLs). A client with a valid bearer
# token mints one of these via POST /ws/tickets (normal Authorization-header
# auth) and puts only this in the WS URL instead — 10 seconds is generous for
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
    return _signed({"purpose": _LOGIN_PURPOSE, "sub": username, "exp": int(time.time()) + TOKEN_TTL_SECONDS})


def verify_credentials(username: str, password: str) -> str | None:
    """
    The matching account's username, or None. Exactly one slow hash runs either way (a dummy one for an unknown
    name), so timing doesn't reveal which names exist. Blocking (~tens of ms): call via asyncio.to_thread.
    """
    stored = settings.AUTH_USERS.get(username)
    matched = passwords.verify(password, stored if stored is not None else passwords.DUMMY_HASH)
    return username if matched and stored is not None else None


def _verify_token(token: str) -> str | None:
    """
    The username the token was issued to, or None if it is forged, expired, has no username, or is a WebSocket
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
    return username


def create_ws_ticket(username: str | None = None, case_id: str | None = None) -> str:
    """
    Called from POST /ws/tickets, which sits behind require_auth — only reachable with a valid bearer
    token already. Signed like the login token but with its own purpose, so neither works as the other.
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


def require_auth(authorization: str | None = Header(default=None)) -> str:
    """Rejects a request without a valid token; returns the signed-in username."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )

    token = authorization.removeprefix("Bearer ").strip()
    username = _verify_token(token)
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
