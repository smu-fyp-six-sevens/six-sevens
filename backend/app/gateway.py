"""
Only Kong may call the public services (api and audit-metrics). Both are reachable on their own run.app URLs, so
without this anyone could go around Kong's login and upload rate limits. Kong adds X-Gateway-Key, a shared secret
(Secret Manager, GATEWAY_SECRET), to every request it forwards (deploy/kong/kong.yml.tmpl); any request without the
right one is refused here, before routing, for HTTP and WebSocket alike.

Left open: /health (probes, and it says nothing), /push/... (Pub/Sub's own signed token, events/push.py), and
/metrics from the container itself (the Managed Prometheus sidecar scrapes over localhost).
"""

from __future__ import annotations

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

HEADER = b"x-gateway-key"
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _is_open(scope: Scope) -> bool:
    path = scope.get("path") or ""
    if path == "/health" or path.startswith("/push/"):
        return True
    client = scope.get("client")
    return path == "/metrics" and client is not None and client[0] in _LOOPBACK


def _sent_by_gateway(scope: Scope, secret: str) -> bool:
    for name, value in scope.get("headers") or []:
        if name == HEADER:
            return hmac.compare_digest(value, secret.encode())
    return False


class GatewayOnly:
    """ASGI middleware: refuses (403, or a closed WebSocket) any request that didn't come through Kong."""

    def __init__(self, app: ASGIApp, secret: str) -> None:
        self.app = app
        self.secret = secret

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or _is_open(scope) or _sent_by_gateway(scope, self.secret):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # Closing before accepting refuses the handshake.
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse({"detail": "Requests must come through the gateway."}, status_code=403)
        await response(scope, receive, send)
