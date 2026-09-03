"""
docgen_fatca_crs.py — for every case in the mastersheet, deterministically pick one
of two FATCA/CRS entity self-cert templates (citi / dbs) and fill it.

Single-file merge of what used to be three scripts (personas_from_mastersheet.py,
fill_selfcert.py, and the original thin docgen_fatca_crs.py driver) plus a small
"pick a template" wrapper, kept together because none of the three pieces are used
anywhere outside this pipeline. case_facts.py stays a separate companion file
instead, because docgen_constitution.py, docgen_incumbency.py and docgen_acra_
profile.py all import it too — forking it in here would fork their logic as well.

A third template (UOB, crsfatca.pdf) was dropped: its 16-page XFA-derived AcroForm
needed per-block-irregular field names and widget export states hand-verified against
the PDF at every step, which made it too fragile/high-maintenance to keep alongside
the other two -- see git history (or ask) if it's ever worth reviving.

Company-level identity (name, UEN, registered address, application date) and the
controlling-person roster come from case_facts.company_facts(), so a self-cert always
agrees with every other document generated for a case, and a mastersheet edit is
picked up automatically. The bank-template pick is seeded on the case ID (same
seeded_rng() convention as case_facts.py), so re-running produces the same assignment
for the same case rather than a fresh shuffle each time. It ignores each case's actual
"Bank Name" -- picking uniformly at random covers every case instead, including the
ones that don't bank with either of these two.

Both supplied templates are AcroForm PDFs, so filling is a field-name lookup rather
than coordinate drawing:

    citi  -> Combined_Self_Cert_English (1).pdf (Citi TTS, 4 pages)
    dbs   -> fatca-self-certification-form.pdf  (DBS Entity Self-Certification, 6 pages)

Assumptions baked in for clean cases (override per case in CASE_OVERRIDES):
  - Singapore-incorporated Pte Ltd, tax resident in Singapore only
  - Singapore TIN = the company UEN
  - FATCA classification = Active NFFE (Active Business, DBS box G)
  - U.S. tax classification = Corporation
  - Signatory = the Director & UBO from the Individuals tab
  - Signing date = the loan application date from Case Registry
  - On dbs, every controlling person listed also signs their own appendix row
  - citi's Part 5 Signature is rendered in a bundled cursive font instead of typed
    plainly, so a batch of self-certs doesn't all show identical machine-typed
    "signatures" -- picked deterministically per case from SIGNATURE_FONTS

Usage:
cd backend/doc_gen_scripts/fatca_crs
python docgen_fatca_crs.py ../v9_document_mastersheet.xlsx --outdir out/
python docgen_fatca_crs.py ../v9_document_mastersheet.xlsx --outdir out/ --cases C001 C002
python docgen_fatca_crs.py ../v9_document_mastersheet.xlsx --outdir out/ --no-strict
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

from case_facts import company_facts, seeded_rng

BLANK_PDFS = {
    "citi": "Combined_Self_Cert_English (1).pdf",
    "dbs": "fatca-self-certification-form.pdf",
}


# --------------------------------------------------------------------------
# Vocabularies shared by all three templates (persona speaks these, maps translate)
# --------------------------------------------------------------------------

# FATCA classification the persona declares.
FATCA_CLASSES = {
    "active_nffe",
    "publicly_traded_nffe",
    "publicly_traded_affiliate",
    "passive_nffe",
    "participating_ffi",
    "nonparticipating_ffi",
    "start_up",
    "nonprofit",
    "public_sector",
    "international_org",
    "treasury_centre",
    "holding_company",
    "liquidating",
}

# Citi Part 2B — U.S. tax classification checkbox names.
CITI_BENEFICIAL_OWNER = {
    "central_bank": "Central Bank of Issue",
    "corporation": "Corporation",
    "foreign_govt_controlled": "Foreign Government  Controlled Entity",
    "tax_exempt": "TaxExempt Organization",
    "complex_trust": "Complex Trust",
    "estate": "Estate",
    "foreign_govt_integral": "Foreign Government  Integral Part",
    "private_foundation": "Private Foundation",
}

# DBS section 1/2 lettered boxes.
DBS_CLASS_BOX = {
    "participating_ffi": "boxa",
    "nonparticipating_ffi": "boxb",
    "other_ffi": "boxc",
    "investment_entity_npj": "boxd",
    "investment_entity_other": "boxe",
    "depository_institution": "boxf",
    "active_nffe": "boxg",          # G — Active Business
    "start_up": "boxh",
    "nonprofit": "boxi",
    "public_sector": "boxj",
    "international_org": "boxk",
    "publicly_traded_nffe": "boxl",
    "publicly_traded_affiliate": "boxl",
    "treasury_centre": "boxm",
    "holding_company": "boxn",
    "liquidating": "boxo",
    "none_of_the_above": "boxp",
}

# DBS controlling-person radio values, verified against widget coordinates.
DBS_CP_TYPE = {
    "legal_person_ownership": "/0",
    "legal_person_senior_managing_official": "/1",
    "legal_person_other_means": "/3",
    "trust_settlor": "/2",
    "trust_trustee": "/4",
    "trust_protector": "/5",
    "trust_beneficiary": "/7",
    "trust_other": "/6",
    "settlor_equivalent": "/8",
    "trustee_equivalent": "/9",
    "protector_equivalent": "/11",
    "beneficiary_equivalent": "/10",
    "other_equivalent": "/12",
}


# --------------------------------------------------------------------------
# Template mappings
# --------------------------------------------------------------------------

def _citi_values(p: dict[str, Any]) -> dict[str, Any]:
    v: dict[str, Any] = {}
    addr = p.get("registered_address", {})
    mail = p.get("mailing_address") or {}

    v["1 Name of Entity or Organization"] = p.get("entity_name", "")
    v["2 Country of Incorporation or Organization"] = p.get("country_of_incorporation", "")
    v["3 US Tax Identification Number TIN 9 character"] = p.get("us_tin", "")
    v["4 Permanent Residence Address"] = addr.get("street", "")
    v["5 City Postal Zip"] = addr.get("city_state_postal", "")
    v["6 Country"] = addr.get("country", "")
    if mail:
        v["7 Mailing Address"] = mail.get("street", "")
        v["8 City Postal Zip"] = mail.get("city_state_postal", "")
        v["9 Country"] = mail.get("country", "")

    # Part 1B — up to three tax residences. Country fields are "Country N";
    # the TIN fields are named "1", "2", "3".
    for i, res in enumerate(p.get("tax_residences", [])[:3], start=1):
        suffix = "" if i == 1 else f"_{i}"
        v[f"Country {i}"] = res.get("country", "")
        v[str(i)] = res.get("tin", "")
        reason = (res.get("no_tin_reason") or "").upper()
        if reason in ("A", "B", "C"):
            v[f"Reason {reason}{suffix}"] = "/On"

    # Part 1C — explanations for Reason B, one row per residence row.
    for i, res in enumerate(p.get("tax_residences", [])[:3], start=1):
        if res.get("reason_b_explanation"):
            v[f"{i}_2"] = res["reason_b_explanation"]

    # Part 2A — this form only offers two FATCA statuses.
    cls = p.get("fatca_classification", "")
    if cls == "active_nffe":
        v["Active NFFE if selected Part 2B and Part 3 must be completed"] = "/On"
        v["I certify that"] = "/On"                       # Part 3 certification
    elif cls in ("publicly_traded_nffe", "publicly_traded_affiliate"):
        v[
            "Publicly Traded NFFE or NFFE Affiliate of a Publicly Traded Entity "
            "if selected Part 2B and Part 4 must be completed"
        ] = "/On"
        if cls == "publicly_traded_nffe":
            v["I certify that_2"] = "/On"                 # Part 4 A
            v["B"] = p.get("securities_exchange", "")     # exchange name blank under A
        else:
            v["I certify that_3"] = "/On"                 # Part 4 B
            # NOTE: these two field names are offset by one line in the source PDF.
            v["The name of the securities market on which the stock is regularly traded is"] = (
                p.get("listed_affiliate_name", "")
            )
            v["and"] = p.get("securities_exchange", "")

    # Part 2B
    bo = CITI_BENEFICIAL_OWNER.get(p.get("beneficial_owner_status", ""))
    if bo:
        v[bo] = "/On"

    if p.get("apply_crs_all_countries"):
        v[
            "Please apply this CRS selfcertification as described above "
            "excluding any countries you have specified"
        ] = "/On"

    # Part 5 — single signatory.
    signers = p.get("signatories", [])
    if signers:
        s = signers[0]
        v["Part 5 Signature"] = s.get("signature_text", s.get("name", ""))
        v["Part 5 Print Name"] = s.get("name", "")
        v["Date"] = s.get("date", "")
        v["Part 5 Capacity"] = s.get("capacity", "")
    return v


def _split_giin(giin: str) -> list[str]:
    """GIIN is XXXXXX.XXXXX.XX.XXX across four boxes."""
    parts = [x for x in re.split(r"[.\s-]+", giin.strip()) if x]
    return (parts + ["", "", "", ""])[:4]


def _dbs_values(p: dict[str, Any]) -> dict[str, Any]:
    v: dict[str, Any] = {}
    addr = p.get("registered_address", {})

    v["Name Of Entity 1"] = p.get("entity_name", "")
    street = addr.get("street", "")
    city = addr.get("city_state_postal", "")
    country = addr.get("country", "")
    v["registered entity address"] = ", ".join(x for x in (street, city, country) if x)
    v["country of incorporation 1"] = p.get("country_of_incorporation", "")

    for i, res in enumerate(p.get("tax_residences", [])[:3], start=1):
        v[f"country/jurisdiction of tax residence {i} 1"] = res.get("country", "")
        v[f"tax indentification number {i} 1"] = res.get("tin", "")
        reason = (res.get("no_tin_reason") or "").upper()
        if reason in ("A", "B", "C"):
            v[f"ABC{i}"] = f"/{reason}"
        if res.get("reason_b_explanation"):
            v[f"reason b {i} 1"] = res["reason_b_explanation"]

    if p.get("not_tax_resident_of_incorporation_country"):
        v["Check Box2"] = "/Yes"

    cls = p.get("fatca_classification", "")
    if cls == "passive_nffe":
        v["boxq1"] = "/Yes"   # Passive NFFE (FATCA column)
        v["boxq2"] = "/Yes"   # Passive NFE (CRS column)
    else:
        box = DBS_CLASS_BOX.get(cls)
        if box:
            v[box] = "/Yes"

    if p.get("giin"):
        for i, chunk in enumerate(_split_giin(p["giin"]), start=1):
            v[f"giin {i}"] = chunk

    for i, cp in enumerate(p.get("controlling_persons", [])[:4], start=1):
        v[f"Name of controlling person {i}"] = cp.get("name", "")
        radio = {
            1: "option 3 5",
            2: "option 3 1 5",
            3: "option 3 2 5",
            4: "option 3 1 6",
        }[i]
        opt = DBS_CP_TYPE.get(cp.get("type", ""))
        if opt:
            v[radio] = opt

    # Appendix sign-off — every controlling person listed above also signs their own
    # appendix row (same shared signing date as the entity-level signatory).
    sign_date = next((s.get("date", "") for s in p.get("signatories", [])), "")
    for i, cp in enumerate(p.get("controlling_persons", [])[:4], start=1):
        v[f"primary entity authorised person name 1 appendix {i}"] = cp.get("name", "")
        v[f"primary entity date 1 appendix {i}"] = sign_date
    return v


TEMPLATES = {"citi": _citi_values, "dbs": _dbs_values}


# --------------------------------------------------------------------------
# Filling
# --------------------------------------------------------------------------

# The DBS template bakes each field's caption ("Name of Entity", "Registered Address
# of Entity", "Country of Incorporation") as static page text sitting just above the
# widget's own /Rect, expecting a viewer to start the answer on the line below. pypdf
# generates its multiline appearance top-anchored to the Rect itself, so the answer's
# first line lands almost on the caption's baseline instead of below it. DBS_CAPTION_
# CLEARANCE is the vertical gap (in points) reserved at the top of each of these
# fields' Rect before any answer text is drawn, so the redrawn appearance clears the
# caption instead of overlapping it.
DBS_CAPTION_CLEARANCE = {
    "Name Of Entity 1": 10.5,
    "registered entity address": 14.0,
    "country of incorporation 1": 10.5,
}
_DBS_FONT_SIZE = 8.0
_DBS_LINE_HEIGHT = 9.5

# citi's "Part 5 Signature" is a real AcroForm field, separate from the printed name
# below it. Rendering it in a real handwriting font -- picked deterministically per
# case, same seeded_rng() convention as case_facts.py -- means a batch of self-certs
# doesn't all show the identical machine-typed "signature".
SIGNATURE_FIELDS = {"citi": "Part 5 Signature"}
_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
# Caveat and Dancing Script, both SIL Open Font License (OFL.txt alongside each .ttf
# here) -- redistribution-safe, and bundled rather than referencing an OS-installed
# font so the same signature renders identically on Windows/Linux/Mac alike.
SIGNATURE_FONTS = ["Caveat", "DancingScript"]
for _name in SIGNATURE_FONTS:
    pdfmetrics.registerFont(TTFont(_name, str(_FONT_DIR / f"{_name}.ttf")))


def _render_cursive_signature(writer: PdfWriter, obj: Any, text: str, font_name: str) -> None:
    """Replace a signature field's auto-generated typed appearance with the same text
    rendered in a bundled handwriting font. Builds a throwaway single-field PDF with
    reportlab (which handles TrueType embedding -- metrics, FontDescriptor, FontFile2 --
    correctly on its own) and clones its already-correct font resource across into the
    target document, the same cross-document object copy pypdf itself uses internally
    for merging PDFs, rather than hand-building a font dictionary from scratch."""
    rect = [float(x) for x in obj["/Rect"]]
    width, height = rect[2] - rect[0], rect[3] - rect[1]

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(width, height))
    c.setFont(font_name, height * 0.85)
    c.drawString(2, height * 0.22, text)
    c.save()
    buf.seek(0)
    mini_page = PdfReader(buf).pages[0]
    content_bytes = mini_page.get_contents().get_data()

    stream = DecodedStreamObject()
    stream.set_data(content_bytes)
    stream[NameObject("/Type")] = NameObject("/XObject")
    stream[NameObject("/Subtype")] = NameObject("/Form")
    stream[NameObject("/FormType")] = NumberObject(1)
    stream[NameObject("/BBox")] = ArrayObject(
        [FloatObject(0), FloatObject(0), FloatObject(width), FloatObject(height)]
    )
    stream[NameObject("/Resources")] = mini_page["/Resources"].clone(writer)
    ref = writer._add_object(stream)
    obj[NameObject("/AP")] = DictionaryObject({NameObject("/N"): ref})


# dbs's "AGREEMENT AND SIGN-OFF" page has four Name/Signature/Date rows, but -- unlike
# citi -- only Name and Date are real AcroForm fields; the "Signature:" line is blank
# page space with no widget behind it at all, meant for a pen signature, same as UOB's
# used to be. There's nothing to attach a field appearance to, so each signed row gets
# its cursive name drawn straight onto the page instead, in the gap between that row's
# own Name field (above) and Date field (below) -- derived from those two fields' own
# rects rather than transcribed page coordinates, so it tracks the real layout.
DBS_SIGNOFF_FIELDS = [
    (f"primary entity authorised person name 1 appendix {i}", f"primary entity date 1 appendix {i}")
    for i in range(1, 5)
]


def _stamp_dbs_signatures(page: Any, page_annots: dict[str, Any], values: dict[str, Any], case_id: str) -> None:
    stamps = []
    for i, (name_field, date_field) in enumerate(DBS_SIGNOFF_FIELDS, start=1):
        text = values.get(name_field)
        if not text or name_field not in page_annots or date_field not in page_annots:
            continue
        name_rect = [float(x) for x in page_annots[name_field]["/Rect"]]
        date_rect = [float(x) for x in page_annots[date_field]["/Rect"]]
        x0, x1 = name_rect[0], name_rect[2]
        y0, y1 = date_rect[3] + 3, name_rect[1] - 2  # the blank gap between the two fields
        rng = seeded_rng(f"{case_id}|signature-font|{i}")
        font_name = SIGNATURE_FONTS[rng.randrange(len(SIGNATURE_FONTS))]
        stamps.append((x0, y0, x1, y1, str(text), font_name))
    if not stamps:
        return

    mb = page.mediabox
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(float(mb.width), float(mb.height)))
    for x0, y0, x1, y1, text, font_name in stamps:
        c.setFont(font_name, (y1 - y0) * 0.75)
        c.drawString(x0 + 2, y0 + (y1 - y0) * 0.25, text)
    c.save()
    buf.seek(0)
    page.merge_page(PdfReader(buf).pages[0])


def _wrap_to_width(text: str, font_size: float, max_width: float) -> list[str]:
    """Greedy word-wrap using actual Helvetica glyph widths."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    line = words[0]
    for word in words[1:]:
        candidate = f"{line} {word}"
        if stringWidth(candidate, "Helvetica", font_size) <= max_width:
            line = candidate
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return lines


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _redraw_below_caption(writer: PdfWriter, obj: Any, text: str, top_clearance: float) -> None:
    """Replace a field's auto-generated appearance with one that starts below the
    caption baked above it on the page, instead of pypdf's top-of-Rect default."""
    rect = [float(x) for x in obj["/Rect"]]
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    pad_x = 2.0
    max_lines = max(1, int((height - top_clearance) // _DBS_LINE_HEIGHT))
    lines = _wrap_to_width(text, _DBS_FONT_SIZE, width - 2 * pad_x)[:max_lines]

    first_baseline = height - top_clearance - _DBS_FONT_SIZE * 0.8
    parts = [
        "/Tx BMC", "q", "BT", f"/Helv {_DBS_FONT_SIZE} Tf", "0 g",
        f"{pad_x} {first_baseline:.2f} Td",
    ]
    for i, line in enumerate(lines):
        if i:
            parts.append(f"0 {-_DBS_LINE_HEIGHT:.2f} Td")
        parts.append(f"({_escape_pdf_text(line)}) Tj")
    parts += ["ET", "Q", "EMC"]
    content = "\n".join(parts).encode("latin-1", errors="replace")

    stream = DecodedStreamObject()
    stream.set_data(content)
    stream[NameObject("/Type")] = NameObject("/XObject")
    stream[NameObject("/Subtype")] = NameObject("/Form")
    stream[NameObject("/FormType")] = NumberObject(1)
    stream[NameObject("/BBox")] = ArrayObject(
        [FloatObject(0), FloatObject(0), FloatObject(width), FloatObject(height)]
    )
    dr_font = writer._root_object["/AcroForm"]["/DR"]["/Font"]
    stream[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/Helv"): dr_font.raw_get("/Helv")})}
    )
    ref = writer._add_object(stream)
    obj[NameObject("/AP")] = DictionaryObject({NameObject("/N"): ref})


def _qualified_field_name(annot: Any) -> str | None:
    """Fully qualified field name for a widget annotation, joining /T through the
    /Parent chain with '.' — matches the keys PdfReader.get_fields() returns. Both
    citi and dbs are flat (single-level) forms, so this just yields each field's
    own /T, but the walk handles a nested form too if one is ever added back."""
    parts = []
    node = annot
    while node is not None:
        t = node.get("/T")
        if t is not None:
            parts.append(str(t))
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    return ".".join(reversed(parts)) if parts else None


def fill_form(
    persona: dict[str, Any],
    template: str,
    blank_pdf: str | Path,
    out_pdf: str | Path,
    lock: bool = False,
    strict: bool = True,
    need_appearances: bool = False,
) -> Path:
    """Fill one template for one persona and write it to out_pdf."""
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}; expected one of {list(TEMPLATES)}")

    values = {k: val for k, val in TEMPLATES[template](persona).items() if val not in ("", None)}

    reader = PdfReader(str(blank_pdf))
    writer = PdfWriter(clone_from=reader)

    known = set(reader.get_fields() or {})
    unknown = set(values) - known
    if unknown:
        msg = f"{Path(blank_pdf).name}: unmapped field names {sorted(unknown)}"
        if strict:
            raise KeyError(msg)
        print("WARN:", msg)
        values = {k: v for k, v in values.items() if k in known}

    for page in writer.pages:
        page_annots = {}
        for annot in page.get("/Annots") or []:
            obj = annot.get_object()
            name = _qualified_field_name(obj)
            if name:
                page_annots[name] = obj
        subset = {k: v for k, v in values.items() if k in page_annots}
        if subset:
            writer.update_page_form_field_values(page, subset, auto_regenerate=False)
            # Some AcroForms ship input fields with the Hidden annotation flag set,
            # meant to be cleared by Adobe JavaScript when their triggering checkbox
            # is ticked. We fill statically, so clear Hidden (bit 2) ourselves or the
            # value we just wrote won't render.
            for name in subset:
                obj = page_annots[name]
                flags = int(obj.get("/F", 0))
                if flags & 2:
                    obj[NameObject("/F")] = NumberObject(flags & ~2)
                if template == "dbs" and name in DBS_CAPTION_CLEARANCE:
                    _redraw_below_caption(writer, obj, str(subset[name]), DBS_CAPTION_CLEARANCE[name])
                elif name == SIGNATURE_FIELDS.get(template):
                    rng = seeded_rng(f"{persona.get('case_id', '')}|signature-font")
                    font_name = SIGNATURE_FONTS[rng.randrange(len(SIGNATURE_FONTS))]
                    _render_cursive_signature(writer, obj, str(subset[name]), font_name)

        if template == "dbs":
            _stamp_dbs_signatures(page, page_annots, values, persona.get("case_id", ""))

    # NeedAppearances tells the viewer to rebuild appearance streams. Leave it OFF:
    # pypdf already writes appearances for text fields, and the templates ship their
    # own tick-mark appearances for checkboxes/radios. With the flag on, some
    # renderers (pdfium, and therefore most rasterising pipelines) discard the
    # existing tick appearance and draw an empty box instead.
    if need_appearances:
        writer.set_need_appearances_writer(True)

    if lock:
        _lock_fields(writer)

    meta = persona.get("metadata")
    if meta:
        writer.add_metadata({f"/{k}": v for k, v in meta.items()})

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with open(out_pdf, "wb") as fh:
        writer.write(fh)
    return out_pdf


def _lock_fields(writer: PdfWriter) -> None:
    """Set the ReadOnly flag on every field so the output cannot be re-edited."""
    for page in writer.pages:
        for annot in page.get("/Annots") or []:
            obj = annot.get_object()
            target = obj if "/FT" in obj else obj.get("/Parent", obj)
            target = target.get_object() if hasattr(target, "get_object") else target
            flags = int(target.get("/Ff", 0))
            target[NameObject("/Ff")] = NumberObject(flags | 1)


# --------------------------------------------------------------------------
# Personas — build one record per mastersheet case
# --------------------------------------------------------------------------

# Per-case deviations from the clean defaults. Merge-on-top of the derived record.
# e.g. CASE_OVERRIDES = {"C012": {"fatca_classification": "passive_nffe"}}
CASE_OVERRIDES: dict[str, dict[str, Any]] = {}

ROLE_TO_CP_TYPE = {
    "director & ubo": "legal_person_ownership",
    "ubo": "legal_person_ownership",
    "shareholder": "legal_person_ownership",
    "director": "legal_person_senior_managing_official",
}

def _rows(ws) -> list[dict[str, Any]]:
    header = [c.value for c in ws[1]]
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        out.append(dict(zip(header, row)))
    return out


def _split_sg_address(addr: str) -> dict[str, str]:
    """'21 Tampines Street 12, #05-34, Singapore 529456' -> street / city / country."""
    addr = (addr or "").strip()
    m = re.search(r",\s*(Singapore\s*\d{6})\s*$", addr, flags=re.I)
    if m:
        return {
            "street": addr[: m.start()].strip().rstrip(","),
            "city_state_postal": m.group(1).strip(),
            "country": "Singapore",
        }
    return {"street": addr, "city_state_postal": "", "country": "Singapore"}


def _fmt_date(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()).strftime("%d/%m/%Y")
        except ValueError:
            return value.strip()
    return ""


def build_personas(xlsx: str | Path, cases: list[str] | None = None) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(str(xlsx), data_only=True)
    registry = _rows(wb["Case Registry"])
    individuals = _rows(wb["Individuals"])

    by_case: dict[str, list[dict[str, Any]]] = {}
    for ind in individuals:
        by_case.setdefault(str(ind["Case ID"]), []).append(ind)

    personas = []
    for case in registry:
        case_id = str(case["Case ID"])
        if cases and case_id not in cases:
            continue
        if not case.get("Company Name"):
            continue  # placeholder row, not yet filled in

        people = sorted(by_case.get(case_id, []), key=lambda r: r.get("Person Seq") or 0)
        facts = company_facts(case, people)
        director = next(
            (h for h in facts["holders"] if "director" in str(h["role"]).lower()),
            facts["holders"][0] if facts["holders"] else None,
        )

        persona: dict[str, Any] = {
            "case_id": case_id,
            "entity_name": facts["company_name"],
            "country_of_incorporation": "Singapore",
            "us_tin": "",
            "registered_address": _split_sg_address(facts["registered_address"]),
            "tax_residences": [{"country": "Singapore", "tin": facts["uen"]}],
            "fatca_classification": "active_nffe",
            "beneficial_owner_status": "corporation",
            "apply_crs_all_countries": False,
            "not_tax_resident_of_incorporation_country": False,
            "controlling_persons": [
                {
                    "name": h["name"],
                    "type": ROLE_TO_CP_TYPE.get(
                        str(h["role"]).strip().lower(), "legal_person_ownership"
                    ),
                    "percentage_ownership": h["pct"],
                }
                for h in facts["holders"]
            ],
            "signatories": (
                [
                    {
                        "name": director["name"],
                        "capacity": "Director",
                        "date": _fmt_date(facts["application_date"]),
                    }
                ]
                if director
                else []
            ),
            # Carried for traceability back to the mastersheet; unused by the filler.
            "_source": {
                "uen": facts["uen"],
                "bank": case.get("Bank Name"),
                "case_label": case.get("Case Label"),
                "fraud_case": case.get("Fraud Case? (Y/N)"),
            },
        }
        persona.update(CASE_OVERRIDES.get(case_id, {}))
        personas.append(persona)
    return personas


# --------------------------------------------------------------------------
# CLI — mastersheet in, filled self-certs out
# --------------------------------------------------------------------------

def pick_template(case_id: str) -> str:
    templates = sorted(TEMPLATES)  # fixed order so the seeded pick is reproducible
    rng = seeded_rng(f"{case_id}|selfcert-template")
    return templates[rng.randrange(len(templates))]


def generate(xlsx: str | Path, outdir: str | Path, cases: list[str] | None = None,
             strict: bool = True) -> list[Path]:
    """Fill one self-cert per case and write a manifest CSV alongside them."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    personas = build_personas(xlsx, cases)

    written: list[Path] = []
    manifest = []
    for p in personas:
        case_id = p["case_id"]
        template = pick_template(case_id)
        # Resolve relative to this file, not the caller's cwd, so this works
        # whether invoked via the CLI below or imported from elsewhere.
        blank_pdf = Path(__file__).with_name(BLANK_PDFS[template])
        out = outdir / f"{case_id}_{template}_selfcert.pdf"
        fill_form(p, template, blank_pdf, out, strict=strict)
        written.append(out)
        manifest.append({"case_id": case_id, "entity_name": p["entity_name"], "template": template, "file": out.name})
        print(f"{case_id}: {template} -> {out}")

    manifest_path = outdir / "selfcert_template_manifest.csv"
    with open(manifest_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["case_id", "entity_name", "template", "file"])
        writer.writeheader()
        writer.writerows(manifest)
    print(f"wrote {len(written)} self-cert(s); manifest -> {manifest_path}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx")
    ap.add_argument("--outdir", default="filled")
    ap.add_argument("--cases", nargs="*", help="case IDs to include; default is all")
    ap.add_argument("--no-strict", action="store_true", help="warn instead of failing on unmapped fields")
    args = ap.parse_args()
    generate(args.xlsx, args.outdir, args.cases, strict=not args.no_strict)


if __name__ == "__main__":
    main()
