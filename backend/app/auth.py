"""
A few named accounts (env-configured), gating every route except /health and
/auth/login. The signed-in username travels in the bearer token and is what the
audit trail records as the actor. No roles, resets or lockouts by design: this is
a small demo deployment, not a product with an account system.

Token = base64url(payload_json) + "." + base64url(hmac_sha256(secret, payload_b64)),
where payload is {"sub": <username>, "exp": <unix_ts>}. Verified via constant-time
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


def create_token(username: str) -> str:
    payload = json.dumps({"sub": username, "exp": int(time.time()) + TOKEN_TTL_SECONDS}).encode("utf-8")
    payload_b64 = _b64encode(payload)
    return f"{payload_b64}.{_sign(payload_b64)}"


def verify_credentials(username: str, password: str) -> str | None:
    """The matching account's username, or None. Checks every account so timing doesn't reveal which names exist."""
    matched = None
    for name, expected in settings.AUTH_USERS.items():
        user_ok = hmac.compare_digest(username.encode("utf-8"), name.encode("utf-8"))
        password_ok = hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8"))
        if user_ok and password_ok:
            matched = name
    return matched


def _verify_token(token: str) -> str | None:
    """The username the token was issued to, or None if it is forged, expired or has no username."""
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return None

    if not hmac.compare_digest(sig, _sign(payload_b64)):
        return None

    try:
        payload = json.loads(_b64decode(payload_b64))
        exp = int(payload["exp"])
        username = payload["sub"]
    except (ValueError, KeyError, TypeError):
        return None

    if not isinstance(username, str) or not username or time.time() >= exp:
        return None
    return username


def create_ws_ticket() -> str:
    """
    Called from POST /ws/tickets, which sits behind require_auth — only reachable with a valid bearer
    token already. Signed like the login token but with its own purpose, so neither works as the other.
    """
    payload = json.dumps({
        "purpose": _WS_PURPOSE, "nonce": secrets.token_urlsafe(16), "exp": time.time() + WS_TICKET_TTL_SECONDS,
    }).encode("utf-8")
    payload_b64 = _b64encode(payload)
    return f"{payload_b64}.{_sign(payload_b64)}"


def consume_ws_ticket(ticket: str) -> bool:
    """True for a genuine, unexpired ticket not yet used on this instance."""
    try:
        payload_b64, sig = ticket.split(".", 1)
        if not hmac.compare_digest(sig, _sign(payload_b64)):
            return False
        payload = json.loads(_b64decode(payload_b64))
        nonce, expires_at = str(payload["nonce"]), float(payload["exp"])
        if payload.get("purpose") != _WS_PURPOSE:
            return False
    except (ValueError, KeyError, TypeError):
        return False
    now = time.time()
    for used, expiry in list(_used_ws_tickets.items()):
        if expiry <= now:
            _used_ws_tickets.pop(used, None)
    if now >= expires_at or nonce in _used_ws_tickets:
        return False
    _used_ws_tickets[nonce] = expires_at
    return True


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
