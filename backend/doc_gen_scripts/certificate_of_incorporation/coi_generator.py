from __future__ import annotations

import argparse
import html
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import truth
from case_facts import seeded_rng

FONT = "Helvetica"

# The certificate is always signed off by the same (fictitious) registrar.
REGISTRAR_NAME = "LIM BEE HOON"


DEFAULT_CONTEXT: dict[str, Any] = {
    "company_name": "ABC COMPANY PTE. LTD.",
    "name_change_note": "name change effective from 01 Oct 2016",
    "uen": "201600008M",
    "incorporation_date": date(2016, 8, 8),
    "company_status": "Limited Exempt Private Company",
    "former_names": [
        {
            "name": "Def Company Pte. Ltd.",
            "effective_from": "08 Aug 2016",
        }
    ],
    "verification_url": "https://www.acratrustbar.gov.sg/verify/4n3NIEdM",
}

# Word pools for synthesizing a former company name, distinct per case, rather than
# every case that keeps its former-names section showing the same literal
# "Def Company Pte. Ltd." from DEFAULT_CONTEXT.
FORMER_NAME_PREFIXES = [
    "Golden", "Silver", "Crimson", "Azure", "Ivory", "Titan", "Meridian", "Zenith",
    "Coral", "Vantage", "Harbor", "Diamond", "Aurora", "Falcon", "Emerald", "Nexus",
    "Pacific", "Sterling", "Orchid", "Summit",
]
FORMER_NAME_NOUNS = ["Harvest", "Oak", "Bay", "Point", "Peak", "Grove", "Light", "Rim", "Ridge", "Field", "Cove", "Bridge"]
FORMER_NAME_TYPES = [
    "Trading", "Logistics", "Construction", "Marine Services", "Manufacturing",
    "Distribution", "Consulting Group", "Engineering", "Holdings", "Enterprises",
    "Industries", "Investments",
]


def _synthesize_former_name(rng: random.Random, current_name: str) -> str:
    """A plausible former company name, distinct from the current one."""
    current_upper = current_name.strip().upper()
    name = current_name
    for _ in range(10):
        name = f"{rng.choice(FORMER_NAME_PREFIXES)} {rng.choice(FORMER_NAME_NOUNS)} {rng.choice(FORMER_NAME_TYPES)} Pte. Ltd."
        if name.upper() != current_upper:
            break
    return name


