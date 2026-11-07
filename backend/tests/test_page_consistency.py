"""Cross-page consistency (fraud_detection/pages): genuine statements pass, and each page-level forgery is
reported once, without repeating what BS.RUNNING_BALANCE already reports."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pymupdf

from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.pages import (
    NOTHING_TO_CHECK_NOT_PDF, NOTHING_TO_CHECK_SINGLE_PAGE, check_page_consistency,
)
from app.fraud_detection.pages.carry_forward import read_carries
from app.fraud_detection.pages.page_view import read_pages
from app.injection.page_defects import delete_page, duplicate_page, edit_carry_line, swap_page
from tests.support.statements import A4, Statement, make_statement

def _check(pdf: bytes, fields: list[ExtractedField] | None = None, filename: str = "statement.pdf"):
    return asyncio.run(check_page_consistency("doc-1", pdf, filename, fields))


def _codes(result) -> list[str]:
    return [f.check_code for f in result.findings]


# ---------------------------------------------------------------------------
# Scope (AC1) and genuine documents (AC10)
# ---------------------------------------------------------------------------


def test_a_genuine_statement_has_no_findings() -> None:
    statement = make_statement()
    result = _check(statement.pdf, statement.fields())
    assert result.unable_to_assess is False
    assert result.findings == []
    assert result.pages_checked == 3


def test_single_page_and_image_uploads_have_nothing_to_check() -> None:
    from app.langgraph.detection.tools import _is_nothing_to_check

    single = _check(make_statement(pages=1).pdf)
    image = _check(b"not used", filename="scan.png")
    for result, reason in ((single, NOTHING_TO_CHECK_SINGLE_PAGE), (image, NOTHING_TO_CHECK_NOT_PDF)):
        assert result.unable_to_assess and result.unable_to_assess_reason == reason
        assert _is_nothing_to_check(reason)  # a pass with nothing to report, not an inconclusive verdict


def test_a_statement_with_a_terms_page_of_another_size_is_genuine() -> None:
    statement = make_statement(terms_page=True)
    assert _check(statement.pdf, statement.fields()).findings == []


def test_a_consolidated_statement_restarting_its_numbering_per_account_is_genuine() -> None:
    first, second = make_statement(pages=2), make_statement(pages=3, account="987-654321-0")
    doc = pymupdf.open(stream=first.pdf, filetype="pdf")
    doc.insert_pdf(pymupdf.open(stream=second.pdf, filetype="pdf"))
    result = _check(doc.tobytes())
    assert "PAGE.NUMBERING" not in _codes(result)


# ---------------------------------------------------------------------------
# PAGE.NUMBERING (AC2)
# ---------------------------------------------------------------------------


def test_a_deleted_page_is_reported_once_as_missing() -> None:
    statement = make_statement(pages=4)
    pdf = delete_page(statement.pdf, 1)
    rows = [r for r in statement.transactions if not r["date"].startswith("02-")]  # what extraction would see
    result = _check(pdf, Statement(pdf, rows).fields())
    [finding] = result.findings
    assert finding.check_code == "PAGE.NUMBERING"
    assert "page 2 missing" in finding.summary
    # The page numbers either side of the gap are boxed where they're printed, top right.
    assert [(place.page, place.text) for place in finding.places] == [(1, "Page 1 of 4"), (2, "Page 3 of 4")]
    assert all(place.bbox[0] >= 460 and place.bbox[3] <= 40 for place in finding.places)
    # The rows break across the gap, so BS.RUNNING_BALANCE reports the balances: the carry check doesn't repeat it.
    assert not any("brings forward" in e and "carries forward" in e for e in finding.evidence)


def test_missing_last_pages_and_a_disagreeing_total_are_reported() -> None:
    statement = make_statement(pages=4)
    assert "missing at the end" in _check(delete_page(statement.pdf, 3)).findings[0].summary


# ---------------------------------------------------------------------------
# PAGE.CARRY_FORWARD (AC4)
# ---------------------------------------------------------------------------


def test_carry_lines_are_read_from_the_balance_column_on_the_same_row() -> None:
    statement = make_statement(pages=2, opening=1_000.00)
    first, second = read_pages(statement.pdf, "s.pdf")
    assert read_carries(first).brought == 1_000.00
    assert read_carries(first).carried == statement.transactions[3]["balance"]
    assert read_carries(second).brought == statement.transactions[3]["balance"]


def test_an_edited_carry_line_is_one_finding_with_both_pieces_of_evidence() -> None:
    statement = make_statement()
    pdf = edit_carry_line(statement.pdf, 1, "brought", "99,999.99")
    [finding] = _check(pdf, statement.fields()).findings
    assert finding.check_code == "PAGE.CARRY_FORWARD"
    assert finding.pages == [1, 2]
    assert "99,999.99" in finding.summary
    assert len(finding.evidence) == 1  # the same boundary: its own page's balance and the previous page's carry
    # Both carry lines it compares are boxed, each with its amount.
    assert [(place.page, place.text) for place in finding.places] == [
        (1, "Balance Carried Forward 10,446.00"), (2, "Balance Brought Forward 99,999.99"),
    ]


def test_a_carry_mismatch_where_the_rows_also_break_is_left_to_the_running_balance_check() -> None:
    from app.fraud_detection.numerical_validation.statements import _check_running_balance

    # Page 2 comes from a statement with another opening balance: each page agrees with its own carry lines,
    # but nothing reconciles across the boundary, rows included. BS.RUNNING_BALANCE reports that break, so
    # the carry check doesn't report it a second time.
    first, second = make_statement(pages=2), make_statement(pages=2, opening=20_000.00)
    doc = pymupdf.open(stream=first.pdf, filetype="pdf")
    doc.delete_page(1)
    doc.insert_pdf(pymupdf.open(stream=second.pdf, filetype="pdf"), from_page=1, to_page=1)
    rows = first.transactions[:4] + second.transactions[4:]
    result = _check(doc.tobytes(), Statement(b"", rows).fields())

    assert "PAGE.CARRY_FORWARD" not in _codes(result) and result.findings == []
    findings, _ = _check_running_balance(transactions=rows)
    assert findings  # reported there instead


def test_a_carry_mismatch_the_rows_hide_is_caught() -> None:
    # The tidied-up deletion: page 1 carries one balance, page 2 brings forward another, but page 2's rows were
    # retyped to continue from page 1, so the running balance reconciles. Only the carry lines show it.
    statement = make_statement(pages=2)
    pdf = edit_carry_line(statement.pdf, 1, "brought", "12,345.67")
    [finding] = _check(pdf, statement.fields()).findings
    assert finding.check_code == "PAGE.CARRY_FORWARD"
    assert any("page 2 brings forward 12,345.67" in text for text in [finding.summary, *finding.evidence])


# ---------------------------------------------------------------------------
# PAGE.HEADER_IDENTITY (AC3)
# ---------------------------------------------------------------------------


def test_a_page_swapped_in_from_another_account_is_one_finding_on_that_page() -> None:
    ours = make_statement(pages=3)
    theirs = make_statement(pages=3, account="555-111222-3", holder="OTHER CO PTE. LTD.", opening=50_000.00)
    result = _check(swap_page(ours.pdf, 1, theirs.pdf, 1), ours.fields())
    # One cause, one finding: the header conflict on page 2 leads, and the holder name and the broken carry
    # lines on either side of the page come with it.
    [finding] = result.findings
    assert finding.check_code == "PAGE.HEADER_IDENTITY"
    assert finding.summary.startswith('Page 2 prints Account No "555-111222-3"')
    codes = [e.split(":")[0] for e in finding.evidence]
    assert codes.count("PAGE.HEADER_IDENTITY") == 1 and codes.count("PAGE.CARRY_FORWARD") == 2
    # Every contributing check is listed once, the lead first, so per-check counts see the merged ones too.
    assert finding.check_codes == ["PAGE.HEADER_IDENTITY", "PAGE.CARRY_FORWARD"]
    # The account number is boxed where page 1 prints ours and page 2 prints theirs.
    boxed = [(place.page, place.text) for place in finding.places]
    assert (1, "Account No: 012-345678-9") in boxed and (2, "Account No: 555-111222-3") in boxed


def test_a_masked_account_number_on_a_later_page_agrees() -> None:
    statement = make_statement(account_on_page={2: "XXX-XXX678-9", 3: "XXX-XXX678-9"})
    assert "PAGE.HEADER_IDENTITY" not in _codes(_check(statement.pdf, statement.fields()))


# ---------------------------------------------------------------------------
# PAGE.SOURCE and PAGE.DUPLICATE (AC5, AC6)
# ---------------------------------------------------------------------------


def test_a_page_of_another_size_is_a_signal() -> None:
    statement = make_statement(page_sizes={2: (612, 792)})  # US Letter among A4
    [finding] = _check(statement.pdf, statement.fields()).findings
    assert (finding.check_code, finding.severity, finding.pages) == ("PAGE.SOURCE", "signal", [2])
    # The finding is about the page itself, so the whole page is boxed.
    assert [(place.page, place.bbox) for place in finding.places] == [(2, (0.0, 0.0, 612.0, 792.0))]


def test_a_page_in_fonts_no_other_page_uses_is_a_signal() -> None:
    # Page 1 in Helvetica, page 2 in Courier, page 3 swapped in from a document set in Times.
    statement = make_statement(font_on_page={2: "cour", 3: "tiro"})
    pdf = statement.pdf
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    doc[0].insert_text((50, 110), "Statement period 01-Jun-26 to 30-Jun-26", fontsize=9, fontname="cour")
    doc[1].insert_text((50, 110), "Statement period 01-Jun-26 to 30-Jun-26", fontsize=9, fontname="helv")
    findings = _check(doc.tobytes(), statement.fields()).findings
    assert [(f.check_code, f.severity, f.pages) for f in findings] == [("PAGE.SOURCE", "signal", [3])]
    assert "Times-Roman" in findings[0].summary


def test_a_page_sharing_one_font_with_the_others_is_genuine() -> None:
    statement = make_statement(font_on_page={3: "tiro"})
    doc = pymupdf.open(stream=statement.pdf, filetype="pdf")
    for line in range(8):  # a Helvetica footer that is a fair share of page 3
        doc[2].insert_text((50, 600 + line * 12), "Deposit Insurance Scheme protects eligible deposits", fontsize=8)
    assert _check(doc.tobytes(), statement.fields()).findings == []


def test_fonts_are_not_compared_on_a_two_page_statement() -> None:
    statement = make_statement(pages=2, font_on_page={2: "tiro"})
    assert _check(statement.pdf, statement.fields()).findings == []


def test_a_scanned_page_among_digital_pages_is_a_signal() -> None:
    statement = make_statement()
    doc = pymupdf.open(stream=statement.pdf, filetype="pdf")
    picture = pymupdf.open(stream=statement.pdf, filetype="pdf")[1].get_pixmap(dpi=60)
    doc.delete_page(1)
    scan = doc.new_page(pno=1, width=A4[0], height=A4[1])
    scan.insert_image(scan.rect, pixmap=picture)
    findings = _check(doc.tobytes(), statement.fields()).findings
    assert ("PAGE.SOURCE", (2,)) in {(f.check_code, tuple(f.pages)) for f in findings}
    # A scan has no text positions, yet every finding still boxes every page it names.
    assert all({place.page for place in f.places} == set(f.pages) for f in findings)


def test_a_duplicated_page_is_one_finding() -> None:
    statement = make_statement()
    [finding] = _check(duplicate_page(statement.pdf, 1), statement.fields()).findings
    assert finding.pages == [2, 3]
    assert "PAGE.DUPLICATE" in finding.check_codes
    assert finding.check_codes[0] == finding.check_code
    # Every page a finding names is boxed somewhere on it.
    assert {place.page for place in finding.places} == {2, 3}


# ---------------------------------------------------------------------------
# Detection integration (AC8) and performance (AC9)
# ---------------------------------------------------------------------------


def test_the_tool_reports_findings_for_the_content_specialist() -> None:
    from app.case_pipeline.categories import TOOL_CATEGORIES
    from app.case_pipeline.models import FindingCategory
    from app.langgraph.detection import tools
    from app.langgraph.detection.specialists import SPECIALISTS

    statement = make_statement()
    pdf = delete_page(statement.pdf, 1)
    document = type("Doc", (), {"filename": "statement.pdf"})()
    with (
        patch.object(tools, "_document", AsyncMock(return_value=document)),
        patch.object(tools, "_file_bytes", AsyncMock(return_value=pdf)),
        patch.object(tools, "_extracted_fields", AsyncMock(return_value=[])),
    ):
        output = asyncio.run(tools.page_consistency_tool.ainvoke({"document_id": "doc-1"}))

    [finding] = output["findings"]
    assert finding["id"] == "page_consistency_tool-1"
    assert finding["check_code"] == "PAGE.NUMBERING" and finding["pages"] == [1, 2]
    assert finding["bbox_units"] == "pt"
    boxed = [(loc["page"], loc["text"]) for loc in finding["locations"]]
    assert (1, "Page 1 of 3") in boxed and (2, "Page 3 of 3") in boxed
    assert "page_consistency_tool" in {t.name for t in SPECIALISTS["content_validation"].tools}
    assert TOOL_CATEGORIES["page_consistency_tool"] == FindingCategory.TAMPERING_FORENSIC


def test_a_fifty_page_statement_is_checked_in_under_two_seconds() -> None:
    statement = make_statement(pages=50, rows_per_page=20)
    fields = statement.fields()
    start = time.perf_counter()
    result = _check(statement.pdf, fields)
    assert time.perf_counter() - start < 2.0
    assert result.findings == []
