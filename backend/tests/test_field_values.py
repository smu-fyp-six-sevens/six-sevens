from __future__ import annotations

from app.fraud_detection.field_values import text_values


def test_walks_lists_rows_and_json_strings_with_paths_top_level_first() -> None:
    fields = {
        "shareholders": [{"name": "NG BEE LENG", "address": "22 Bedok North Avenue 3"}],
        "individual_address": ["12 Yishun Street 21"],
        "officers": '[{"name": "CHIA WEI MING"}]',  # a table stored as a JSON string
        "company_name": '"Meridian Pte Ltd"',  # a doubly-encoded string
        "issued_shares": 500000,
        "blank": " ",
    }
    found = [(v.path, v.key, v.text) for v in text_values(fields)]
    assert found == [
        ("company_name", "company_name", "Meridian Pte Ltd"),
        ("shareholders[0].name", "name", "NG BEE LENG"),
        ("shareholders[0].address", "address", "22 Bedok North Avenue 3"),
        ("individual_address[0]", "individual_address", "12 Yishun Street 21"),
        ("officers[0].name", "name", "CHIA WEI MING"),
    ]


def test_describe_path_reads_like_the_frontend_field_labels() -> None:
    from app.fraud_detection.field_values import also_printed_in, describe_path

    fields = {
        "shareholders": [{"name": "CHIA WEI MING", "identification_no": "S8934521C"},
                         {"name": "NG BEE LENG", "identification_no": "S7845632F"}],
        "officers": '[{"name": "TEO LI XUAN", "address": "x"}]',
        "individual_address": ["a", "b"],
    }
    assert describe_path(fields, "shareholders[1].identification_no") == "Shareholder 2 (NG BEE LENG), Identification No"
    assert describe_path(fields, "officers[0].address") == "Officer 1 (TEO LI XUAN), Address"
    assert describe_path(fields, "individual_address[1]") == "Individual Address 2"
    assert describe_path(fields, "nric_number") == "NRIC Number"
    assert also_printed_in(fields, []) == ""
    assert also_printed_in(fields, ["nric_number", "individual_address[0]"]) == \
        ". Also printed in NRIC Number and Individual Address 1"
