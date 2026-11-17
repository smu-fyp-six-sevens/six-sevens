
import re
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth, passwords
from app.auth import require_auth
from app.main import app

client = TestClient(app)

# Stored as hashes, as AUTH_USERS holds them; made once, since each takes a fraction of a second.
DEMO_HASH = passwords.hash_password("s3cret")
BOB_HASH = passwords.hash_password("bobs-pass")


@pytest.fixture(autouse=True)
def _real_auth(monkeypatch: pytest.MonkeyPatch):
    """Undo conftest's global bypass so this file exercises the real gate."""
    app.dependency_overrides.pop(require_auth, None)
    monkeypatch.setattr(auth.settings, "AUTH_USERS", {"demo": DEMO_HASH})
    monkeypatch.setattr(auth.settings, "AUTH_SECRET_KEY", "test-signing-key")
    yield


def test_login_rejects_wrong_credentials():
    response = client.post("/auth/login", json={"username": "demo", "password": "wrong"})
    assert response.status_code == 401


def test_login_issues_token_that_unlocks_protected_routes():
    login = client.post("/auth/login", json={"username": "demo", "password": "s3cret"})
    assert login.status_code == 200
    token = login.json()["token"]

    response = client.get("/cases", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code != 401


def test_protected_route_rejects_missing_token():
    response = client.get("/cases")
    assert response.status_code == 401


def test_protected_route_rejects_garbage_token():
    response = client.get("/cases", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_health_does_not_require_auth():
    response = client.get("/health")
    assert response.status_code == 200


def _signed(payload: bytes) -> str:
    payload_b64 = auth._b64encode(payload)
    return f"{payload_b64}.{auth._sign(payload_b64)}"


def test_a_token_carries_the_username_it_was_issued_to():
    assert auth._verify_token(auth.create_token("demo")) == "demo"


def test_verify_token_rejects_expired_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "TOKEN_TTL_SECONDS", -1)
    assert auth._verify_token(auth.create_token("demo")) is None


def test_verify_token_rejects_tampered_signature():
    payload_b64, _sig = auth.create_token("demo").split(".", 1)
    assert auth._verify_token(f"{payload_b64}.tampered") is None


@pytest.mark.parametrize(
    "payload",
    [b"not json", b'{"foo": 1}', b'{"exp": 9999999999}', b'{"sub": "", "exp": 9999999999}', b'{"sub": 7, "exp": 9999999999}'],
    ids=["not json", "no exp", "no username (a token from before accounts)", "blank username", "non-string username"],
)
def test_verify_token_rejects_a_correctly_signed_payload_of_the_wrong_shape(payload: bytes):
    assert auth._verify_token(_signed(payload)) is None


# --- several accounts ----------------------------------------------------------------------------


@pytest.fixture
def two_accounts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth.settings, "AUTH_USERS", {"demo": DEMO_HASH, "bob": BOB_HASH})


