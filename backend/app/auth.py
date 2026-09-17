"""
Shared-credential auth: one username/password (env-configured), gating
every route except /health and /auth/login. Not a real multi-user auth
system by design — this is a public demo deployment for external
stakeholders to view, not a product with accounts, so a single signed
bearer token is enough to keep it from being wide open.

Token = base64url(payload_json) + "." + base64url(hmac_sha256(secret, payload_b64)),
where payload is just {"exp": <unix_ts>}. Verified via constant-time
comparison; no external JWT dependency needed for something this small.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import Header, HTTPException, status

from .config import settings

TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days — shared demo credential, not a per-user session


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(payload_b64: str) -> str:
    secret = settings.AUTH_SECRET_KEY.encode("utf-8")
    digest = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64encode(digest)


def create_token() -> str:
    payload = json.dumps({"exp": int(time.time()) + TOKEN_TTL_SECONDS}).encode("utf-8")
    payload_b64 = _b64encode(payload)
    return f"{payload_b64}.{_sign(payload_b64)}"


def verify_credentials(username: str, password: str) -> bool:
    # compare_digest on both to avoid leaking which of the two was wrong via timing
    valid_user = hmac.compare_digest(username, settings.AUTH_USERNAME)
    valid_pass = hmac.compare_digest(password, settings.AUTH_PASSWORD)
    return valid_user and valid_pass


def _verify_token(token: str) -> bool:
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return False

    if not hmac.compare_digest(sig, _sign(payload_b64)):
        return False

    try:
        payload = json.loads(_b64decode(payload_b64))
        exp = int(payload["exp"])
    except (ValueError, KeyError, TypeError):
        return False

    return time.time() < exp


def verify_token(token: str) -> bool:
    """
    Public entry point for verifying a token passed outside the
    Authorization header — a browser's native WebSocket API can't set
    custom headers on the handshake, so the WS route (see routers/ws.py)
    takes the token as a query param and checks it here instead of via
    require_auth.
    """
    return _verify_token(token)


def require_auth(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )

    token = authorization.removeprefix("Bearer ").strip()
    if not _verify_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
