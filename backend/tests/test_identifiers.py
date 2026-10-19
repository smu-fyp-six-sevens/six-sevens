from __future__ import annotations

from app.identifiers import alnum, company_key, id_key, linked_people, name_key, uen_key


def test_alnum_keeps_letters_and_digits_upper_case() -> None:
    assert alnum("s1234-567a") == "S1234567A"
    assert alnum(None) == "" and alnum(12345) == ""


def test_id_key_ignores_spaces_and_punctuation() -> None:
    assert id_key("S 1234567-A") == "S1234567A"
    assert id_key("!!!") is None and id_key(None) is None


def test_uen_key_pulls_the_uen_out_of_a_labelled_value() -> None:
    assert uen_key("UEN: 2019-12345K") == "201912345K"
    assert uen_key("t08ll1234a") == "T08LL1234A"
    assert uen_key("!!!") is None and uen_key(12345) is None


def test_name_key_ignores_order_case_and_punctuation() -> None:
    assert name_key("Tan, Wei Ming") == name_key("WEI MING TAN") == "MING TAN WEI"
    assert name_key("") is None and name_key(None) is None


def test_company_key_drops_the_legal_suffix_and_treats_ampersand_as_and() -> None:
    assert company_key("ACME Pte. Ltd.") == company_key("Acme Private Limited") == "ACME"
    assert company_key("Smith & Sons Pte Ltd") == "SMITH AND SONS"
    assert company_key("A.C.M.E.") != company_key("ACME")


def test_linked_people_pairs_a_person_by_id_even_when_the_name_differs() -> None:
    [(a, b)] = linked_people(
        {"individual_name": "Tan Wei Ming", "nric_number": "S1234567A"},
        {"individual_name": "Tan Wei Ming", "nric_number": "S 1234567 A"},
    )
    assert id_key(a.id_number) == id_key(b.id_number)
