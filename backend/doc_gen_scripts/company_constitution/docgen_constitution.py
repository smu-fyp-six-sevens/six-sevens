"""
docgen_constitution.py — generate Company Constitution PDFs from the mastersheet.

Produces a Singapore private-company constitution in the shape of the Model
Constitution under the Companies Act 1967 (Twelfth Schedule): cover page, numbered
clauses across the usual headings, and an execution page listing the subscribers and
their shareholdings.

Documents are CLEAN by default. Pass --inject-defects to honour the per-document
"Fraud Injected? (Y/N)" / "Detection Dimension" columns on the Company Constitution tab.

To generate:

cd backend/doc_gen_scripts/company_constitution
python docgen_constitution.py ../v9_document_mastersheet.xlsx --outdir out/
python docgen_constitution.py ../v9_document_mastersheet.xlsx --outdir out/ --cases C001
python docgen_constitution.py ../v9_document_mastersheet.xlsx --outdir out/ --inject-defects


Company name, UEN, registered address, application date and the individuals come from
the mastersheet, reconciled through case_facts.company_facts() — shared with every
other generator so they all agree on capital, ownership and incorporation date.
Everything else is seeded from SHA-256(case_id) for reproducibility. Output filenames
come from the tab's Filename column (C001_Constitution.pdf).
"""

from __future__ import annotations

import argparse
import hashlib
import random
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from case_facts import company_facts
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
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

PAGE_W, PAGE_H = A4
MARGIN_L = 25 * mm
MARGIN_R = 25 * mm
SYNTHETIC_TAG = "SYNTHETIC-TEST-DATA"

CORP_SEC_FIRMS = [
    ("Rivergate Corporate Services Pte. Ltd.", "8 Cross Street, #24-03 Manulife Tower, Singapore 048424"),
    ("Ashworth Corporate Advisory Pte. Ltd.", "137 Cecil Street, #09-01 Cecil Building, Singapore 069537"),
    ("Merlion Company Secretaries Pte. Ltd.", "20 Collyer Quay, #17-04, Singapore 049319"),
    ("Kallang Corporate Management Pte. Ltd.", "3 Church Street, #12-06 Samsung Hub, Singapore 049483"),
]

WITNESS_NAMES = [
    "Chan Siew Peng",
    "Rajeswari d/o Muthusamy",
    "Goh Mei Ling",
    "Nurul Aisyah binte Rahman",
    "Wong Kah Chun",
    "Teo Li Xuan",
]

SHARE_SPLITS = [(60, 40), (70, 30), (75, 25), (51, 49), (80, 20), (65, 35)]
SHARE_CAPITALS = [100_000, 250_000, 500_000, 750_000, 1_000_000]


def seeded_rng(case_id: str) -> random.Random:
    return random.Random(int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16], 16))


# --------------------------------------------------------------------------
# Mastersheet
# --------------------------------------------------------------------------

def _rows(ws) -> list[dict[str, Any]]:
    header = [c.value for c in ws[1]]
    return [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True) if any(r)]


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            return None
    return None


def _d(value: date | None) -> str:
    return value.strftime("%d %B %Y") if value else ""


def load_cases(xlsx: str | Path, cases: list[str] | None = None) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(str(xlsx), data_only=True)
    registry = {str(r["Case ID"]): r for r in _rows(wb["Case Registry"])}
    doc_rows = {str(r["Case ID"]): r for r in _rows(wb["Company Constitution"])}

    people: dict[str, list[dict[str, Any]]] = {}
    for ind in _rows(wb["Individuals"]):
        people.setdefault(str(ind["Case ID"]), []).append(ind)

    out = []
    for case_id, case in registry.items():
        if cases and case_id not in cases:
            continue
        if not case.get("Company Name"):
            continue  # placeholder row
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
    """Per-document only. v7 assigns each case's dimension to a single document, so the
    old case-level fallback would wrongly stamp the same defect on every artefact."""
    doc = record["doc_row"]
    if str(doc.get("Fraud Injected? (Y/N)", "")).strip().upper() != "Y":
        return None

    text = str(doc.get("Detection Dimension") or "").lower()
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
# Content
# --------------------------------------------------------------------------

