import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.auth import require_auth
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _real_auth(monkeypatch: pytest.MonkeyPatch):
    """Undo conftest's global bypass so this file exercises the real gate."""
    app.dependency_overrides.pop(require_auth, None)
    monkeypatch.setattr(auth.settings, "AUTH_USERS", {"demo": "s3cret"})
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
    monkeypatch.setattr(auth.settings, "AUTH_USERS", {"demo": "s3cret", "bob": "bobs-pass"})


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


def test_auth_users_is_a_semicolon_separated_list_of_name_colon_password():
    from app.config import _users

    assert _users("") == {}
    assert _users("alice:pw1;bob:pw2") == {"alice": "pw1", "bob": "pw2"}
    assert _users(" alice : pw1 ; ; bob:pw2; ") == {"alice": "pw1", "bob": "pw2"}
    assert _users("carol:pa:ss") == {"carol": "pa:ss"}  # a password may contain a colon


@pytest.mark.parametrize("bad", ["alice", "alice:", ":pw", "alice:pw1;bob"], ids=["no colon", "no password", "no name", "one bad entry"])
def test_a_malformed_auth_users_entry_is_refused_at_start_up(bad: str):
    from app.config import _users

    with pytest.raises(ValueError):
        _users(bad)
