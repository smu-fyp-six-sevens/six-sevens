"""
Regression tests (SCRUM-323) for cross-page validation — "Test Cases - Sprint 5.xlsx", Cross Doc Validation sheet,
Cross Page Validation (SCRUM-282): CD-022 – CD-037.

Each test builds the document its test case describes (the page labels, sizes, fonts and figures in its Test Data)
and runs it through the real detection graph, Sprint 3's harness: the Content Validation specialist calls the real
page_consistency_tool, which runs the real checks in fraud_detection/pages on the PDF's bytes. Only Claude's replies
are scripted: the specialist rates every finding the tool returned. The statements come from the check's own test
builder (tests/test_page_consistency.py) and its page-defect injectors (app/injection/page_defects.py), so they
are laid out as a bank prints one: page numbers, the account header on every page, and the balances carried
between pages.
"""
from __future__ import annotations

import pymupdf
import pytest

from app.injection.page_defects import duplicate_page, edit_carry_line
from app.langgraph.detection.graph import run_detection
from tests.support.statements import Statement, make_statement
from tests.support import detection_harness
from tests.support.detection_harness import CONTENT, Specialist, consulted, findings_from

# The Sprint 3 harness's fixtures: scripted Claude, the in-memory store, and the cleanup after each run.
ai = detection_harness.ai
store = detection_harness.store
_clear_detection_memory = detection_harness._clear_detection_memory

TOOL = "page_consistency_tool"
A4, A3 = (595, 842), (842, 1191)
LANDSCAPE_A4 = (842, 595)


def _fields(statement: Statement, **extra) -> dict:
    """What extraction returns for the statement: the header the pages print, and the transactions."""
    return {"account_number": "012-345678-9", "company_name": "ACME TRADING PTE. LTD.",
            "transactions": statement.transactions, **extra}


