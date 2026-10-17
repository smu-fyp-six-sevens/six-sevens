from app.injection.field_text import (
    _is_label_phrase,
    _looks_like_label,
    field_value_text,
    replace_value,
    split_label_and_value,
)


def test_splits_account_no_from_value() -> None:
    assert split_label_and_value("Account No. XXXX-0472") == (
        "Account No. ",
        "XXXX-0472",
    )
    assert split_label_and_value("Account No. 12345678") == (
        "Account No. ",
        "12345678",
    )


def test_splits_colon_label() -> None:
    assert split_label_and_value("Account Number: 12345678") == (
        "Account Number: ",
        "12345678",
    )


def test_label_only_headers_are_not_values() -> None:
    for text in (
        "Opening Balance",
        "Average Balance",
        "Available Balance",
        "Description",
        "Debit $$",
        "Deposit $$",
        "Balance $$",
        "Account Number",
        "Customer Name",
        "STATEMENT OF ACCOUNT",
        "Transaction",
        "Page 1 of 2",
        "OCBC",
        "OCBC Bank",
        "Total Withdrawals",
        "Total Withdrawals / Deposits",
    ):
        assert field_value_text(text) == ""


def test_value_only_fields_keep_full_text() -> None:
    for text in (
        "JORDAN LEE",
        "Jane Citizen",
        "1,500.00",
        "ATM WITHDRAWAL",
        "MONTHLY SAVINGS ACCOUNT",
        "BALANCE BF",
        "PAYROLL CREDIT",
        "Orchard Road Branch",
        "TOTAL EQUITY AND LIABILITIES",
        "Total equity",
        "Revenue",
    ):
        assert split_label_and_value(text) == ("", text), text


def test_replace_value_preserves_label() -> None:
    assert replace_value("Account No. XXXX-0472", "YYYY-1111") == "Account No. YYYY-1111"


def test_replace_value_replaces_header_entirely() -> None:
    assert replace_value("Opening Balance", "Openign Balance") == "Openign Balance"


def test_split_label_and_value_empty_string_returns_empty_pair() -> None:
    assert split_label_and_value("") == ("", "")


def test_is_label_phrase_whitespace_only_is_not_a_label() -> None:
    assert _is_label_phrase("   ") is False
    # Falls through to the separator search below, which finds none —
    # whitespace-only text ends up classified as (empty label, the text).
    assert split_label_and_value("   ") == ("", "   ")


def test_looks_like_label_false_when_no_alpha_tokens() -> None:
    # Punctuation-only text has no words at all to judge as label-like.
    assert _looks_like_label("***") is False


def test_looks_like_label_false_when_only_stopwords() -> None:
    # Every token is a stopword ("to", "the") — no actual content word to
    # judge as a label term, so this shouldn't be treated as a label.
    assert _looks_like_label("to the") is False


def test_split_label_and_value_absorbs_extra_whitespace_after_separator() -> None:
    # " No. " already consumes one trailing space — extra whitespace beyond
    # that (a common OCR artifact) should still end up in the label, not
    # leak into the value.
    assert split_label_and_value("Account No.   XXXX-0472") == (
        "Account No.   ",
        "XXXX-0472",
    )
