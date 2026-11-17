"""Cross-page consistency: the pages of one document belong together, and none are missing, swapped in,
edited on their own, or copied.

Five deterministic checks, no model calls:
* PAGE.NUMBERING        "Page x of y" runs 1..y with no gaps, repeats or reordering (numbering.py)
* PAGE.CARRY_FORWARD    balances carried between pages agree with each other and the page (carry_forward.py)
* PAGE.HEADER_IDENTITY  account number, names, UEN... are the same on every page that prints them (identity.py)
* PAGE.SOURCE           a page of a different size or orientation, a scan among digital pages, or a page in
                        fonts no other page uses (source.py)
* PAGE.DUPLICATE        two pages with the same content (duplicates.py)

Each check reports evidence about a page or a boundary between pages. Evidence about the same page or
boundary is one cause and becomes one finding, so a deleted page that shows up in the numbering and the
carry lines is reported once. These checks stay out of what others already cover: transaction arithmetic
and opening/closing balances (BS.*), periods (BS.PERIOD_BOUNDS), odd runs of text in another font, header
positions and image resolution (the layout and image checks).
"""

from __future__ import annotations

from typing import Any

from ...cpu_pool import run_cpu
from ..font_consistency import ExtractedField
from ..numerical_validation.common import fields_by_name
from .carry_forward import check_carry_forward
from .duplicates import check_duplicates
from .identity import check_header_identity
from .models import SEVERITY_RANK, CheckOutcome, Evidence, PageConsistencyResult, PageFinding
from .numbering import check_numbering
from .page_view import PageView, place_rows, read_pages, whole_page
from .source import check_source

NOTHING_TO_CHECK_SINGLE_PAGE = "Nothing to check: a single-page document"
NOTHING_TO_CHECK_NOT_PDF = "Nothing to check: an image upload is a single page"


def merge(evidence: list[Evidence], pages: list[PageView]) -> list[PageFinding]:
    """One finding per page or boundary: the strongest evidence leads, the rest is listed with it. Evidence
    about a page also takes in the boundaries on either side of it: a page swapped in mid-statement breaks
    the carry lines before and after it, and that is the same cause. Every page a finding names is boxed:
    where the lines it rests on are, else the whole page."""
    by_number = {page.number: page for page in pages}
    groups: dict[tuple, list[Evidence]] = {}
    for item in evidence:
        groups.setdefault(item.key, []).append(item)
    for key in [k for k in groups if k[0] == "page"]:
        for boundary in (("after", key[1] - 1), ("after", key[1])):
            if boundary in groups:
                groups[key].extend(groups.pop(boundary))
    findings = []
    for items in groups.values():
        items.sort(key=lambda e: SEVERITY_RANK[e.severity])
        lead = items[0]
        numbers = sorted({page for item in items for page in item.pages})
        places = {place for item in items for place in item.places}
        placed = {place.page for place in places}
        places |= {whole_page(by_number[n]) for n in numbers if n not in placed and n in by_number}
        findings.append(PageFinding(
            check_code=lead.check_code, severity=lead.severity,
            check_codes=list(dict.fromkeys(item.check_code for item in items)),
            pages=numbers,
            summary=lead.summary, stated=lead.stated, expected=lead.expected,
            evidence=[f"{item.check_code}: {item.summary}" for item in items[1:]],
            places=sorted(places, key=lambda p: (p.page, p.bbox[1], p.bbox[0])),
        ))
    findings.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.pages))
    return findings


def run_checks(pages: list[PageView], fields: dict[str, Any]) -> tuple[list[PageFinding], list]:
    transactions = fields.get("transactions")
    placed = place_rows(pages, transactions)
    outcomes: list[CheckOutcome] = [
        check_numbering(pages),
        check_carry_forward(pages, transactions, placed),
        check_header_identity(pages, fields),
        check_source(pages),
        check_duplicates(pages, placed),
    ]
    evidence = [item for outcome in outcomes for item in outcome.evidence]
    skipped = [item for outcome in outcomes for item in outcome.skipped]
    return merge(evidence, pages), skipped


def _check(document_id: str, file_bytes: bytes, filename: str, fields: dict[str, Any]) -> PageConsistencyResult:
    def result(pages_checked: int = 0, findings=None, skipped=None, unable: str | None = None) -> PageConsistencyResult:
        return PageConsistencyResult(
            document_id=document_id, pages_checked=pages_checked, findings=findings or [], skipped_checks=skipped or [],
            unable_to_assess=unable is not None, unable_to_assess_reason=unable,
        )

    if not filename.lower().endswith(".pdf"):
        return result(unable=NOTHING_TO_CHECK_NOT_PDF)
    try:
        pages = read_pages(file_bytes, filename)
    except Exception as exc:  # a file PyMuPDF can't open
        return result(unable=f"Could not read the document's pages: {type(exc).__name__}: {exc}")
    if len(pages) < 2:
        return result(len(pages), unable=NOTHING_TO_CHECK_SINGLE_PAGE)
    if not any(page.lines for page in pages):
        return result(len(pages), unable="No text could be read from any page")
    findings, skipped = run_checks(pages, fields)
    return result(len(pages), findings, skipped)


async def check_page_consistency(
    document_id: str, file_bytes: bytes, filename: str, extracted_fields: list[ExtractedField] | None = None,
) -> PageConsistencyResult:
    """Cross-page consistency of one document (see the module docstring)."""
    return await run_cpu(_check, document_id, file_bytes, filename, fields_by_name(extracted_fields))