def build_content(record: dict[str, Any]) -> dict[str, Any]:
    facts = company_facts(record["case"], record["people"])
    rng = seeded_rng(record["case_id"] + "|constitution")

    subscribers = [
        {"name": h["name"], "id": h["id"], "address": h["address"], "shares": h["shares"],
         "pct": h["pct"]}
        for h in facts["holders"]
    ]

    return {
        "case_id": facts["case_id"],
        "company_name": facts["company_name"],
        "uen": facts["uen"],
        "registered_address": facts["registered_address"],
        "incorporation_date": facts["incorporation_date"],
        "adoption_date": facts["incorporation_date"],
        "share_capital": facts["share_capital"],
        "subscribers": subscribers,
        "firm": facts["firm"],
        "firm_address": facts["firm_address"],
        "witness": facts["secretary"],
        "quorum": rng.choice([2, 2, 2, 3]),
        "notice_days": rng.choice([14, 14, 21]),
        "max_members": 50,
    }


# --------------------------------------------------------------------------
# Clause text
# --------------------------------------------------------------------------

def clauses(c: dict[str, Any]) -> list[tuple[str, list[str]]]:
    name = c["company_name"]
    return [
        ("1. NAME", [
            f"The name of the Company is <b>{name}</b>.",
        ]),
        ("2. REGISTERED OFFICE", [
            f"The registered office of the Company shall be situated at {c['registered_address']}, "
            "or at such other place in the Republic of Singapore as the directors may from time "
            "to time determine.",
        ]),
        ("3. LIABILITY OF MEMBERS", [
            "The liability of the members is limited to the amount, if any, unpaid on the shares "
            "respectively held by them.",
        ]),
        ("4. PRIVATE COMPANY", [
            "The Company is a private company, and accordingly:",
            f"(a) the number of members of the Company (not including persons who are in the "
            f"employment of the Company) shall be limited to {c['max_members']};",
            "(b) the right to transfer the shares of the Company is restricted in the manner "
            "set out in clause 6; and",
            "(c) the Company shall not make any invitation to the public to subscribe for any "
            "shares or debentures of the Company, nor invite the public to deposit money with "
            "the Company.",
        ]),
        ("5. SHARE CAPITAL AND VARIATION OF RIGHTS", [
            f"5.1 The issued share capital of the Company at the date of adoption of this "
            f"Constitution is SGD {c['share_capital']:,.2f} divided into {c['share_capital']:,} "
            "ordinary shares.",
            "5.2 Subject to the Act and to this Constitution, the directors may allot, issue, "
            "grant options over or otherwise dispose of shares to such persons, on such terms "
            "and at such times as the directors may determine, provided that no shares shall be "
            "issued without the prior approval of the Company in general meeting.",
            "5.3 If at any time the share capital is divided into different classes of shares, "
            "the rights attached to any class may be varied only with the consent in writing of "
            "the holders of three-quarters of the issued shares of that class, or with the "
            "sanction of a special resolution passed at a separate general meeting of the holders "
            "of the shares of that class.",
        ]),
        ("6. TRANSFER OF SHARES", [
            "6.1 No share shall be transferred to a person who is not a member of the Company "
            "unless the shares have first been offered to the existing members in proportion to "
            "their existing holdings, at a price determined in accordance with clause 6.2.",
            "6.2 The transfer price shall be the price agreed between the transferor and the "
            "directors or, failing agreement, the fair value certified by the auditors of the "
            "Company acting as experts and not as arbitrators.",
            "6.3 The directors may, in their absolute discretion and without assigning any "
            "reason, decline to register the transfer of any share, whether or not it is a fully "
            "paid share.",
            "6.4 Every instrument of transfer shall be left at the registered office of the "
            "Company for registration, accompanied by the certificate of the shares to be "
            "transferred and such other evidence as the directors may reasonably require.",
        ]),
        ("7. TRANSMISSION OF SHARES", [
            "7.1 In the case of the death of a member, the survivors where the deceased was a "
            "joint holder, and the legal personal representatives of the deceased where he was a "
            "sole holder, shall be the only persons recognised by the Company as having any title "
            "to his interest in the shares.",
            "7.2 Any person becoming entitled to a share in consequence of the death or "
            "bankruptcy of a member may, upon such evidence being produced as the directors "
            "require, elect either to be registered as the holder of the share or to have some "
            "person nominated by him registered as the transferee.",
        ]),
        ("8. GENERAL MEETINGS", [
            f"8.1 An annual general meeting shall be held in accordance with the Act, unless the "
            "Company is exempt from holding such meeting by virtue of the Act or has dispensed "
            "with the holding of annual general meetings by resolution passed in accordance with "
            "the Act.",
            f"8.2 A general meeting shall be called by not less than {c['notice_days']} days' "
            "notice in writing, exclusive of the day on which the notice is served and of the day "
            "for which it is given, save that a meeting may be called by shorter notice with the "
            "agreement of the requisite majority of members prescribed by the Act.",
            "8.3 The accidental omission to give notice of a meeting to, or the non-receipt of "
            "notice by, any person entitled to receive notice shall not invalidate the "
            "proceedings at that meeting.",
        ]),
        ("9. PROCEEDINGS AT GENERAL MEETINGS", [
            f"9.1 No business shall be transacted at any general meeting unless a quorum is "
            f"present at the time when the meeting proceeds to business. {c['quorum']} members "
            "present in person or by proxy shall constitute a quorum.",
            "9.2 The chairman of the board of directors shall preside as chairman at every "
            "general meeting. If there is no such chairman, or if he is not present within "
            "fifteen minutes after the time appointed for the meeting, the members present shall "
            "elect one of their number to be chairman of the meeting.",
            "9.3 At any general meeting a resolution put to the vote of the meeting shall be "
            "decided on a show of hands unless a poll is demanded in accordance with the Act.",
            "9.4 Subject to any rights or restrictions attached to any class of shares, on a show "
            "of hands every member present in person shall have one vote, and on a poll every "
            "member shall have one vote for each share held by him.",
        ]),
        ("10. DIRECTORS", [
            "10.1 The number of directors shall not be less than one, and at least one director "
            "shall be ordinarily resident in Singapore as required by the Act.",
            "10.2 The Company may by ordinary resolution appoint any person to be a director, "
            "and may by ordinary resolution remove any director before the expiration of his "
            "period of office notwithstanding anything in this Constitution or in any agreement "
            "between the Company and that director.",
            "10.3 The office of a director shall become vacant if the director becomes bankrupt, "
            "becomes disqualified from acting as a director under the Act, resigns by notice in "
            "writing to the Company, or becomes of unsound mind.",
            "10.4 The directors shall not be required to hold any shares in the Company by way "
            "of qualification.",
        ]),
        ("11. POWERS AND DUTIES OF DIRECTORS", [
            "11.1 The business of the Company shall be managed by the directors, who may exercise "
            "all such powers of the Company as are not, by the Act or by this Constitution, "
            "required to be exercised by the Company in general meeting.",
            "11.2 The directors may exercise all the powers of the Company to borrow money, to "
            "mortgage or charge its undertaking, property and uncalled capital, and to issue "
            "debentures or give any other security for any debt, liability or obligation of the "
            "Company or of any third party.",
            "11.3 The directors may from time to time appoint one or more of their number to the "
            "office of managing director, and may delegate to him such of their powers as they "
            "think fit.",
        ]),
        ("12. PROCEEDINGS OF DIRECTORS", [
            "12.1 The directors may meet together for the despatch of business, adjourn and "
            "otherwise regulate their meetings as they think fit. Questions arising at any "
            "meeting shall be decided by a majority of votes, and in the case of an equality of "
            "votes the chairman shall have a second or casting vote.",
            "12.2 A resolution in writing signed by all the directors for the time being entitled "
            "to receive notice of a meeting of the directors shall be as valid and effectual as "
            "if it had been passed at a meeting duly convened and held.",
            "12.3 A director may participate in a meeting of directors by means of a telephone "
            "conference or any other form of communication by which all persons participating in "
            "the meeting are able to hear and be heard by all other participants.",
        ]),
        ("13. SECRETARY", [
            "The secretary shall be appointed by the directors for such term, at such "
            "remuneration and upon such conditions as they may think fit, and any secretary so "
            "appointed may be removed by them, subject always to the requirements of the Act.",
        ]),
        ("14. ACCOUNTS AND AUDIT", [
            "14.1 The directors shall cause proper accounting and other records to be kept, and "
            "shall distribute copies of financial statements as required by the Act.",
            "14.2 Auditors shall be appointed and their duties regulated in accordance with the "
            "Act, unless the Company is exempt from audit requirements under the Act.",
        ]),
        ("15. DIVIDENDS AND RESERVES", [
            "15.1 The Company may by ordinary resolution declare dividends, but no dividend shall "
            "exceed the amount recommended by the directors.",
            "15.2 No dividend shall be paid otherwise than out of profits available for "
            "distribution in accordance with the Act.",
            "15.3 The directors may, before recommending any dividend, set aside out of the "
            "profits of the Company such sums as they think proper as a reserve.",
        ]),
        ("16. NOTICES", [
            "16.1 A notice may be given by the Company to any member either personally, by "
            "sending it by post to his registered address, or by electronic communication to the "
            "electronic address supplied by him for that purpose.",
            "16.2 A notice sent by post shall be deemed to have been served on the day following "
            "that on which it was posted.",
        ]),
        ("17. WINDING UP", [
            "If the Company is wound up, the liquidator may, with the sanction of a special "
            "resolution and any other sanction required by the Act, divide amongst the members in "
            "specie the whole or any part of the assets of the Company.",
        ]),
        ("18. INDEMNITY", [
            "Subject to the Act, every director, auditor, secretary or other officer of the "
            "Company shall be entitled to be indemnified out of the assets of the Company against "
            "any liability incurred by him in defending any proceedings, whether civil or "
            "criminal, in which judgment is given in his favour or in which he is acquitted.",
        ]),
    ]


