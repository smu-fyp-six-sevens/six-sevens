"""
The push adapter: Pub/Sub delivers each subscription's messages to POST /push/{role}. The work runs inside
the request (Cloud Run gives no CPU outside one). 2xx acks; anything else is redelivered and, after the
subscription's maximum attempts, dead-lettered. Not behind the app's login: Pub/Sub signs each push with an
OIDC token, checked here when PUSH_AUDIENCE is set and by Cloud Run IAM on private workers. The local
emulator sends none.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, Field, ValidationError

from .. import telemetry
from ..config import settings
from . import registry
from .envelope import Event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


class _PushMessage(BaseModel):
    data: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    message_id: str | None = Field(default=None, alias="messageId")


class _PushBody(BaseModel):
    message: _PushMessage
    subscription: str | None = None


def _verify_push_token(authorization: str | None) -> None:
    """Raises 401 unless the request carries Pub/Sub's OIDC token for our audience and service account."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing push token")
    try:
        claims = id_token.verify_oauth2_token(
            authorization.removeprefix("Bearer ").strip(), google_requests.Request(), audience=settings.PUSH_AUDIENCE
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid push token") from exc
    if settings.PUSH_SERVICE_ACCOUNT and (
        claims.get("email") != settings.PUSH_SERVICE_ACCOUNT or not claims.get("email_verified")
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Push token from the wrong account")


@router.post("/push/{role}", status_code=status.HTTP_204_NO_CONTENT)
async def receive(role: str, body: _PushBody, authorization: str | None = Header(default=None)) -> Response:
    if settings.PUSH_AUDIENCE:
        await asyncio.to_thread(_verify_push_token, authorization)
    if role not in registry.installed_roles():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"This service does not run {role}")
    try:
        event = Event.model_validate_json(base64.b64decode(body.message.data))
    except (binascii.Error, ValueError, ValidationError) as exc:
        logger.error("Malformed push message %s on %s: %s", body.message.message_id, role, exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed event") from exc
    # The publisher's traceparent, so the work joins the trace of the request that caused it.
    with telemetry.continued_from(body.message.attributes):
        await registry.dispatch(role, event)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
