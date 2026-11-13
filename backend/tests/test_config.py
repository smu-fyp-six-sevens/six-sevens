from __future__ import annotations

import pytest

from app.config import _bool, _float, _int


def test_a_bad_whole_number_names_its_setting(monkeypatch) -> None:
    monkeypatch.setenv("STUCK_RUNNING_SECONDS", "15m")
    with pytest.raises(ValueError, match=r"STUCK_RUNNING_SECONDS must be a whole number, got '15m'"):
        _int("STUCK_RUNNING_SECONDS", 900)


def test_a_bad_number_names_its_setting(monkeypatch) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "abc")
    with pytest.raises(ValueError, match=r"LLM_TIMEOUT_SECONDS must be a number, got 'abc'"):
        _float("LLM_TIMEOUT_SECONDS", 300)


def test_an_unset_setting_takes_its_default(monkeypatch) -> None:
    monkeypatch.delenv("SIX_SEVENS_UNSET", raising=False)
    assert _int("SIX_SEVENS_UNSET", 7) == 7
    assert _float("SIX_SEVENS_UNSET", 1.5) == 1.5
    assert _bool("SIX_SEVENS_UNSET", True) is True


def test_numbers_are_read_as_before(monkeypatch) -> None:
    monkeypatch.setenv("SIX_SEVENS_N", " 42 ")
    assert _int("SIX_SEVENS_N", 0) == 42
    assert _float("SIX_SEVENS_N", 0) == 42.0


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("TRUE", True), ("false", False), ("yes", False), ("", False)])
def test_a_switch_is_on_only_for_true(monkeypatch, raw, expected) -> None:
    monkeypatch.setenv("SIX_SEVENS_FLAG", raw)
    assert _bool("SIX_SEVENS_FLAG", True) is expected
