from app.injection.field_text import (
    field_value_text,
    is_label_only,
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
    ):
        assert is_label_only(text), text
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
    ):
        assert split_label_and_value(text) == ("", text), text
        assert not is_label_only(text)


def test_replace_value_preserves_label() -> None:
    assert replace_value("Account No. XXXX-0472", "YYYY-1111") == "Account No. YYYY-1111"


def test_replace_value_rejects_label_only() -> None:
    try:
        replace_value("Opening Balance", "1.00")
    except ValueError as exc:
        assert "label" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")