# --------------------------------------------------------------------------
# Defects
# --------------------------------------------------------------------------

SPELLING_SWAPS = [
    ("CONSTITUTION", "CONSITUTION"),
    ("REGISTERED OFFICE", "REGISTERD OFFICE"),
    ("Republic of Singapore", "Republic of Singpore"),
    ("SHARE CAPITAL AND VARIATION OF RIGHTS", "SHARE CAPITAL AND VARIATON OF RIGHTS"),
]


def apply_defect(content: dict[str, Any], defect: str | None) -> dict[str, Any]:
    content["defect"] = defect
    content["style_overrides"] = {}
    content["defect_description"] = ""
    if not defect:
        return content

    rng = seeded_rng(content["case_id"] + defect)

    if defect == "font_size":
        content["style_overrides"]["odd_clause"] = rng.randrange(4, 12)
        content["defect_description"] = (
            f"Clause block {content['style_overrides']['odd_clause'] + 1} set in Times-Roman "
            "10.5pt against Helvetica 9.5pt elsewhere."
        )

    elif defect == "numerical":
        delta = rng.choice([50_000, 75_000, 120_000])
        content["subscribers"][0]["shares"] += delta
        content["defect_description"] = (
            f"Subscriber shareholdings exceed the capital stated in clause 5.1 by {delta:,} shares."
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
            "Creator/Producer inconsistent with a corporate secretarial workflow; ModDate later "
            "than the adoption date."
        )

    elif defect == "cross_document":
        original = content["registered_address"]
        altered = re.sub(r"#\d{2}-\d{2,3}", "#11-27", original)
        content["registered_address"] = altered if altered != original else original + " "
        content["defect_description"] = (
            f"Registered office in clause 2 differs from Case Registry ({original})."
        )

    elif defect == "spelling":
        swap = SPELLING_SWAPS[rng.randrange(len(SPELLING_SWAPS))]
        content["style_overrides"]["spelling_swap"] = swap
        content["defect_description"] = f"Substituted '{swap[0]}' with '{swap[1]}'."

    elif defect == "misalignment":
        content["style_overrides"]["indent_shift_mm"] = rng.choice([6, 9, 12])
        content["style_overrides"]["indent_clause"] = rng.randrange(4, 12)
        content["defect_description"] = (
            f"Clause block {content['style_overrides']['indent_clause'] + 1} indented "
            f"{content['style_overrides']['indent_shift_mm']}mm off the document's left margin."
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

class _NumberedCanvas(pdfcanvas.Canvas):
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


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    mk = lambda n, **kw: ParagraphStyle(n, parent=base, **kw)
    return {
        "cover_act": mk("ca", fontName="Helvetica-Bold", fontSize=11, leading=16,
                        alignment=TA_CENTER, spaceAfter=4),
        "cover_sub": mk("cs", fontName="Helvetica", fontSize=10, leading=15,
                        alignment=TA_CENTER, spaceAfter=4),
        "cover_name": mk("cn", fontName="Helvetica-Bold", fontSize=16, leading=22,
                         alignment=TA_CENTER, spaceAfter=6),
        "clause_head": mk("ch", fontName="Helvetica-Bold", fontSize=9.5, leading=13,
                          spaceBefore=12, spaceAfter=5),
        "body": mk("bd", fontName="Helvetica", fontSize=9.5, leading=14,
                   alignment=TA_JUSTIFY, spaceAfter=6),
        "body_odd": mk("bo", fontName="Times-Roman", fontSize=10.5, leading=15,
                       alignment=TA_JUSTIFY, spaceAfter=6),
        "cell": mk("cl", fontName="Helvetica", fontSize=8.5, leading=11),
        "cell_head": mk("clh", fontName="Helvetica-Bold", fontSize=8.5, leading=11),
        "sign": mk("sg", fontName="Helvetica", fontSize=9.5, leading=14),
    }


def _page_furniture(c, page_no: int, total: int, content: dict[str, Any]) -> None:
    if page_no == 1:
        return  # cover page carries no running header/footer
    c.saveState()
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#666666"))
    c.drawString(MARGIN_L, 14 * mm, f"Constitution of {content['company_name']}")
    c.drawRightString(PAGE_W - MARGIN_R, 14 * mm, f"Page {page_no} of {total}")
    c.restoreState()


def render(content: dict[str, Any], out_pdf: str | Path, synthetic_tag: bool = True) -> Path:
    st = _styles()
    ov = content.get("style_overrides", {})
    swap = ov.get("spelling_swap")
    sw = (lambda t: t.replace(*swap)) if swap else (lambda t: t)

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(out_pdf),
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        title=f"Constitution of {content['company_name']}",
        author=content["firm"],
        subject=f"Company Constitution — {content['case_id']}",
    )
    doc.addPageTemplates(
        [PageTemplate(id="main",
                      frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height)])]
    )
    w = doc.width
    story: list[Any] = []

    # --- Cover --------------------------------------------------------------
    story.append(Spacer(1, 40))
    story.append(Paragraph("THE COMPANIES ACT 1967", st["cover_act"]))
    story.append(Paragraph(sw("REPUBLIC OF SINGAPORE"), st["cover_sub"]))
    story.append(Spacer(1, 18))
    story.append(Paragraph("PRIVATE COMPANY LIMITED BY SHARES", st["cover_act"]))
    story.append(Spacer(1, 30))
    story.append(Paragraph(sw("CONSTITUTION"), st["cover_act"]))
    story.append(Paragraph("OF", st["cover_sub"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph(content["company_name"].upper(), st["cover_name"]))
    story.append(Paragraph(f"(UEN {content['uen']})", st["cover_sub"]))
    story.append(Spacer(1, 24))
    story.append(
        Paragraph(f"Incorporated on {_d(content['incorporation_date'])}", st["cover_sub"])
    )
    story.append(
        Paragraph(f"Adopted on {_d(content['adoption_date'])}", st["cover_sub"])
    )
    story.append(Spacer(1, 90))
    story.append(Paragraph(content["firm"], st["cover_sub"]))
    story.append(Paragraph(content["firm_address"], st["cover_sub"]))
    story.append(PageBreak())

    # --- Clauses ------------------------------------------------------------
    odd = ov.get("odd_clause")
    ind_clause = ov.get("indent_clause")
    ind_mm = ov.get("indent_shift_mm", 0) * mm

    for i, (heading, paras) in enumerate(clauses(content)):
        body_style = st["body_odd"] if odd == i else st["body"]
        if ind_clause == i and ind_mm:
            body_style = ParagraphStyle(
                f"ind{i}", parent=body_style, leftIndent=ind_mm, rightIndent=-ind_mm
            )
            head_style = ParagraphStyle(f"indh{i}", parent=st["clause_head"], leftIndent=ind_mm)
        else:
            head_style = st["clause_head"]

        block = [Paragraph(sw(heading), head_style)] + [
            Paragraph(sw(p), body_style) for p in paras
        ]
        story.append(KeepTogether(block) if len(paras) <= 2 else block[0])
        if len(paras) > 2:
            story.extend(block[1:])

    # --- Execution page -----------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("EXECUTION", st["clause_head"]))
    story.append(
        Paragraph(
            "We, the several persons whose names, addresses and descriptions are subscribed "
            "below, are desirous of being formed into a company in pursuance of this "
            "Constitution, and we respectively agree to take the number of shares in the capital "
            "of the Company set opposite our respective names.",
            st["body"],
        )
    )
    story.append(Spacer(1, 8))

    rows = [
        [
            Paragraph("Name, Address and Description of Subscriber", st["cell_head"]),
            Paragraph("Identification No.", st["cell_head"]),
            Paragraph("Number of Shares Taken", st["cell_head"]),
        ]
    ]
    for s in content["subscribers"]:
        rows.append(
            [
                Paragraph(f"<b>{s['name']}</b><br/>{s['address']}", st["cell"]),
                Paragraph(str(s["id"]), st["cell"]),
                Paragraph(f"{s['shares']:,}", st["cell"]),
            ]
        )
    total_shares = sum(s["shares"] for s in content["subscribers"])
    rows.append(
        [
            Paragraph("<b>Total shares taken</b>", st["cell"]),
            Paragraph("", st["cell"]),
            Paragraph(f"<b>{total_shares:,}</b>", st["cell"]),
        ]
    )
    t = Table(rows, colWidths=[0.52 * w, 0.24 * w, 0.24 * w], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9E9E9")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(t)

    story.append(Spacer(1, 16))
    story.append(
        Paragraph(f"Dated this {content['adoption_date'].strftime('%d')} day of "
                  f"{content['adoption_date'].strftime('%B %Y')}.", st["sign"])
    )
    story.append(Spacer(1, 22))
    story.append(
        Paragraph("Witness to the above signatures:", st["sign"])
    )
    story.append(Spacer(1, 22))
    story.append(Paragraph("_____________________________________", st["sign"]))
    story.append(Paragraph(f"<b>{content['witness']}</b>", st["sign"]))
    story.append(Paragraph(f"for and on behalf of {content['firm']}", st["sign"]))

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
        filename = record["doc_row"].get("Filename") or f"{record['case_id']}_Constitution.pdf"
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
        help="honour the mastersheet fraud markers; omit for clean documents only",
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
