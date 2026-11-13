import pytest

from app.feedback_scrub import names_in, scrub


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


def test_names_come_from_any_field_that_names_a_person_or_company() -> None:
    fields = {
        "employer_name": {"value": "Acme Holdings Pte Ltd"},
        "account_holder": "Tan Wei Ming",
        "directors": '[{"name": "Lakshmi Devi", "id_number": "S1234567D"}, {"name": "Raj Kumar"}]',
        "net_pay": {"value": "1000"},
    }

    names = names_in(fields, extra=["Acme", None])

    assert sorted(names) == ["Acme", "Acme Holdings Pte Ltd", "Lakshmi Devi", "Raj Kumar", "Tan Wei Ming"]


def test_one_word_of_a_name_is_scrubbed_on_its_own() -> None:
    assert scrub("Wei signed the form", ["Tan Wei Ming"]) == "[name] signed the form"


def test_amounts_are_scrubbed_with_or_without_a_currency() -> None:
    assert scrub("paid S$ 5,000 and 1,250.00 in total") == "paid [amount] and [amount] in total"


def test_the_words_every_company_has_are_never_scrubbed_alone() -> None:
    text = "A Pte Ltd must file its accounts"
    assert scrub(text, ["Acme Holdings Pte Ltd"]) == text
