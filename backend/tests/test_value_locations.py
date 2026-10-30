from __future__ import annotations

import pymupdf

from app.langgraph.detection import value_locations


def _pdf(lines: list[tuple[float, float, str]]) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page()
    for x, y, text in lines:
        page.insert_text((x, y), text)
    return pymupdf.open(stream=doc.tobytes(), filetype="pdf")


def _lines(finding: dict) -> list[int]:
    """The line (the baseline its text was written at, lines being 40 pt apart) of each location."""
    return [round((loc["bbox"][1] + loc["bbox"][3]) / 2 / 20) * 20 for loc in finding["locations"]]


def test_a_date_is_found_however_it_is_printed() -> None:
    doc = _pdf([(72, 100, "Contract end date"), (300, 100, "10 Mar 2032")])
    finding = {"tool": "date_consistency_tool", "field": "contract_end_date", "stated": None}

    placed = value_locations.locate(doc, finding, {"contract_end_date": "2032-03-10"})

    assert _lines(placed) == [100]


def test_a_zero_is_only_boxed_beside_its_label() -> None:
    doc = _pdf([
        (72, 100, "Deposit"), (300, 100, "$0.00"),
        (72, 140, "Outstanding Balance"), (300, 140, "$0.00"),
    ])
    finding = {"tool": "numerical_checks_tool", "field": "outstanding_balance", "stated": "0.00"}

    assert _lines(value_locations.locate(doc, finding, {})) == [140]
    # With no label on the page, a zero is too common to point at.
    unlabelled = {**finding, "field": "amount_due"}
    assert value_locations.locate(doc, unlabelled, {})["locations"] == []


def test_a_number_inside_a_longer_number_is_not_a_match() -> None:
    doc = _pdf([(72, 100, "Net pay 14,095.77")])
    finding = {"tool": "numerical_checks_tool", "field": "net_pay", "stated": "4,095.77"}

    assert value_locations.locate(doc, finding, {})["locations"] == []


def test_a_row_value_is_taken_from_its_rows_line() -> None:
    doc = _pdf([
        (72, 100, "GIRO RENTAL JUN"), (400, 100, "1,250.00"),
        (72, 140, "GIRO RENTAL JUL"), (400, 140, "1,250.00"),
    ])
    rows = [{"description": "GIRO RENTAL JUN", "balance": 1250}, {"description": "GIRO RENTAL JUL", "balance": 1250}]
    finding = {"tool": "numerical_checks_tool", "field": "transactions[1].balance", "stated": "1,250.00"}

    assert _lines(value_locations.locate(doc, finding, {"transactions": rows})) == [140]


def test_a_finding_about_a_list_boxes_the_rows_its_summary_names() -> None:
    doc = _pdf([
        (72, 100, "CARD MART"), (400, 100, "35.00"),
        (72, 140, "SERVICE CHARGE"), (400, 140, "12.00"),
        (72, 180, "CARD MART"), (400, 180, "35.00"),
    ])
    rows = [
        {"description": "CARD MART", "amount": 35},
        {"description": "SERVICE CHARGE", "amount": 12},
        {"description": "CARD MART", "amount": 35},
    ]
    finding = {
        "tool": "numerical_checks_tool", "field": "transactions", "stated": None,
        "summary": "Transaction 2 repeats the same payee and amount as transaction 2",
    }

    placed = value_locations.locate(doc, finding, {"transactions": rows})

    [row] = placed["locations"]
    assert row["bbox"][0] < 80 and row["bbox"][2] > 400 and row["bbox"][1] < 140 < row["bbox"][3]


def test_a_finding_with_nothing_on_the_page_has_no_locations() -> None:
    doc = _pdf([(72, 100, "Officers")])
    finding = {"tool": "numerical_checks_tool", "field": "officers", "stated": None,
               "summary": "No director is listed among the officers"}

    assert value_locations.locate(doc, finding, {"officers": []})["locations"] == []


def test_a_registry_finding_boxes_the_uen() -> None:
    doc = _pdf([(72, 100, "OF UEN"), (72, 140, "201878901H")])
    finding = {"tool": "external_validation_tool", "field": "uen", "stated": "201878901H"}

    assert _lines(value_locations.locate(doc, finding, {})) == [140]


def test_an_address_is_boxed_from_its_first_line_to_its_postal_code() -> None:
    doc = _pdf([
        (72, 100, "8 BEDOK RESERVOIR ROAD"), (400, 100, "Page 1 of 2"),
        (72, 114, "#08-50"),
        (72, 128, "SINGAPORE 101897"),
    ])
    finding = {
        "tool": "external_validation_tool", "field": "individual_address",
        "stated": "8 BEDOK RESERVOIR ROAD #08-50 SINGAPORE 101897",
    }

    [area] = value_locations.locate(doc, finding, {})["locations"]

    x0, y0, x1, y1 = area["bbox"]
    assert y0 < 100 and y1 > 128
    # The whole first line, but not the other column beside it.
    assert 150 < x1 < 400
