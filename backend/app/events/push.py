"""
The push adapter: Pub/Sub delivers each subscription's messages to POST /push/{role}. The work runs inside
the request (Cloud Run gives no CPU outside one). 2xx acks; anything else is redelivered and, after the
subscription's maximum attempts, dead-lettered. Not behind the app's login: in the cloud only the Pub/Sub
service account may invoke a worker (Cloud Run IAM checks its OIDC token); the local emulator sends none.
"""

from __future__ import annotations

import base64
import binascii
import logging

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, ValidationError

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


@router.post("/push/{role}", status_code=status.HTTP_204_NO_CONTENT)
async def receive(role: str, body: _PushBody) -> Response:
    if role not in registry.installed_roles():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"This service does not run {role}")
    try:
        event = Event.model_validate_json(base64.b64decode(body.message.data))
    except (binascii.Error, ValueError, ValidationError) as exc:
        logger.error("Malformed push message %s on %s: %s", body.message.message_id, role, exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed event") from exc
    await registry.dispatch(role, event)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
