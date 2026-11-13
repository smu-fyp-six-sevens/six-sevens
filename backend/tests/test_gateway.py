from __future__ import annotations

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.config import settings
from app.gateway import GatewayOnly

_KEY = {"X-Gateway-Key": "s"}


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/cases")
    def cases() -> dict:
        return {"ok": True}

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/push/doc-worker")
    def push() -> dict:
        return {"ok": True}

    @app.get("/metrics")
    def metrics() -> dict:
        return {"ok": True}

    @app.websocket("/ws/cases/c1")
    async def socket(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    app.add_middleware(GatewayOnly, secret="s")
    return app


def _client(host: str = "203.0.113.5") -> TestClient:
    return TestClient(_app(), client=(host, 50000))


@pytest.mark.parametrize("headers", [{}, {"X-Gateway-Key": "wrong"}, {"X-Gateway-Key": ""}])
def test_a_request_without_kongs_key_is_refused(headers) -> None:
    response = _client().get("/cases", headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Requests must come through the gateway."}


def test_a_request_with_kongs_key_passes() -> None:
    assert _client().get("/cases", headers=_KEY).status_code == 200


def test_a_websocket_without_the_key_is_closed_with_policy_violation() -> None:
    with pytest.raises(WebSocketDisconnect) as closed:
        with _client().websocket_connect("/ws/cases/c1"):
            pass
    assert closed.value.code == 1008


def test_a_websocket_with_the_key_opens() -> None:
    with _client().websocket_connect("/ws/cases/c1", headers=_KEY) as socket:
        assert socket.receive_text() == "hello"


def test_health_and_push_are_open_without_the_key() -> None:
    client = _client()
    assert client.get("/health").status_code == 200
    assert client.post("/push/doc-worker").status_code == 200


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_metrics_from_the_container_itself_is_open(host) -> None:
    assert _client(host).get("/metrics").status_code == 200


def test_metrics_from_elsewhere_needs_the_key() -> None:
    client = _client()
    assert client.get("/metrics").status_code == 403
    assert client.get("/metrics", headers=_KEY).status_code == 200


def test_a_lookalike_path_is_not_open() -> None:
    client = _client()
    assert client.get("/healthcheck").status_code == 403
    assert client.post("/pushx").status_code == 403


@pytest.fixture
def deployed(monkeypatch):
    monkeypatch.setattr(main, "ROLES", frozenset({"api"}))
    monkeypatch.setattr(main, "IN_PROCESS", False)
    monkeypatch.delenv("PUBSUB_EMULATOR_HOST", raising=False)
    monkeypatch.setattr(settings, "GATEWAY_SECRET", "")


def test_a_deployed_api_without_the_secret_refuses_to_start(deployed) -> None:
    with pytest.raises(RuntimeError, match="GATEWAY_SECRET"):
        main.check_gateway_secret()


def test_a_deployed_api_with_the_secret_starts(deployed, monkeypatch) -> None:
    monkeypatch.setattr(settings, "GATEWAY_SECRET", "s")
    main.check_gateway_secret()


def test_the_secret_is_not_needed_locally_or_off_the_gateway(deployed, monkeypatch) -> None:
    monkeypatch.setattr(main, "IN_PROCESS", True)
    main.check_gateway_secret()
    monkeypatch.setattr(main, "IN_PROCESS", False)
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "localhost:8085")
    main.check_gateway_secret()
    monkeypatch.delenv("PUBSUB_EMULATOR_HOST")
    monkeypatch.setattr(main, "ROLES", frozenset({"doc-worker"}))
    main.check_gateway_secret()
