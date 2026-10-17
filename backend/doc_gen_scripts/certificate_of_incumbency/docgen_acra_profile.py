"""
docgen_acra_profile.py — generate ACRA-style Business Profile PDFs to serve as the
Certificate of Incumbency artefact for each case.

Layout follows the BizFile+ "Business Profile" product (the document Singapore banks
actually accept as proof of incumbency), adapted from the VCC sample to a private
company limited by shares: entity information, business activity, capital, officers
and shareholders, abbreviations/notes, and the registrar sign-off page.

The mastersheet is the source of truth for company name, UEN, registered address,
application date, the individuals and the share structure — via
case_facts.company_facts(), shared with every other generator so they all agree on
capital, ownership and incorporation date. Everything else is seeded from
SHA-256(case_id) so runs are reproducible.

To generate:

cd backend/doc_gen_scripts/certificate_of_incumbency
python docgen_acra_profile.py ../v9_document_mastersheet.xlsx --outdir out/
python docgen_acra_profile.py ../v9_document_mastersheet.xlsx --outdir out/ --cases C001
python docgen_acra_profile.py ../v9_document_mastersheet.xlsx --outdir out/ --no-synthetic-tag

Output filenames come from the "Cert of Incumbency" tab (C001_CertIncumbency.pdf).
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "certificate_of_incorporation"))

import openpyxl
from case_facts import company_facts, seeded_rng
# Same seed key + word pools as coi_generator.py's own former-name synthesis, so a
# case's Certificate of Incorporation and Certificate of Incumbency agree on
# whether the company has a former name on record, and what it is.
from coi_generator import _synthesize_former_name
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# --------------------------------------------------------------------------
# Layout constants
# --------------------------------------------------------------------------

PAGE_W, PAGE_H = A4
MARGIN_L = 20 * mm
MARGIN_R = 20 * mm
HEADER_H = 48 * mm          # space reserved at the top of every page
FOOTER_H = 24 * mm
ORANGE = colors.HexColor("#F5A200")
GREY_FILL = colors.HexColor("#E9E9E9")
GRID = colors.HexColor("#AAAAAA")

# Marker written into PDF metadata so synthetic corpus documents stay traceable.
SYNTHETIC_TAG = "SYNTHETIC-TEST-DATA"


# --------------------------------------------------------------------------
# Deterministic seeding
# --------------------------------------------------------------------------

SSIC_ACTIVITIES = [
    ("46900", "WHOLESALE TRADE OF A VARIETY OF GOODS WITHOUT A DOMINANT PRODUCT"),
    ("41009", "BUILDING CONSTRUCTION (GENERAL CONTRACTORS)"),
    ("52299", "OTHER SUPPORTING SERVICES TO TRANSPORTATION N.E.C."),
    ("13999", "MANUFACTURE OF OTHER TEXTILES N.E.C."),
    ("47199", "RETAIL SALE IN NON-SPECIALISED STORES N.E.C."),
    ("62019", "DEVELOPMENT OF OTHER SOFTWARE AND PROGRAMMING ACTIVITIES N.E.C."),
    ("70201", "MANAGEMENT CONSULTANCY SERVICES (GENERAL)"),
]

AUDIT_FIRMS = [
    ("Tan & Partners LLP", "T09LL0512C"),
    ("Chartwell Assurance PAC", "T14PF0087J"),
    ("Meridian Audit LLP", "T11LL1934B"),
    ("Sim Loo & Associates PAC", "T07PF0451D"),
]

SECRETARY_NAMES = [
    "CHAN SIEW PENG",
    "RAJESWARI D/O MUTHUSAMY",
    "GOH MEI LING",
    "NURUL AISYAH BINTE RAHMAN",
    "WONG KAH CHUN",
    "TEO LI XUAN",
]

NATIONALITY_MAP = {
    "singaporean": "SINGAPORE CITIZEN",
    "singapore citizen": "SINGAPORE CITIZEN",
    "singapore pr": "SINGAPORE PR",
    "malaysian": "MALAYSIAN",
}

# --------------------------------------------------------------------------
# Mastersheet reading
# --------------------------------------------------------------------------

def _rows(ws) -> list[dict[str, Any]]:
    header = [c.value for c in ws[1]]
    return [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True) if any(r)]


def _d(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


def load_cases(xlsx: str | Path, cases: list[str] | None = None) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(str(xlsx), data_only=True)
    registry = {str(r["Case ID"]): r for r in _rows(wb["Case Registry"])}
    doc_rows = {str(r["Case ID"]): r for r in _rows(wb["Cert of Incumbency"])}

    people: dict[str, list[dict[str, Any]]] = {}
    for ind in _rows(wb["Individuals"]):
        people.setdefault(str(ind["Case ID"]), []).append(ind)

    out = []
    for case_id, case in registry.items():
        if cases and case_id not in cases:
            continue
        if not case.get("Company Name"):
            continue  # placeholder row, not yet filled in
        out.append(
            {
                "case_id": case_id,
                "case": case,
                "doc_row": doc_rows.get(case_id, {}),
                "people": sorted(people.get(case_id, []), key=lambda r: r.get("Person Seq") or 0),
            }
        )
    return out


def resolve_defect(record: dict[str, Any]) -> str | None:
    """Per-document flag wins; fall back to the case-level fraud marker."""
    doc = record["doc_row"]
    if str(doc.get("Fraud Injected? (Y/N)", "")).strip().upper() == "Y":
        dimension = doc.get("Detection Dimension")
    elif str(record["case"].get("Fraud Case? (Y/N)", "")).strip().upper() == "Y":
        dimension = record["case"].get("Case Label", "")
    else:
        return None

    text = str(dimension or "").lower()
    for key, token in [
        ("font", "font_size"),
        ("size", "font_size"),
        ("numerical", "numerical"),
        ("metadata", "metadata"),
        ("cross-document", "cross_document"),
        ("cross document", "cross_document"),
        ("spelling", "spelling"),
        ("misalign", "misalignment"),
        ("ai-generated", "ai_generated"),
        ("ai generated", "ai_generated"),
    ]:
        if key in text:
            return token
    return None


# --------------------------------------------------------------------------
# Content assembly
# --------------------------------------------------------------------------

def build_content(record: dict[str, Any]) -> dict[str, Any]:
    case_id = record["case_id"]
    facts = company_facts(record["case"], record["people"])
    rng = seeded_rng(case_id + "|acra")

    def nationality(raw: Any) -> str:
        return NATIONALITY_MAP.get(str(raw).strip().lower(), str(raw).upper())

    holders = [
        {
            "name": str(h["name"]).upper(),
            "id": h["id"],
            "nationality": nationality(h["nationality"]),
            "address": str(h["address"]).upper(),
            "role": h["role"],
            "shares": h["shares"],
            "pct": h["pct"],
            "appointed": h["appointed"],
        }
        for h in facts["holders"]
    ]

    officers = [
        {
            "name": h["name"],
            "address": h["address"],
            "id": h["id"],
            "position": "Director",
            "nationality": h["nationality"],
            "appointed": h["appointed"],
        }
        for h in holders
        if "director" in str(h["role"]).lower()
    ]
    officers.append(
        {
            "name": str(facts["secretary"]).upper(),
            "address": str(facts["firm_address"]).upper(),
            "id": facts["secretary_id"],
            "position": "Secretary",
            "nationality": "SINGAPORE CITIZEN",
            "appointed": facts["secretary_appointed"],
        }
    )

    profile_date = facts["application_date"] - timedelta(days=rng.randint(2, 14))
    code, activity = SSIC_ACTIVITIES[rng.randrange(len(SSIC_ACTIVITIES))]
    audit_name, audit_uen = AUDIT_FIRMS[rng.randrange(len(AUDIT_FIRMS))]

    fye_month, fye_day = rng.choice([(12, 31), (3, 31), (6, 30), (9, 30)])
    agm_after_fye, ar_after_agm = rng.randint(90, 150), rng.randint(7, 45)
    # The last AGM and annual return on record are ones already held by the profile date: step back a
    # financial year until they are.
    fye_date = date(profile_date.year - 1, fye_month, fye_day)
    while fye_date + timedelta(days=agm_after_fye + ar_after_agm) >= profile_date:
        fye_date = date(fye_date.year - 1, fye_month, fye_day)
    last_agm = fye_date + timedelta(days=agm_after_fye)
    last_ar = last_agm + timedelta(days=ar_after_agm)

    # Same seed key, draw order and 0.4 threshold as coi_generator.py's
    # generate_certificate_of_incorporation(), so this case's COI and Business
    # Profile independently land on the same "has a former name?" decision (and the
    # same name) without either generator needing to pass data to the other. The
    # effective-date draws are consumed (not just the keep/clear roll) even though
    # this document doesn't show that date, to keep the two RNG streams in step.
    former_rng = seeded_rng(f"{facts['uen']}|former-names")
    if former_rng.random() >= 0.4:
        former_name = ""
    else:
        former_rng.randint(1, 6)
        former_rng.randint(1, 12)
        former_rng.randint(1, 28)
        former_name = _synthesize_former_name(former_rng, str(facts["company_name"]))

    return {
        "case_id": case_id,
        "company_name": str(facts["company_name"]).upper(),
        "former_name": former_name.upper() if former_name else "",
        "uen": facts["uen"],
        "registered_address": str(facts["registered_address"]).upper(),
        "incorporation_date": facts["incorporation_date"],
        "profile_date": profile_date,
        "company_type": str(facts["company_type"]).upper(),
        "status_date": facts["incorporation_date"],
        "activity_code": code,
        "activity": activity,
        "fye": _d(fye_date),
        "last_agm": last_agm,
        "last_ar": last_ar,
        "share_capital": facts["share_capital"],
        "share_type": str(facts["share_class"]).upper(),
        "audit_firm": audit_name,
        "audit_uen": audit_uen,
        "officers": officers,
        "shareholders": holders,
        "authentication_no": f"A{rng.randint(10000000, 99999999)}Z",
        "receipt_no": f"ACRA{rng.randint(10**11, 10**12 - 1)}",
    }


# --------------------------------------------------------------------------
# Defect injection
# --------------------------------------------------------------------------

SPELLING_SWAPS = [
    ("ACCOUNTING AND CORPORATE REGULATORY AUTHORITY", "ACCOUNTING AND CORPORATE REGULATARY AUTHORITY"),
    ("Shareholders", "Shareholdes"),
    ("Registered Office Address", "Registered Office Adress"),
    ("PRIVATE COMPANY LIMITED BY SHARES", "PRIVATE COMPANY LIMITED BY SHAREs"),
]


def apply_defect(content: dict[str, Any], defect: str | None) -> dict[str, Any]:
    content["defect"] = defect
    content["style_overrides"] = {}
    content["defect_description"] = ""
    if not defect:
        return content

    rng = seeded_rng(content["case_id"] + defect)

    if defect == "font_size":
        content["style_overrides"]["odd_row"] = rng.randrange(len(content["shareholders"]))
        content["defect_description"] = (
            f"Shareholder row {content['style_overrides']['odd_row'] + 1} set in Times-Roman "
            "9.5pt against Helvetica 8pt elsewhere."
        )

    elif defect == "numerical":
        target = content["shareholders"][0]
        delta = rng.choice([50_000, 75_000, 120_000])
        target["shares"] += delta
        content["defect_description"] = (
            f"Shareholdings exceed the stated issued capital by {delta:,} shares."
        )

    elif defect == "metadata":
        content["metadata_override"] = {
            "/Producer": "Microsoft: Print To PDF",
            "/Creator": "Adobe Photoshop 25.9 (Windows)",
            "/Author": "user",
            "/CreationDate": "D:20260211170422+08'00'",
            "/ModDate": "D:20260214093100+08'00'",
        }
        content["defect_description"] = (
            "Creator/Producer inconsistent with a BizFile+ export; ModDate later than the "
            "profile date."
        )

    elif defect == "cross_document":
        original = content["registered_address"]
        altered = re.sub(r"#\d{2}-\d{2,3}", "#11-27", original)
        content["registered_address"] = altered if altered != original else original + " "
        content["defect_description"] = (
            f"Registered office address differs from Case Registry ({original})."
        )

    elif defect == "spelling":
        swap = SPELLING_SWAPS[rng.randrange(len(SPELLING_SWAPS))]
        content["style_overrides"]["spelling_swap"] = swap
        content["defect_description"] = f"Substituted '{swap[0]}' with '{swap[1]}'."

    elif defect == "misalignment":
        content["style_overrides"]["indent_shift_mm"] = rng.choice([5, 8, 11])
        content["defect_description"] = (
            f"Officers table indented {content['style_overrides']['indent_shift_mm']}mm off the "
            "document's left margin."
        )

    elif defect == "ai_generated":
        content["defect_description"] = (
            "NOT INJECTED HERE — the AI-generated variant must be produced outside this "
            "generator. This run emits the clean baseline."
        )

    return content


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    mk = lambda name, **kw: ParagraphStyle(name, parent=base, **kw)
    return {
        "section": mk("sec", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
                      spaceBefore=10, spaceAfter=5),
        "label": mk("label", fontName="Helvetica", fontSize=8.5, leading=11),
        "value": mk("value", fontName="Helvetica", fontSize=8.5, leading=11),
        "cell": mk("cell", fontName="Helvetica", fontSize=8, leading=10),
        "cell_odd": mk("cell_odd", fontName="Times-Roman", fontSize=9.5, leading=11),
        "cell_head": mk("cellhead", fontName="Helvetica-Bold", fontSize=8, leading=10),
        "note": mk("note", fontName="Helvetica", fontSize=8, leading=11, spaceAfter=5),
        "sign": mk("sign", fontName="Helvetica", fontSize=9, leading=13),
    }


class _NumberedCanvas(pdfcanvas.Canvas):
    """Two-pass canvas so the header can print 'Page N of M'."""

    def __init__(self, *args, **kwargs):
        self._draw_page = kwargs.pop("draw_page")
        super().__init__(*args, **kwargs)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._draw_page(self, self.getPageNumber(), total)
            super().showPage()
        super().save()


def _page_furniture(c, page_no: int, total: int, content: dict[str, Any]) -> None:
    swap = content.get("style_overrides", {}).get("spelling_swap")
    sw = (lambda t: t.replace(*swap)) if swap else (lambda t: t)

    c.saveState()
    right = PAGE_W - MARGIN_R
    top = PAGE_H - 12 * mm

    # Authority wordmark
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(PAGE_W / 2 - 6 * mm, top,
                        sw("ACCOUNTING AND CORPORATE REGULATORY AUTHORITY"))
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(PAGE_W / 2 + 24 * mm, top - 10, "(ACRA)")

    # bizfile+ wordmark
    c.setFillColor(colors.HexColor("#6B6B4E"))
    c.setFont("Helvetica-Oblique", 15)
    c.drawRightString(right - 4, top - 4, "bizfile")
    c.setFillColor(ORANGE)
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(right, top + 7, "+")
    c.setFillColor(colors.black)

    # Orange "INFORMATION RESOURCES" badge
    bw, bh = 52 * mm, 6.5 * mm
    badge_top = top - 14
    c.setFillColor(ORANGE)
    c.rect(MARGIN_L, badge_top - bh, bw, bh, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(MARGIN_L + bw / 2, badge_top - bh + 5, "INFORMATION RESOURCES")
    c.setFillColor(colors.black)

    # Disclaimer
    c.setFont("Helvetica-Bold", 7.5)
    y = badge_top - bh - 14
    c.drawString(MARGIN_L, y,
                 "WHILST EVERY ENDEAVOR IS MADE TO ENSURE THAT INFORMATION PROVIDED IS "
                 "UPDATED AND CORRECT. THE AUTHORITY")
    c.drawString(MARGIN_L, y - 10,
                 "DISCLAIMS ANY LIABILITY FOR ANY DAMAGE OR LOSS THAT MAY BE CAUSED AS A "
                 "RESULT OF ANY ERROR OR OMISSION.")

    # Title row; shrink the title if the company name would collide with the date.
    title_y = y - 26
    date_text = _d(content["profile_date"])
    c.setFont("Helvetica", 8)
    dw = c.stringWidth(date_text, "Helvetica", 8)
    c.setFont("Helvetica-Bold", 8)
    lw = c.stringWidth("Date: ", "Helvetica-Bold", 8)
    c.drawString(right - dw - lw, title_y, "Date: ")
    c.setFont("Helvetica", 8)
    c.drawString(right - dw, title_y, date_text)

    title = f"Business Profile (Company) of {content['company_name']} ({content['uen']})"
    avail = (right - dw - lw - 6) - MARGIN_L
    size = 10.5
    while size > 7 and c.stringWidth(title, "Helvetica-Bold", size) > avail:
        size -= 0.25
    c.setFont("Helvetica-Bold", size)
    c.drawString(MARGIN_L, title_y, title)

    # Footer
    c.setFont("Helvetica", 8)
    c.drawRightString(right, 18 * mm, f"Authentication No : {content['authentication_no']}")
    c.drawRightString(right, 12 * mm, f"Page {page_no} of {total}")
    c.restoreState()


def _kv_table(pairs: list[tuple[str, str]], st: dict[str, ParagraphStyle], width: float) -> Table:
    data = [
        [Paragraph(k, st["label"]), ":", Paragraph(v, st["value"])] for k, v in pairs
    ]
    t = Table(data, colWidths=[0.34 * width, 0.03 * width, 0.63 * width], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, -1), 6),
                ("LEFTPADDING", (1, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return t


def _grid_table(data: list[list[Any]], widths: list[float], indent: float = 0) -> Table:
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), GREY_FILL),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    t.setStyle(TableStyle(style))
    if indent:
        t._argW = t._argW  # no-op; indent applied by caller via Table wrapper
    return t


def render(content: dict[str, Any], out_pdf: str | Path, synthetic_tag: bool = True) -> Path:
    st = _styles()
    swap = content.get("style_overrides", {}).get("spelling_swap")
    sw = (lambda t: t.replace(*swap)) if swap else (lambda t: t)

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(out_pdf),
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=HEADER_H,
        bottomMargin=FOOTER_H,
        title=f"Business Profile (Company) of {content['company_name']}",
        author="Accounting and Corporate Regulatory Authority",
        subject=f"Business Profile — {content['case_id']}",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame])])
    w = doc.width
    story: list[Any] = []

    # --- Entity Information -------------------------------------------------
    story.append(Paragraph("Entity Information", st["section"]))
    story.append(
        _kv_table(
            [
                ("UEN", content["uen"]),
                ("Name", content["company_name"]),
                ("Former Name if any", content.get("former_name") or "-"),
                ("Incorporation Date", _d(content["incorporation_date"])),
                ("Company Type", sw(content["company_type"])),
                ("Status", "LIVE COMPANY"),
                ("Status Date", _d(content["status_date"])),
            ],
            st,
            w,
        )
    )

    # --- Business Activity --------------------------------------------------
    story.append(Paragraph("Business Activity", st["section"]))
    story.append(
        _kv_table(
            [
                ("Principal Activity", f"{content['activity']} ({content['activity_code']})"),
                ("Description", ""),
                (sw("Registered Office Address"), content["registered_address"]),
                ("Date of Address", _d(content["incorporation_date"])),
                ("Date of Last AGM", _d(content["last_agm"])),
                ("Date of Last AR", _d(content["last_ar"])),
                ("FYE As At Date of Last AR", content["fye"]),
            ],
            st,
            w,
        )
    )

    # --- Capital ------------------------------------------------------------
    story.append(Paragraph("Capital", st["section"]))
    cap_rows = [
        [Paragraph(h, st["cell_head"]) for h in
         ("Issued Share Capital", "Number of Shares", "Currency", "Share Type")],
        [
            Paragraph(f"{content['share_capital']:,.2f}", st["cell"]),
            Paragraph(f"{content['share_capital']:,}", st["cell"]),
            Paragraph("SINGAPORE, DOLLARS", st["cell"]),
            Paragraph(content["share_type"], st["cell"]),
        ],
    ]
    story.append(_grid_table(cap_rows, [0.3 * w, 0.25 * w, 0.27 * w, 0.18 * w]))

    # --- Audit Firm ---------------------------------------------------------
    story.append(Paragraph("Audit Firms", st["section"]))
    story.append(
        _grid_table(
            [
                [Paragraph("Name", st["cell_head"]), Paragraph("UEN of Audit Firm", st["cell_head"])],
                [Paragraph(content["audit_firm"].upper(), st["cell"]),
                 Paragraph(content["audit_uen"], st["cell"])],
            ],
            [0.6 * w, 0.4 * w],
        )
    )

    # --- Charges (typically empty) ------------------------------------------
    story.append(Paragraph("Charges", st["section"]))
    story.append(
        _grid_table(
            [
                [Paragraph(h, st["cell_head"]) for h in
                 ("Chargee(s)", "Charge No.", "Date Registered", "Amount Secured", "Currency")],
                ["", "", "", "", ""],
            ],
            [0.28 * w, 0.16 * w, 0.2 * w, 0.22 * w, 0.14 * w],
        )
    )

    # --- Officers -----------------------------------------------------------
    story.append(Paragraph("Officers", st["section"]))
    off_rows = [
        [
            Paragraph("Name<br/><br/>Address", st["cell_head"]),
            Paragraph("Identification No.", st["cell_head"]),
            Paragraph("Position Held", st["cell_head"]),
            Paragraph("Nationality/<br/>Citizenship", st["cell_head"]),
            Paragraph("Date of<br/>Appointment", st["cell_head"]),
        ]
    ]
    for o in content["officers"]:
        off_rows.append(
            [
                Paragraph(f"{o['name']}<br/><br/>{o['address']}", st["cell"]),
                Paragraph(str(o["id"]), st["cell"]),
                Paragraph(o["position"], st["cell"]),
                Paragraph(o["nationality"], st["cell"]),
                Paragraph(_d(o["appointed"]), st["cell"]),
            ]
        )
    indent = content.get("style_overrides", {}).get("indent_shift_mm", 0) * mm
    officers_tbl = _grid_table(off_rows, [0.34 * w, 0.17 * w, 0.15 * w, 0.18 * w, 0.16 * w])
    if indent:
        wrapper = Table([[officers_tbl]], colWidths=[w], hAlign="LEFT")
        wrapper.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), indent),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(wrapper)
    else:
        story.append(officers_tbl)

    # --- Shareholders -------------------------------------------------------
    story.append(Paragraph(sw("Shareholders"), st["section"]))
    odd = content.get("style_overrides", {}).get("odd_row")
    sh_rows = [
        [
            Paragraph("Name<br/><br/>Address", st["cell_head"]),
            Paragraph("Identification No.", st["cell_head"]),
            Paragraph("Nationality/<br/>Place of Incorporation", st["cell_head"]),
            Paragraph("No. of Shares", st["cell_head"]),
            Paragraph("Share Type", st["cell_head"]),
        ]
    ]
    for i, h in enumerate(content["shareholders"]):
        cs = st["cell_odd"] if odd == i else st["cell"]
        sh_rows.append(
            [
                Paragraph(f"{h['name']}<br/><br/>{h['address']}", cs),
                Paragraph(str(h["id"]), cs),
                Paragraph(h["nationality"], cs),
                Paragraph(f"{h['shares']:,}", cs),
                Paragraph(content["share_type"], cs),
            ]
        )
    story.append(_grid_table(sh_rows, [0.34 * w, 0.17 * w, 0.19 * w, 0.15 * w, 0.15 * w]))

    # --- Notes page ---------------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Abbreviation", st["section"]))
    for line in ("AR – Annual Return", "AGM – Annual General Meeting", "FYE – Financial Year End"):
        story.append(Paragraph(line, st["note"]))
    story.append(Paragraph("Note", st["section"]))
    story.append(
        Paragraph(
            "1.&nbsp;&nbsp;&nbsp;The information contained in this product is collated from "
            "lodgments filed with ACRA and/or information collected by other government "
            "verified sources.",
            st["note"],
        )
    )
    story.append(
        Paragraph(
            "2.&nbsp;&nbsp;&nbsp;The list of officers for this entity is available for online "
            "authentication within 30 days from the date of purchase of this product.",
            st["note"],
        )
    )
    story.append(Spacer(1, 40))
    story.append(
        KeepTogether(
            [
                Paragraph("ASST. REGISTRAR OF COMPANIES &amp; BUSINESS NAMES", st["sign"]),
                Paragraph("SINGAPORE", st["sign"]),
                Spacer(1, 12),
                _kv_table(
                    [("RECEIPT NO.", content["receipt_no"]), ("DATE", _d(content["profile_date"]))],
                    st,
                    w * 0.6,
                ),
                Spacer(1, 12),
                Paragraph("This is computer generated. Hence no signature required.", st["sign"]),
            ]
        )
    )

    doc.build(
        story,
        canvasmaker=lambda *a, **kw: _NumberedCanvas(
            *a, draw_page=lambda c, n, t: _page_furniture(c, n, t, content), **kw
        ),
    )

    meta = dict(content.get("metadata_override") or {})
    if synthetic_tag:
        meta["/Keywords"] = SYNTHETIC_TAG
    if meta:
        _rewrite_metadata(out_pdf, meta)
    return out_pdf


def _rewrite_metadata(pdf_path: Path, meta: dict[str, str]) -> None:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter(clone_from=reader)
    writer.add_metadata(meta)
    with open(pdf_path, "wb") as fh:
        writer.write(fh)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def generate(
    xlsx: str | Path,
    outdir: str | Path,
    cases: list[str] | None = None,
    synthetic_tag: bool = True,
    inject_defects: bool = False,
) -> list[Path]:
    """Clean by default. Pass inject_defects=True to honour the mastersheet fraud markers."""
    written = []
    for record in load_cases(xlsx, cases):
        defect = resolve_defect(record) if inject_defects else None
        content = apply_defect(build_content(record), defect)
        filename = record["doc_row"].get("Filename") or f"{record['case_id']}_CertIncumbency.pdf"
        path = render(content, Path(outdir) / filename, synthetic_tag=synthetic_tag)
        written.append(path)
        print(
            f"{record['case_id']}  {filename}  [{content.get('defect') or 'clean'}]  "
            f"{content.get('defect_description') or 'clean'}"
        )
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--cases", nargs="*")
    ap.add_argument(
        "--inject-defects",
        action="store_true",
        help="honour the mastersheet fraud markers; omit this flag for clean documents only",
    )
    ap.add_argument(
        "--no-synthetic-tag",
        action="store_true",
        help="omit the SYNTHETIC-TEST-DATA marker from PDF /Keywords",
    )
    args = ap.parse_args()
    generate(
        args.xlsx,
        args.outdir,
        args.cases,
        synthetic_tag=not args.no_synthetic_tag,
        inject_defects=args.inject_defects,
    )


if __name__ == "__main__":
    main()