def test_each_account_logs_in_as_itself(two_accounts):
    for username, password in (("demo", "s3cret"), ("bob", "bobs-pass")):
        response = client.post("/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200
        assert response.json()["username"] == username
        assert auth._verify_token(response.json()["token"]) == username


def test_one_accounts_password_does_not_open_another(two_accounts):
    response = client.post("/auth/login", json={"username": "bob", "password": "s3cret"})
    assert response.status_code == 401


def test_a_protected_route_tells_the_handler_who_is_signed_in(two_accounts):
    seen: list[str] = []

    @app.get("/_whoami")
    def whoami(user: auth.CurrentUser) -> None:
        seen.append(user)

    token = client.post("/auth/login", json={"username": "bob", "password": "bobs-pass"}).json()["token"]
    client.get("/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert seen == ["bob"]


def test_with_no_accounts_nobody_can_log_in_even_with_blank_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth.settings, "AUTH_USERS", {})
    assert client.post("/auth/login", json={"username": "", "password": ""}).status_code == 401


def test_auth_users_is_a_semicolon_separated_list_of_name_colon_hash():
    from app.config import _users

    assert _users("") == {}
    assert _users(f"alice:{DEMO_HASH};bob:{BOB_HASH}") == {"alice": DEMO_HASH, "bob": BOB_HASH}
    assert _users(f" alice : {DEMO_HASH} ; ; bob:{BOB_HASH}; ") == {"alice": DEMO_HASH, "bob": BOB_HASH}


@pytest.mark.parametrize(
    "bad", ["alice", "alice:", f":{DEMO_HASH}", f"alice:{DEMO_HASH};bob", "alice:password1"],
    ids=["no colon", "no password", "no name", "one bad entry", "plain-text password"],
)
def test_a_malformed_auth_users_entry_is_refused_at_start_up(bad: str):
    from app.config import _users

    with pytest.raises(ValueError):
        _users(bad)


def test_an_unknown_name_still_runs_one_hash(monkeypatch: pytest.MonkeyPatch):
    checked: list[str] = []
    monkeypatch.setattr(auth.passwords, "verify", lambda password, stored: checked.append(stored) or False)

    assert auth.verify_credentials("ghost", "s3cret") is None
    assert checked == [passwords.DUMMY_HASH]


# The only routes that answer without a login: probes and the login itself. Pub/Sub's /push and /metrics are not in
# the API schema; the socket takes a ticket.
_PUBLIC_ROUTES = {
    ("GET", "/health"), ("GET", "/healthz"), ("GET", "/health/db"), ("POST", "/auth/login"), ("POST", "/auth/logout"),
}
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _protected_operations() -> list[tuple[str, str]]:
    return sorted(
        (method.upper(), path)
        for path, methods in app.openapi()["paths"].items()
        for method in methods
        if method.upper() in _METHODS and (method.upper(), path) not in _PUBLIC_ROUTES
    )


def test_the_public_routes_are_the_only_ones_left_out():
    operations = {(m.upper(), p) for p, methods in app.openapi()["paths"].items() for m in methods}
    assert _PUBLIC_ROUTES <= operations
    assert len(_protected_operations()) > 60


@pytest.mark.parametrize(("method", "path"), _protected_operations())
def test_every_route_but_the_public_ones_needs_a_login(method: str, path: str):
    url = re.sub(r"\{[^}]+\}", "x", path)
    body = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}

    response = client.request(method, url, **body)

    assert response.status_code == 401, f"{method} {path} answered {response.status_code} without a token"


def test_a_websocket_ticket_is_not_a_login_token():
    ticket = auth.create_ws_ticket("alice", "case-1")
    with pytest.raises(HTTPException) as refused:
        require_auth(f"Bearer {ticket}")
    assert refused.value.status_code == 401


def test_a_login_token_is_not_a_websocket_ticket():
    assert auth.consume_ws_ticket(auth.create_token("alice"), "case-1") is None


def test_a_login_token_from_before_purposes_still_works():
    old = auth._signed({"sub": "alice", "exp": int(time.time()) + 60})
    assert require_auth(f"Bearer {old}") == "alice"


def test_a_token_with_another_purpose_is_refused():
    other = auth._signed({"purpose": "reset", "sub": "alice", "exp": int(time.time()) + 60})
    with pytest.raises(HTTPException):
        require_auth(f"Bearer {other}")


# --- The session cookie (the browser's credential) ---------------------------------------------------------------

def _cookie_client() -> TestClient:
    # A client of its own, so no cookie leaks into the other tests' shared one.
    return TestClient(app)


def _session_header(token: str) -> dict[str, str]:
    # Sent by hand: the cookie is Secure, which an HTTP test client won't send back by itself.
    return {"Cookie": f"{auth.SESSION_COOKIE}={token}"}


@pytest.fixture
def whoami():
    seen: list[str] = []

    @app.get("/_whoami_cookie")
    def read(user: auth.CurrentUser) -> dict:
        seen.append(user)
        return {"user": user}

    @app.post("/_whoami_cookie")
    def write(user: auth.CurrentUser) -> dict:
        seen.append(user)
        return {"user": user}

    yield seen
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/_whoami_cookie"]


def test_login_sets_an_httponly_secure_strict_cookie_holding_the_token():
    response = _cookie_client().post("/auth/login", json={"username": "demo", "password": "s3cret"})
    assert response.status_code == 200
    header = response.headers["set-cookie"]
    assert header.startswith(f"{auth.SESSION_COOKIE}={response.json()['token']};")
    lowered = header.lower()
    assert "httponly" in lowered and "secure" in lowered and "samesite=strict" in lowered and "path=/" in lowered
    assert f"max-age={auth.TOKEN_TTL_SECONDS}" in lowered
    assert "domain=" not in lowered  # host-only: the API's own host