def _parse_date(value: Any) -> date:
    """Coerce a date/datetime/string into a plain date (dropping any time-of-day)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Could not parse incorporation_date: {value!r}")


def _generate_receipt_no(inc_date: date) -> str:
    """ACRA-style receipt number: YYMMDD (from the incorporation date) padded with
    random digits to the same total length as a real receipt no."""
    prefix = inc_date.strftime("%y%m%d")
    suffix_len = max(12 - len(prefix), 0)
    suffix = "".join(str(random.randint(0, 9)) for _ in range(suffix_len))
    return f"ACRA{prefix}{suffix}"


OUTPUT_FILE = "coi_certificate.pdf"


def load_context(json_path: Path | None) -> dict[str, Any]:
    context = dict(DEFAULT_CONTEXT)
    if json_path is None:
        return context

    with json_path.open("r", encoding="utf-8") as handle:
        user_context = json.load(handle)

    if not isinstance(user_context, dict):
        raise ValueError("The JSON input must contain an object at the top level.")

    context.update(user_context)
    return context


ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "images"
LOGO_PATH = ASSETS_DIR / "acra_logo.jpg"
STAMP_PATH = ASSETS_DIR / "acra_stamp.png"

_IMAGE_CACHE: dict[str, Any] = {}


def _load_logo():
    """The wordmark JPEG already has a plain white background, so it can be drawn
    as-is (no alpha work needed)."""
    if "logo" in _IMAGE_CACHE:
        return _IMAGE_CACHE["logo"]
    from reportlab.lib.utils import ImageReader

    reader = ImageReader(str(LOGO_PATH)) if LOGO_PATH.exists() else None
    _IMAGE_CACHE["logo"] = reader
    return reader


def _load_stamp(white_cutoff: int = 245):
    """The seal PNG is a scan with a solid white field (fully opaque), so key out
    anything near-white to transparent before drawing it over the page."""
    if "stamp" in _IMAGE_CACHE:
        return _IMAGE_CACHE["stamp"]
    if not STAMP_PATH.exists():
        _IMAGE_CACHE["stamp"] = None
        return None

    import io

    from PIL import Image
    from reportlab.lib.utils import ImageReader

    img = Image.open(STAMP_PATH).convert("RGBA")
    pixels = img.load()
    w, h = img.size
    if pixels:
        for yy in range(h):
            for xx in range(w):
                r, g, b, _a = pixels[xx, yy]
                if r >= white_cutoff and g >= white_cutoff and b >= white_cutoff:
                    pixels[xx, yy] = (r, g, b, 0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    reader = ImageReader(buf)
    _IMAGE_CACHE["stamp"] = reader
    return reader


def write_pdf_reportlab(context: dict[str, Any], output_path: Path) -> None:
    """Render the certificate directly with ReportLab, mirroring coi_template.html's
    layout so no WeasyPrint / system-library install is required.

    coi_template.html lays everything out in a 794x1123 CSS-px canvas. At the
    browser's 96dpi that canvas is exactly A4 at the PDF's 72dpi (794px = 595.5pt,
    1123px = 842.25pt), so every position/size below is copied straight from the
    template's CSS and multiplied by 0.75 (96 -> 72 dpi) to land in the same place.
    """
    import sys

    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import simpleSplit
    from reportlab.platypus import Paragraph
    from reportlab.pdfgen import canvas as pdfcanvas

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from synthetic_signature import draw_signature

    PAGE_W, PAGE_H = A4

    def px(value: float) -> float:
        return value * 0.75

    ORANGE = colors.HexColor("#ed7d22")
    BAR_ORANGE = colors.HexColor("#f6953d")
    LOGO_GREY = colors.HexColor("#6f6b61")
    CAPTION_GREY = colors.HexColor("#77736b")
    NAVY = colors.HexColor("#243d72")
    NOTE_GREY = colors.HexColor("#333333")
    URL_GREY = colors.HexColor("#333333")
    VERIFY_BG = colors.HexColor("#fff1dd")
    BLACK = colors.black
    WHITE = colors.white

    left = px(70)
    right = PAGE_W - px(70)
    content_w = right - left

    c = pdfcanvas.Canvas(str(output_path), pagesize=A4)
    c.setTitle("Certificate Confirming Incorporation of Company")

    c.setFillColor(BAR_ORANGE)
    c.rect(0, 0, px(30), PAGE_H, stroke=0, fill=1)

    def draw_lines(text: str, x: float, top: float, *, font: str, size: float,
                   leading: float | None = None, color=BLACK, width: float | None = None,
                   align: str = "left") -> float:
        """Draw text (wrapped to `width` if given) starting with its cap-height at
        `top`; returns the y just below the last line drawn."""
        leading = leading or size * 1.3
        w = width if width is not None else 0.0
        lines = simpleSplit(text, font, size, width) if width else [text]
        c.setFillColor(color)
        c.setFont(font, size)
        y = top - size
        for line in lines:
            if align == "right":
                c.drawRightString(x + w, y, line)
            elif align == "center":
                c.drawCentredString(x + w / 2, y, line)
            else:
                c.drawString(x, y, line)
            y -= leading
        return y + leading - size

    def draw_paragraph(html_text: str, x: float, top: float, width: float, *,
                        size: float, leading: float, color=BLACK) -> float:
        style = ParagraphStyle("body", fontName=FONT, fontSize=size, leading=leading,
                                textColor=color)
        p = Paragraph(html_text, style)
        _, h = p.wrapOn(c, width, PAGE_H)
        p.drawOn(c, x, top - h)
        return top - h

    y = PAGE_H - px(58)  # top padding

    # --- ACRA wordmark ---
    logo = _load_logo()
    logo_w = px(180)  # matches .acra-logo { width: 180px }
    if logo is not None:
        iw, ih = logo.getSize()
        logo_h = logo_w * ih / iw
        c.drawImage(logo, left, y - logo_h, width=logo_w, height=logo_h, mask="auto")
        y -= logo_h
    else:
        c.setFillColor(LOGO_GREY)
        c.setFont("Times-Bold", px(38))
        c.drawString(left, y - px(30), "A C R A")
        y = draw_lines(
            "ACCOUNTING AND CORPORATE   REGULATORY AUTHORITY", left, y - px(38) - px(6),
            font=FONT, size=px(6), leading=px(7.5), color=CAPTION_GREY, width=px(220),
        )
    y -= px(48)  # .acra-header margin-bottom

    # --- Title ---
    y = draw_lines(
        "Certificate Confirming Incorporation of Company", left, y,
        font=f"{FONT}-Bold", size=px(25), leading=px(26.5), color=BLACK, width=content_w,
    )
    y -= px(25)

    # --- Presented to / company name ---
    y = draw_lines("THIS CERTIFICATE IS PRESENTED TO", left, y, font=FONT, size=px(11), color=BLACK)
    y -= px(8)
    y = draw_lines(str(context.get("company_name", "")).upper(), left, y,
                    font=f"{FONT}-Bold", size=px(17), color=ORANGE, width=content_w)
    y -= px(8)
    if context.get("name_change_note"):
        y = draw_lines(f"({context['name_change_note']})", left, y, font=FONT, size=px(12), color=NOTE_GREY)
        y -= px(25)

    # --- UEN ---
    y = draw_lines("OF UEN", left, y, font=FONT, size=px(11), color=BLACK)
    y -= px(8)
    y = draw_lines(str(context.get("uen", "")), left, y, font=f"{FONT}-Bold", size=px(17), color=ORANGE)
    y -= px(27)

    # --- Body paragraph (with bold spans, so this one uses Paragraph) ---
    inc_date_obj = _parse_date(context.get("incorporation_date", ""))
    inc_date_display = inc_date_obj.strftime("%d %b %Y")
    comp_status = html.escape(str(context.get("company_status", "")))
    body_html = (
        f"The company was incorporated under the Companies Act 1967, on and from "
        f"<b>{html.escape(inc_date_display)}</b>, and is a <b>{comp_status}</b>."
    )
    y = draw_paragraph(body_html, left, y, min(px(520), content_w), size=px(11), leading=px(17))
    y -= px(35)

    # --- Former names (only shown when the company actually has any) ---
    former_names = context.get("former_names") or []
    if former_names:
        y = draw_lines("THE COMPANY WAS FORMERLY KNOWN AS:", left, y, font=FONT, size=px(11), color=BLACK)
        y -= px(10)

        col2_x = right - px(10)
        c.setFont(f"{FONT}-Bold", px(10))
        c.setFillColor(BLACK)
        c.drawString(left, y - px(10), "Previous Name(s)")
        c.drawRightString(col2_x, y - px(10), "Effective From")
        y -= px(10) + px(6)

        for f in former_names:
            name, effective = str(f.get("name", "")), str(f.get("effective_from", ""))
            c.setFont(FONT, px(9))
            c.setFillColor(BLACK)
            c.drawString(left, y - px(9), name)
            c.drawRightString(col2_x, y - px(9), effective)
            y -= px(9) * 1.4
        y -= px(5)

        y = draw_lines(
            "Only the 5 most recent former names are listed. Any change in name effected "
            "before 13 Jan 2003 will not be listed.",
            left, y, font=FONT, size=px(8), leading=px(11.2), color=BLACK, width=content_w,
        )

    # --- Bottom section: signature block (left) and verify box (right), both
    # bottom-aligned px(20) above the page edge, matching .bottom{bottom:20px}. ---
    bottom_y = px(20)
    sig_x = px(70)
    verify_w = px(290)
    verify_x = (PAGE_W - px(45)) - verify_w

    # Verify Document box
    verify_h = px(100)
    c.setFillColor(VERIFY_BG)
    c.rect(verify_x, bottom_y, verify_w, verify_h, stroke=0, fill=1)
    text_x = verify_x + px(13)
    text_w = verify_w - px(13) - px(65) - px(13)
    vy = bottom_y + verify_h - px(13)
    vy = draw_lines("Verify Document Instantly", text_x, vy, font=f"{FONT}-Bold", size=px(10), color=BLACK)
    vy -= px(4)
    vy = draw_lines("Check if this document is issued by ACRA and is up-to-date.",
                     text_x, vy, font=FONT, size=px(10), leading=px(14), color=BLACK, width=text_w)
    vy -= px(4)
    draw_lines(str(context.get("verification_url", "")), text_x, vy,
               font=FONT, size=px(9), leading=px(11.5), color=URL_GREY, width=text_w)

    # QR-style placeholder square (decorative, not a scannable code)
    qr_size = px(65)
    qr_x = verify_x + verify_w - px(13) - qr_size
    qr_y = bottom_y + (verify_h - qr_size) / 2
    c.setFillColor(BLACK)
    c.rect(qr_x, qr_y, qr_size, qr_size, stroke=0, fill=1)
    c.setFillColor(WHITE)
    inset = px(4)
    c.rect(qr_x + inset, qr_y + inset, qr_size - 2 * inset, qr_size - 2 * inset, stroke=0, fill=1)
    cell = (qr_size - 2 * inset) / 6
    pattern = [1, 0, 1, 1, 0, 1, 1, 1, 0, 0, 1, 0, 0, 1, 1, 0, 1, 1]
    c.setFillColor(BLACK)
    for i, bit in enumerate(pattern):
        if bit:
            gx = qr_x + inset + (i % 6) * cell
            gy = qr_y + inset + (i // 6) * cell
            c.rect(gx, gy, cell, cell, stroke=0, fill=1)
    finder = px(17)
    for fx, fy in ((qr_x + px(2), qr_y + qr_size - px(2) - finder), (qr_x + qr_size - px(2) - finder, qr_y + px(2))):
        c.setFillColor(WHITE)
        c.rect(fx, fy, finder, finder, stroke=0, fill=1)
        c.setStrokeColor(BLACK)
        c.setLineWidth(px(4))
        c.rect(fx, fy, finder, finder, stroke=1, fill=0)

    # Signature block: ACRA seal stamp, cursive "signature", then registrar info
    # (typed name, receipt no, date) — the cursive mark sits directly above the
    # typed name, same convention as the FATCA/CRS self-cert's signatory stamps.
    # A re-issued certificate (after a name change) carries its own issue date; the original is dated
    # the day of incorporation.
    issue_date_obj = _parse_date(context["issue_date"]) if context.get("issue_date") else inc_date_obj
    issue_date_display = issue_date_obj.strftime("%d %b %Y")
    receipt_no = context.get("receipt_no") or _generate_receipt_no(issue_date_obj)
    sig_info_lines = 5  # registrar name + 2 org lines + receipt no + date
    info_h = px(9) * 1.35 * sig_info_lines
    sig_line_h = px(9) * 1.9
    info_top = bottom_y + info_h + sig_line_h
    iy = info_top

    c.setFillColor(BLACK)
    draw_signature(
        c, REGISTRAR_NAME.title(), sig_x, iy - sig_line_h + px(6),
        seed=f"{context.get('uen', '')}|registrar-signature", size=px(16),
    )
    iy -= sig_line_h

    c.setFont(f"{FONT}-Bold", px(9))
    c.setFillColor(BLACK)
    c.drawString(sig_x, iy - px(9), REGISTRAR_NAME)
    iy -= px(9) * 1.35
    for line in ("ASST REGISTRAR OF COMPANIES & BUSINESS NAMES",
                 "ACCOUNTING AND CORPORATE REGULATORY AUTHORITY SINGAPORE"):
        c.setFont(FONT, px(9))
        c.drawString(sig_x, iy - px(9), line)
        iy -= px(9) * 1.35
    c.setFont(FONT, px(9))
    c.drawString(sig_x, iy - px(9), f"RECEIPT NO.  : {receipt_no}")
    iy -= px(9) * 1.35
    c.drawString(sig_x, iy - px(9), f"DATE          : {issue_date_display}")

    stamp_r = px(30)
    stamp_cx = sig_x + stamp_r
    stamp_y = info_top + px(4) + stamp_r

    stamp = _load_stamp()
    if stamp is not None:
        iw, ih = stamp.getSize()
        stamp_size = stamp_r * 2
        sw, sh = (stamp_size, stamp_size * ih / iw) if iw >= ih else (stamp_size * iw / ih, stamp_size)
        c.drawImage(stamp, stamp_cx - sw / 2, stamp_y - sh / 2, width=sw, height=sh, mask="auto")
    else:
        c.setStrokeColor(NAVY)
        c.setLineWidth(1.2)
        c.circle(stamp_cx, stamp_y, stamp_r, stroke=1, fill=0)
        c.circle(stamp_cx, stamp_y, stamp_r - px(6), stroke=1, fill=0)
        c.setFillColor(NAVY)
        c.setFont(f"{FONT}-Bold", px(7))
        c.drawCentredString(stamp_cx, stamp_y + px(6), "ACRA")
        c.drawCentredString(stamp_cx, stamp_y - px(2), "OFFICIAL")
        c.drawCentredString(stamp_cx, stamp_y - px(10), "SEAL")

    c.showPage()
    c.save()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the ACRA certificate of incorporation to PDF."
    )
    parser.add_argument(
        "--data",
        type=Path,
        help="Optional JSON file containing placeholder values.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name(OUTPUT_FILE),
        help="Output PDF file path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    context = load_context(args.data)
    if not context.get("receipt_no"):
        context["receipt_no"] = _generate_receipt_no(_parse_date(context.get("incorporation_date", "")))
    write_pdf_reportlab(context, args.output)
    print(f"PDF written to: {args.output}")


if __name__ == "__main__":
    main()


def generate_certificate_of_incorporation(
    output_path: str,
    *,
    company_name: str,
    company_number: str,
    incorporation_date: str | date | datetime | None = None,
    company_type: str | None = None,
    receipt_no: str | None = None,
    verification_url: str | None = None,
    latest_date: date | None = None,
) -> dict:
    """Compatibility wrapper to render a COI PDF from structured args.

    Returns a dict with keys similar to the older generator for compatibility.
    """
    context = dict(DEFAULT_CONTEXT)
    context.update({
        "company_name": company_name or context.get("company_name"),
        "uen": company_number or context.get("uen"),
        "incorporation_date": incorporation_date or context.get("incorporation_date"),
        "company_status": company_type or context.get("company_status"),
    })
    if receipt_no:
        context["receipt_no"] = receipt_no
    if verification_url:
        context["verification_url"] = verification_url

    # Not every company has a former name on record - vary this deterministically per
    # company so the synthetic dataset isn't 100% "has former names". When one is
    # kept, synthesize a name and effective date specific to this case instead of
    # DEFAULT_CONTEXT's literal "Def Company Pte. Ltd." showing up on every case.
    rng = seeded_rng(f"{context['uen']}|former-names")
    if rng.random() >= 0.4:
        context["former_names"] = []
        context["name_change_note"] = None
    else:
        incorporation_date = _parse_date(context.get("incorporation_date", ""))
        effective = date(
            incorporation_date.year + rng.randint(1, 6), rng.randint(1, 12), rng.randint(1, 28)
        )
        # The certificate listing a former name is one re-issued after the change, so it is dated
        # after the change, and no later than `latest_date` (the application it was submitted with).
        # A change that would land too close to that date is moved back; draws above are unchanged so
        # the ACRA business profile, which repeats them, still finds the same former name.
        if latest_date:
            effective = max(min(effective, latest_date - timedelta(days=60)),
                            incorporation_date + timedelta(days=30))
        reissue_rng = seeded_rng(f"{context['uen']}|coi-reissue")
        issue_date = effective + timedelta(days=reissue_rng.randint(7, 45))
        if latest_date:
            issue_date = max(effective, min(issue_date, latest_date - timedelta(days=1)))
        context["issue_date"] = issue_date
        if not receipt_no:
            context["receipt_no"] = _generate_receipt_no(issue_date)
        effective_str = effective.strftime("%d %b %Y")
        context["former_names"] = [{
            "name": _synthesize_former_name(rng, str(context["company_name"])),
            "effective_from": effective_str,
        }]
        context["name_change_note"] = f"name change effective from {effective_str}"

    out_path = Path(output_path)
    write_pdf_reportlab(context, out_path)
    truth.record(out_path, "certificate_of_incorporation", truth.certificate_of_incorporation(context))
    return {"output_path": str(out_path), "company_name": context["company_name"], "company_number": context["uen"]}
