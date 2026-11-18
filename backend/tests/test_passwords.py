"""Officer passwords are stored as salted scrypt hashes (app/passwords.py)."""

import pytest

from app import passwords
from app.config import _users
from scripts import hash_password as script

HASH = passwords.hash_password("correct horse")


def test_a_hash_verifies_its_own_password_only():
    assert passwords.verify("correct horse", HASH)
    assert not passwords.verify("correct horse ", HASH)
    assert not passwords.verify("", HASH)


def test_the_same_password_hashes_differently_each_time():
    assert passwords.hash_password("correct horse") != HASH


def test_a_hash_holds_none_of_the_characters_auth_users_and_env_files_treat_specially():
    assert not any(c in HASH for c in ";,$# \n")
    assert HASH.startswith("scrypt:")


@pytest.mark.parametrize(
    "stored",
    ["correct horse", "", "scrypt:", "scrypt:16384:8:5:salt", "bcrypt:16384:8:5:c2FsdHNhbHQ:a2V5a2V5a2V5a2V5a2V5a2V5",
     "scrypt:1000:8:5:c2FsdHNhbHQ:a2V5a2V5a2V5a2V5a2V5a2V5", "scrypt:16384:8:5:c2FsdA:a2V5a2V5a2V5a2V5a2V5a2V5",
     "scrypt:16384:8:5:c2FsdHNhbHQ:!!!", "scrypt:1048576:64:1:c2FsdHNhbHQ:a2V5a2V5a2V5a2V5a2V5a2V5"],
    ids=["plain text", "empty", "prefix only", "too few parts", "another scheme", "n not a power of two",
         "short salt", "not base64", "too much memory"],
)
def test_a_malformed_hash_is_refused_and_never_matches(stored: str):
    assert not passwords.is_hash(stored)
    assert not passwords.verify("correct horse", stored)


def test_a_hash_made_at_another_cost_still_verifies(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(passwords, "_N", 1024)
    older = passwords.hash_password("pw")
    monkeypatch.undo()

    assert older.split(":")[1] == "1024"
    assert passwords.verify("pw", older)
    assert not passwords.verify("other", older)


def test_the_dummy_hash_is_a_valid_hash():
    assert passwords.is_hash(passwords.DUMMY_HASH)


# --- The password script (scripts/hash_password.py, PENDING_TESTS row 32) ------------------------------------------



def _run(monkeypatch, capsys, argv: list[str], answers: list[str]) -> tuple[int, str, str]:
    monkeypatch.setattr(script.sys, "argv", ["hash_password", *argv])
    replies = iter(answers)
    monkeypatch.setattr(script.getpass, "getpass", lambda prompt="": next(replies))
    code = script.main()
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_good_entry_prints_a_line_auth_users_accepts(monkeypatch, capsys):
    code, out, _ = _run(monkeypatch, capsys, ["alice"], ["correct horse", "correct horse"])

    line = out.strip()
    assert code == 0 and line.startswith("alice:scrypt:")
    stored = _users(line)["alice"]
    assert passwords.verify("correct horse", stored) and not passwords.verify("wrong", stored)


def test_two_different_entries_are_refused(monkeypatch, capsys):
    code, out, err = _run(monkeypatch, capsys, ["alice"], ["correct horse", "correct hose"])

    assert code == 1 and out == "" and "differ" in err


def test_an_empty_password_is_refused(monkeypatch, capsys):
    code, out, err = _run(monkeypatch, capsys, ["alice"], ["", ""])

    assert code == 1 and out == "" and "empty" in err


@pytest.mark.parametrize("argv", [["al:ice"], ["al;ice"], ["al ice"], [""], [], ["alice", "bob"]],
                         ids=["colon", "semicolon", "space", "blank", "none", "two names"])
def test_a_bad_name_is_refused_before_asking_for_a_password(monkeypatch, capsys, argv):
    code, out, err = _run(monkeypatch, capsys, argv, [])  # no answers: asking would raise StopIteration

    assert code == 2 and out == "" and "Usage" in err