def test_a_failed_login_sets_no_cookie():
    response = _cookie_client().post("/auth/login", json={"username": "demo", "password": "wrong"})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_the_cookie_alone_unlocks_a_read(whoami):
    token = auth.create_token("demo")
    response = _cookie_client().get("/_whoami_cookie", headers=_session_header(token))
    assert response.status_code == 200
    assert whoami == ["demo"]


def test_a_forged_or_expired_cookie_is_refused(whoami, monkeypatch: pytest.MonkeyPatch):
    client_ = _cookie_client()
    assert client_.get("/_whoami_cookie", headers=_session_header("forged.token")).status_code == 401
    monkeypatch.setattr(auth, "TOKEN_TTL_SECONDS", -1)
    expired = auth.create_token("demo")
    assert client_.get("/_whoami_cookie", headers=_session_header(expired)).status_code == 401
    assert whoami == []


def test_a_websocket_ticket_in_the_cookie_is_refused(whoami):
    ticket = auth.create_ws_ticket("demo", "case-1")
    assert _cookie_client().get("/_whoami_cookie", headers=_session_header(ticket)).status_code == 401


def test_a_cookie_write_without_the_csrf_header_is_refused_before_the_route_runs(whoami):
    response = _cookie_client().post("/_whoami_cookie", headers=_session_header(auth.create_token("demo")))
    assert response.status_code == 403
    assert whoami == []


def test_a_cookie_write_with_the_csrf_header_goes_through(whoami):
    headers = {**_session_header(auth.create_token("demo")), "X-Requested-With": "fetch"}
    assert _cookie_client().post("/_whoami_cookie", headers=headers).status_code == 200
    assert whoami == ["demo"]


def test_a_bearer_write_needs_no_csrf_header(whoami):
    # A browser never adds a bearer header by itself, so a script's call isn't forgeable cross-site.
    headers = {"Authorization": f"Bearer {auth.create_token('demo')}"}
    assert _cookie_client().post("/_whoami_cookie", headers=headers).status_code == 200


def test_a_bearer_header_wins_over_the_cookie(whoami, two_accounts):
    headers = {**_session_header(auth.create_token("demo")), "Authorization": f"Bearer {auth.create_token('bob')}"}
    assert _cookie_client().get("/_whoami_cookie", headers=headers).status_code == 200
    assert whoami == ["bob"]


def test_a_malformed_authorization_header_is_not_saved_by_the_cookie(whoami):
    headers = {**_session_header(auth.create_token("demo")), "Authorization": "Basic abc"}
    assert _cookie_client().get("/_whoami_cookie", headers=headers).status_code == 401


def test_a_fresh_session_is_not_renewed(whoami):
    response = _cookie_client().get("/_whoami_cookie", headers=_session_header(auth.create_token("demo")))
    assert "set-cookie" not in response.headers


def test_an_aging_session_is_renewed_so_an_officer_at_work_stays_signed_in(whoami):
    issued = int(time.time()) - auth.RENEW_AFTER_SECONDS - 5
    old = auth._signed({"purpose": "login", "sub": "demo", "iat": issued, "exp": issued + auth.TOKEN_TTL_SECONDS})

    response = _cookie_client().get("/_whoami_cookie", headers=_session_header(old))

    assert response.status_code == 200
    fresh = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    payload = auth._login_payload(fresh)
    assert payload is not None and payload["sub"] == "demo"
    assert payload["exp"] >= time.time() + auth.TOKEN_TTL_SECONDS - 5  # a full idle window from now


def test_a_refused_request_renews_nothing(whoami, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "RENEW_AFTER_SECONDS", 0)
    response = _cookie_client().post("/_whoami_cookie", headers=_session_header(auth.create_token("demo")))
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_a_bearer_request_is_never_given_a_cookie(whoami, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "RENEW_AFTER_SECONDS", 0)
    headers = {"Authorization": f"Bearer {auth.create_token('demo')}"}
    response = _cookie_client().get("/_whoami_cookie", headers=headers)
    assert "set-cookie" not in response.headers


def test_logout_clears_the_cookie_even_without_a_session():
    response = _cookie_client().post("/auth/logout")
    assert response.status_code == 204
    header = response.headers["set-cookie"].lower()
    assert header.startswith(f"{auth.SESSION_COOKIE}=") and "max-age=0" in header


def test_logout_needs_no_csrf_header():
    response = _cookie_client().post("/auth/logout", headers=_session_header(auth.create_token("demo")))
    assert response.status_code == 204
