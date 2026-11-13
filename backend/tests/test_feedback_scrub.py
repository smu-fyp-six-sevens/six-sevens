import pytest

from app.feedback_scrub import scrub


@pytest.mark.parametrize(("text", "expected"), [
    ("NRIC S1234567D does not match", "NRIC [id] does not match"),
    ("call 9123 4567 or +65 6123 4567", "call [number] or [number]"),
    ("account 123-456-789-0 was closed", "account [number] was closed"),
    ("mail tan@example.com about it", "mail [email] about it"),
    ("lives at Blk 22 Bedok South Ave 1 #05-01 Singapore 460022", "lives at [address] [address] [address]"),
    ("12 Orchard Road is a mall", "[address] is a mall"),
    ("born 13/02/1990, issued 2024-03-05", "born [date], issued [date]"),
    ("UEN 201912345K lapsed", "UEN [id] lapsed"),
])
def test_scrub_replaces_confidential_details(text: str, expected: str) -> None:
    assert scrub(text) == expected


def test_scrub_removes_the_names_the_case_holds_longest_first() -> None:
    text = "Acme Holdings Pte Ltd and Lakshmi Devi both signed; Acme is new"
    assert scrub(text, ["Acme", "Acme Holdings Pte Ltd", "Lakshmi Devi"]) == "[name] and [name] both signed; [name] is new"


def test_scrub_keeps_what_is_not_confidential() -> None:
    text = "Font differs on the total line; the amount is plausible."
    assert scrub(text) == text


def test_scrub_of_nothing_is_nothing() -> None:
    assert scrub(None) is None
    assert scrub("   ") is None
