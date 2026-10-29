from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import app_settings_repository
from app.auth import require_auth
from app.main import app
from tests.conftest import TEST_USER

client = TestClient(app)


@pytest.fixture
def stored():
    with patch("app.routers.settings.app_settings_repository") as repo:
        repo.get_finding_reviews_enabled.return_value = True
        yield repo


def _signed_in_as(username: str) -> None:
    app.dependency_overrides[require_auth] = lambda: username


@pytest.mark.parametrize("username, admin", [("carol", True), (TEST_USER, False)])
def test_every_officer_reads_the_settings_and_learns_whether_they_are_admin(stored, username, admin) -> None:
    _signed_in_as(username)

    response = client.get("/settings")

    assert response.status_code == 200
    assert response.json() == {"finding_reviews_enabled": True, "is_admin": admin}


def test_an_admin_turns_finding_reviews_off(stored) -> None:
    _signed_in_as("carol")

    response = client.put("/settings/finding-reviews", json={"enabled": False})

    assert response.status_code == 200
    assert response.json() == {"finding_reviews_enabled": False, "is_admin": True}
    stored.set_finding_reviews_enabled.assert_called_once_with(False, "carol")


def test_an_officer_who_is_not_admin_cannot_change_it(stored) -> None:
    _signed_in_as(TEST_USER)

    response = client.put("/settings/finding-reviews", json={"enabled": False})

    assert response.status_code == 403
    stored.set_finding_reviews_enabled.assert_not_called()


def test_a_failed_save_says_to_create_the_table(stored) -> None:
    _signed_in_as("carol")
    stored.set_finding_reviews_enabled.side_effect = RuntimeError("relation app_settings does not exist")

    response = client.put("/settings/finding-reviews", json={"enabled": True})

    assert response.status_code == 502
    assert "app_settings.sql" in response.json()["detail"]


@pytest.mark.parametrize("rows, expected", [([], True), ([{"value": True}], True), ([{"value": False}], False)])
def test_reviews_are_on_unless_stored_off(rows, expected) -> None:
    with patch.object(app_settings_repository, "service_supabase_client") as supabase:
        supabase.return_value.table.return_value.select.return_value.eq.return_value.limit.return_value \
            .execute.return_value.data = rows
        assert app_settings_repository.get_finding_reviews_enabled() is expected


def test_reviews_stay_on_when_the_table_is_missing() -> None:
    with patch.object(app_settings_repository, "service_supabase_client", side_effect=RuntimeError("no table")):
        assert app_settings_repository.get_finding_reviews_enabled() is True