def _with_lines(pdf: bytes, lines: dict[int, str], *, at: tuple[float, float] = (470, 30)) -> bytes:
    """Prints one line on each given page (0-based), e.g. its "Page x of y" label."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for index, text in lines.items():
        doc[index].insert_text(at, text, fontsize=8, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def numbered(*labels: str) -> tuple[bytes, Statement]:
    """A statement with one page per label, each page printing its label where a bank prints its page number."""
    statement = make_statement(pages=len(labels), numbering=False)
    return _with_lines(statement.pdf, dict(enumerate(labels))), statement


async def check(ai, store, pdf: bytes, fields: dict, filename: str = "C090_BankStatement.pdf") -> dict:
    document_id = store.add(filename, "bank_statement", pdf, fields=fields)
    ai.specialists[CONTENT] = Specialist(tools=[TOOL], severity="low")
    return await run_detection(document_id)


def page_findings(report: dict) -> list[dict]:
    return findings_from(report, TOOL)


def summaries(report: dict) -> list[str]:
    return [f["summary"] for f in page_findings(report)]


def ran(report: dict) -> bool:
    return TOOL in consulted(report, CONTENT)["tools_called"]


# --- Scope -------------------------------------------------------------------------------------------


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-022")
async def test_a_single_page_document_has_nothing_to_check(ai, store):
    statement = make_statement(pages=1)

    report = await check(ai, store, statement.pdf, _fields(statement))

    assert ran(report)
    assert page_findings(report) == []
    [nothing] = [n for n in report["nothing_to_check"] if n["tool"] == TOOL]
    assert nothing["reason"] == "Nothing to check: a single-page document"
    assert not any(u["tool"] == TOOL for u in report["unable_to_assess"])


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-023")
async def test_a_scanned_document_with_no_text_is_unable_to_assess(ai, store):
    # Three scanned pages with nothing legible on them: no text layer, and nothing OCR can read either. (A scan
    # that OCR can read is checked like any other document.)
    doc = pymupdf.open()
    blank_scan = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 560), False)
    blank_scan.clear_with(235)
    for _ in range(3):
        page = doc.new_page(width=A4[0], height=A4[1])
        page.insert_image(page.rect, pixmap=blank_scan)

    report = await check(ai, store, doc.tobytes(), {"account_number": "012-345678-9"})

    assert page_findings(report) == []
    [unable] = [u for u in report["unable_to_assess"] if u["tool"] == TOOL]
    assert unable["reason"] == "No text could be read from any page"
    assert report["verdict"] != "clean"


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-024 / CD-030")
async def test_a_consistent_three_page_statement_is_checked_and_raises_nothing(ai, store):
    pdf, statement = numbered("Page 1 of 3", "Page 2 of 3", "Page 3 of 3")

    report = await check(ai, store, pdf, _fields(statement))

    assert ran(report)
    assert page_findings(report) == []
    assert not any(n["tool"] == TOOL for n in report["nothing_to_check"] + report["unable_to_assess"])


# --- Page numbering ------------------------------------------------------------------------------------


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-025")
async def test_a_missing_page_number_is_flagged(ai, store):
    pdf, statement = numbered("Page 1 of 3", "Page 3 of 3", "Page 3 of 3")

    report = await check(ai, store, pdf, _fields(statement))

    # The repeated "3 of 3" that took page 2's place is reported too.
    assert "Page numbered 1 is followed by 3 of 3: page 2 missing" in summaries(report)
    assert all(f["check_code"] == "PAGE.NUMBERING" for f in page_findings(report))


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-026")
async def test_a_repeated_page_number_is_flagged(ai, store):
    pdf, statement = numbered("Page 1 of 3", "Page 2 of 3", "Page 2 of 3")

    report = await check(ai, store, pdf, _fields(statement))

    [repeat] = [f for f in page_findings(report) if f["summary"] == "Pages 2 and 3 are both numbered 2 of 3"]
    assert repeat["pages"] == [2, 3]
    # With page 2 printed twice, nothing says 3 of 3: that is reported as the last page missing.
    assert "page 3 missing at the end" in " ".join(summaries(report))


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-027")
async def test_out_of_order_pages_are_flagged(ai, store):
    pdf, statement = numbered("Page 1 of 3", "Page 3 of 3", "Page 2 of 3")

    report = await check(ai, store, pdf, _fields(statement))

    [out_of_order] = [f for f in page_findings(report) if "comes after" in f["summary"]]
    assert out_of_order["summary"] == "Page 3 is numbered 2 but comes after page numbered 3"
    assert out_of_order["pages"] == [2, 3]


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-028")
async def test_different_page_totals_are_flagged(ai, store):
    pdf, statement = numbered("Page 1 of 3", "Page 2 of 4", "Page 3 of 3")

    report = await check(ai, store, pdf, _fields(statement))

    [totals] = [f for f in page_findings(report) if f["summary"].startswith("The pages disagree")]
    assert totals["summary"] == "The pages disagree on how many pages there are: of 3, of 4"
    assert totals["pages"] == [1, 2, 3]


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-029")
async def test_a_printed_total_that_differs_from_the_page_count_is_flagged(ai, store):
    pdf, statement = numbered("Page 1 of 4", "Page 2 of 4", "Page 3 of 4")

    [finding] = page_findings(await check(ai, store, pdf, _fields(statement)))

    assert finding["check_code"] == "PAGE.NUMBERING"
    assert finding["summary"] == "The document says 4 pages, but the last numbered page is 3: page 4 missing at the end"


# --- Identifiers and carried balances --------------------------------------------------------------------


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-031")
async def test_a_repeated_identifier_with_a_different_value_is_flagged(ai, store):
    pdf, statement = numbered("Page 1 of 3", "Page 2 of 3", "Page 3 of 3")
    pdf = _with_lines(pdf, {0: "UEN: 201234567A", 1: "UEN: 201234567A", 2: "UEN: 201234568A"}, at=(50, 110))

    [finding] = page_findings(await check(ai, store, pdf, _fields(statement, uen="201234567A")))

    assert finding["check_code"] == "PAGE.HEADER_IDENTITY"
    assert finding["pages"] == [1, 3]
    assert finding["summary"] == 'Page 3 prints UEN "201234568A", but page 1 prints "201234567A"'


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-032")
async def test_a_carried_and_brought_forward_balance_mismatch_is_flagged(ai, store):
    # Page 1 carries forward 5,000.00 (its four rows add 446.00 to the opening balance); page 2 brings 4,500.00.
    statement = make_statement(pages=3, opening=4_554.00)
    pdf = edit_carry_line(statement.pdf, 1, "brought", "4,500.00")

    [finding] = page_findings(await check(ai, store, pdf, _fields(statement)))

    assert finding["check_code"] == "PAGE.CARRY_FORWARD"
    assert finding["pages"] == [1, 2]
    text = " ".join([finding["summary"], *finding["evidence"]])
    assert "5,000.00" in text
    assert "4,500.00" in text


# --- Where pages came from ---------------------------------------------------------------------------------


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-033")
async def test_a_page_of_a_different_size_is_flagged(ai, store):
    statement = make_statement(pages=3, page_sizes={3: A3})

    [finding] = page_findings(await check(ai, store, statement.pdf, _fields(statement)))

    assert (finding["check_code"], finding["pages"]) == ("PAGE.SOURCE", [3])
    assert "Page 3 is 842x1191 pt; the other pages are 595x842 pt" in finding["summary"]


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-034")
async def test_a_page_of_a_different_orientation_is_flagged(ai, store):
    statement = make_statement(pages=3, page_sizes={3: LANDSCAPE_A4})

    [finding] = page_findings(await check(ai, store, statement.pdf, _fields(statement)))

    assert (finding["check_code"], finding["pages"]) == ("PAGE.SOURCE", [3])
    assert "Page 3 is 842x595 pt; the other pages are 595x842 pt" in finding["summary"]


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-035")
async def test_a_page_in_an_entirely_different_font_set_is_a_low_finding(ai, store):
    # Pages 1 and 2 in Helvetica (with a line in Courier on each), page 3 in Times only. Page 3 prints its
    # transactions, so it is a statement page, not a terms page at the back.
    statement = make_statement(pages=3, font_on_page={3: "tiro"})
    doc = pymupdf.open(stream=statement.pdf, filetype="pdf")
    for index in (0, 1):
        doc[index].insert_text((50, 110), "Statement period 01-Jun-26 to 30-Jun-26", fontsize=9, fontname="cour")

    [finding] = page_findings(await check(ai, store, doc.tobytes(), _fields(statement)))

    assert (finding["check_code"], finding["pages"]) == ("PAGE.SOURCE", [3])
    assert "Times-Roman" in finding["summary"]
    assert finding["severity"] == "low"


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-036")
async def test_a_terms_page_in_another_font_is_not_a_font_finding(ai, store):
    # Pages 1 and 2 are the statement; page 3 is the bank's terms and conditions, in Times, with no amounts.
    statement = make_statement(pages=2, numbering=False)
    doc = pymupdf.open(stream=statement.pdf, filetype="pdf")
    terms = doc.new_page(width=A4[0], height=A4[1])
    for line in range(12):
        terms.insert_text((50, 80 + line * 14), "Terms and conditions: please check this statement carefully and "
                          "tell the bank of any discrepancy within fourteen days.", fontsize=8, fontname="tiro")
    pdf = _with_lines(doc.tobytes(), {0: "Page 1 of 3", 1: "Page 2 of 3", 2: "Page 3 of 3"})

    report = await check(ai, store, pdf, _fields(statement))

    assert ran(report)
    assert not any("PAGE.SOURCE" in f["check_codes"] for f in page_findings(report))
    # The other checks still run on the statement pages.
    assert not any(n["tool"] == TOOL for n in report["nothing_to_check"] + report["unable_to_assess"])


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-282", "CD-037")
async def test_a_duplicated_page_is_flagged(ai, store):
    statement = make_statement(pages=3)

    [finding] = page_findings(await check(ai, store, duplicate_page(statement.pdf, 0), _fields(statement)))

    assert "PAGE.DUPLICATE" in finding["check_codes"]
    assert finding["pages"] == [1, 2]
