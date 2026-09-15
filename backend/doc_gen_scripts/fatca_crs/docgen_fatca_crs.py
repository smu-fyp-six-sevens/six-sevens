"""
docgen_fatca_crs.py — for every case in the mastersheet, fill GLDB's FATCA/CRS entity
self-certification template.

Single-file merge of what used to be three scripts (personas_from_mastersheet.py,
fill_selfcert.py, and the original thin docgen_fatca_crs.py driver), kept together
because none of the three pieces are used anywhere outside this pipeline. case_facts.py
stays a separate companion file instead, because docgen_constitution.py, docgen_
incumbency.py and docgen_acra_profile.py all import it too -- forking it in here would
fork their logic as well.

Project scope narrowed to GLDB (Green Link Digital Bank) only, so the earlier citi/dbs
dual-template picker is gone -- see git history if the other two templates are ever
worth reviving. GLDB's is an AcroForm PDF, so filling is a field-name lookup rather
than coordinate drawing. Its checkboxes are all named generically ("Check Box34" etc
rather than something self-describing), so GLDB_ENTITY_TYPE_BOX / TAX_REASON_BOX /
CP_TYPE_BOX below were built by rendering each page with every widget's rect and field
name overlaid and reading off which checkbox sits next to which printed label --
re-derive them the same way if GLDB ever reissues the form with fields reordered.

Company-level identity (name, UEN, registered address, application date) and the
controlling-person roster come from case_facts.company_facts(), so a self-cert always
agrees with every other document generated for a case, and a mastersheet edit is
picked up automatically.

Assumptions baked in for clean cases (override per case in CASE_OVERRIDES):
  - Singapore-incorporated Pte Ltd, tax resident in Singapore only, not a branch
  - Singapore TIN = the company UEN
  - FATCA classification = Active NFFE (Active Business, GLDB box G)
  - Not a Specified U.S. Person
  - Signatory = the Director & UBO from the Individuals tab
  - Signing date = the loan application date from Case Registry
  - GLDB's two "Signature of Authorised Signatory" boxes are real /Sig widgets, not
    plain text fields, so a signatory's name is instead stamped straight onto the page
    inside that widget's /Rect, in a bundled cursive font instead of typed plainly --
    picked deterministically per case from SIGNATURE_FONTS -- so a batch of self-certs
    doesn't all show identical machine-typed "signatures".

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
from pypdf.generic import NameObject, NumberObject
from reportlab.pdfgen import canvas as pdfcanvas

from case_facts import company_facts
from synthetic_signature import pick_signature_font

GLDB_BLANK_PDF = Path(__file__).resolve().parent / "GLDB FATCA and CRS self-certification for entity_5 Nov 25.pdf"


# --------------------------------------------------------------------------
# Vocabulary the persona speaks -- fatca_classification and controlling-person
# "type" values, translated to GLDB field names below.
# --------------------------------------------------------------------------

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

# Section 2 "Type of Entity" lettered checkboxes (A-M).
GLDB_ENTITY_TYPE_BOX = {
    "participating_ffi": "Check Box16",          # A
    "nonparticipating_ffi": "Check Box17",        # B
    "active_nffe": "Check Box27",                 # G - Active business
    "start_up": "Check Box28",                    # H
    "public_sector": "Check Box29",                # I
    "international_org": "Check Box30",            # J
    "publicly_traded_nffe": "Check Box31",         # K
    "publicly_traded_affiliate": "Check Box31",    # K
    "nonprofit": "Check Box32",                     # L - catch-all
    "treasury_centre": "Check Box32",               # L
    "holding_company": "Check Box32",               # L
    "liquidating": "Check Box32",                   # L
    "passive_nffe": "Check Box33",                  # M
}

# Section 1 tax-residence table, row -> {reason letter -> checkbox}. GLDB labels the
# middle option "B+" on the page but it's the same Reason B code everywhere else.
TAX_REASON_BOX = {
    1: {"A": "Check Box7", "B": "Check Box8", "C": "Check Box9"},
    2: {"A": "Check Box10", "B": "Check Box11", "C": "Check Box12"},
    3: {"A": "Check Box13", "B": "Check Box14", "C": "Check Box15"},
}

# Section 3 "Type of Controlling Person" is only applicable -- and only asked for on
# the GLDB form -- when Section 2's entity type is "D" (Passive Investment Entity
# managed by another FI; not currently modelled as a FATCA_CLASSES value) or "M"
# (Passive NFFE). Every other Section 2 selection (e.g. "G" Active NFFE) must leave
# Section 3 blank.
SECTION_3_REQUIRED_CLASSES = {"passive_nffe"}  # M; add e.g. a "D" classification here if it's ever modelled

# Section 3 "Type of Controlling Person", one checkbox set per CP row (up to 3).
CP_NAME_FIELD = {1: "Text47", 2: "Text48", 3: "Text75"}
CP_TYPE_BOX = {
    1: {
        "legal_person_ownership": "Check Box34",
        "settlor_equivalent": "Check Box35",
        "legal_person_senior_managing_official": "Check Box36",
        "legal_person_other_means": "Check Box37",
        "trust_settlor": "Check Box38",
        "trust_trustee": "Check Box39",
        "trust_protector": "Check Box40",
        "trust_beneficiary": "Check Box41",
        "trust_other": "Check Box42",
        "trustee_equivalent": "Check Box43",
        "protector_equivalent": "Check Box44",
        "beneficiary_equivalent": "Check Box45",
        "other_equivalent": "Check Box46",
    },
    2: {
        "legal_person_ownership": "Check Box49",
        "legal_person_senior_managing_official": "Check Box50",
        "legal_person_other_means": "Check Box51",
        "trust_settlor": "Check Box52",
        "trust_trustee": "Check Box53",
        "trust_protector": "Check Box54",
        "trust_beneficiary": "Check Box55",
        "trust_other": "Check Box56",
        "settlor_equivalent": "Check Box57",
        "trustee_equivalent": "Check Box58",
        "protector_equivalent": "Check Box59",
        "beneficiary_equivalent": "Check Box60",
        "other_equivalent": "Check Box61",
    },
    3: {
        "legal_person_ownership": "Check Box62",
        "legal_person_senior_managing_official": "Check Box63",
        "legal_person_other_means": "Check Box64",
        "trust_settlor": "Check Box65",
        "trust_trustee": "Check Box66",
        "trust_protector": "Check Box67",
        "trust_beneficiary": "Check Box68",
        "trust_other": "Check Box69",
        "settlor_equivalent": "Check Box70",
        "trustee_equivalent": "Check Box71",
        "protector_equivalent": "Check Box72",
        "beneficiary_equivalent": "Check Box73",
        "other_equivalent": "Check Box74",
    },
}

# The two "Signature of Authorised Signatory" boxes are /Sig widgets, not /Tx fields,
# so there's no AcroForm value to set -- a name is stamped onto the page instead (see
# _stamp_signatures below), one field per signatory in persona["signatories"][:2].
# Font registration/pick lives in synthetic_signature.py, shared with every other
# generator that stamps a cursive-name signature (COI, loan letters, constitution,
# source-of-wealth statements, passport).
SIGNATURE_SIG_FIELDS = ["Signature76", "Signature77"]


def _split_giin(giin: str) -> tuple[str, str, str]:
    """GIIN is nominally FIID.CATEGORY.COUNTRY.FILERCAT (6.5.2.3 chars), but the GLDB
    form only offers three boxes -- pack the first two groups into the first box."""
    parts = [x for x in re.split(r"[.\s-]+", giin.strip()) if x]
    if len(parts) >= 4:
        return f"{parts[0]}.{parts[1]}", parts[2], parts[3]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return giin.strip(), "", ""


def _gldb_values(p: dict[str, Any]) -> dict[str, Any]:
    v: dict[str, Any] = {}
    addr = p.get("registered_address", {})

    v["Full Legal Name of Entity Branch"] = p.get("entity_name", "")
    v["Country of Incorporation"] = p.get("country_of_incorporation", "")
    v["Block"] = addr.get("block", "")
    v["Street"] = addr.get("street", "")
    v["Floor"] = addr.get("floor", "")
    v["Unit"] = addr.get("unit", "")
    v["City  State"] = addr.get("city", "")
    v["Postal Code"] = addr.get("postal_code", "")
    v["Country"] = addr.get("country", "")

    mail = p.get("mailing_address") or {}
    if mail:
        v["Check Box1"] = "/Yes"
        v["Street_4"] = mail.get("street", "")
        v["City  State_2"] = mail.get("city", "")
        v["Postal Code_2"] = mail.get("postal_code", "")
        v["Country_2"] = mail.get("country", "")
        if p.get("mailing_country_mismatch_explanation"):
            v[
                "If the country of the mailing address does not match the country "
                "of the registered address please explainRow1"
            ] = p["mailing_country_mismatch_explanation"]

    if p.get("is_branch"):
        v["Check Box3"] = "/Yes"
        if p.get("branch_headquarters_jurisdiction"):
            v["No YesIf yes in which jurisdiction is the headquarters located"] = (
                p["branch_headquarters_jurisdiction"]
            )
    else:
        v["Check Box2"] = "/Yes"

    if p.get("us_tin"):
        v["Check Box5"] = "/Yes"
        v["Text6"] = p["us_tin"]
    else:
        v["Check Box4"] = "/Yes"
        for i, res in enumerate(p.get("tax_residences", [])[:3], start=1):
            v[f"Country Jurisdiction of Tax Residence{i}"] = res.get("country", "")
            v[f"Tax Identification Number TIN{i}"] = res.get("tin", "")
            reason = (res.get("no_tin_reason") or "").upper()
            box = TAX_REASON_BOX.get(i, {}).get(reason)
            if box:
                v[box] = "/Yes"
            if res.get("reason_b_explanation"):
                v[f"Explanation for Reason BCountry {i} indicated above because"] = (
                    res["reason_b_explanation"]
                )

    cls = p.get("fatca_classification", "")
    box = GLDB_ENTITY_TYPE_BOX.get(cls)
    if box:
        v[box] = "/Yes"
    if cls == "participating_ffi" and p.get("giin"):
        b1, b2, b3 = _split_giin(p["giin"])
        v["Text21"], v["Text22"], v["Text23"] = b1, b2, b3

    if cls in SECTION_3_REQUIRED_CLASSES:
        for i, cp in enumerate(p.get("controlling_persons", [])[:3], start=1):
            v[CP_NAME_FIELD[i]] = cp.get("name", "")
            cbox = CP_TYPE_BOX[i].get(cp.get("type", ""))
            if cbox:
                v[cbox] = "/Yes"

    for i, s in enumerate(p.get("signatories", [])[:2]):
        suffix = "" if i == 0 else "_2"
        v[f"Name{suffix}"] = s.get("name", "")
        v[f"Designation{suffix}"] = s.get("capacity", "")
        v[f"Date DDMMYY{suffix}"] = s.get("date", "")
    return v


# --------------------------------------------------------------------------
# Filling
# --------------------------------------------------------------------------

def _stamp_signatures(page: Any, page_annots: dict[str, Any], persona: dict[str, Any], case_id: str) -> None:
    stamps = []
    for i, s in enumerate(persona.get("signatories", [])[:2]):
        field = SIGNATURE_SIG_FIELDS[i]
        text = s.get("signature_text", s.get("name", ""))
        if not text or field not in page_annots:
            continue
        rect = [float(x) for x in page_annots[field]["/Rect"]]
        font_name = pick_signature_font(f"{case_id}|signature-font|{i}")
        stamps.append((rect, str(text), font_name))
    if not stamps:
        return

    mb = page.mediabox
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(float(mb.width), float(mb.height)))
    for (x0, y0, x1, y1), text, font_name in stamps:
        height = y1 - y0
        c.setFont(font_name, height * 0.45)
        c.drawString(x0 + 4, y0 + height * 0.12, text)
    c.save()
    buf.seek(0)
    page.merge_page(PdfReader(buf).pages[0])


def _qualified_field_name(annot: Any) -> str | None:
    """Fully qualified field name for a widget annotation, joining /T through the
    /Parent chain with '.' — matches the keys PdfReader.get_fields() returns. GLDB's
    form is flat (single-level), so this just yields each field's own /T, but the walk
    handles a nested form too if one is ever added."""
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
    blank_pdf: str | Path = GLDB_BLANK_PDF,
    out_pdf: str | Path = "out.pdf",
    lock: bool = False,
    strict: bool = True,
    need_appearances: bool = False,
) -> Path:
    """Fill the GLDB template for one persona and write it to out_pdf."""
    values = {k: val for k, val in _gldb_values(persona).items() if val not in ("", None)}

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

        _stamp_signatures(page, page_annots, persona, persona.get("case_id", ""))

    # NeedAppearances tells the viewer to rebuild appearance streams. Leave it OFF:
    # pypdf already writes appearances for text fields, and the template ships its
    # own tick-mark appearances for checkboxes. With the flag on, some renderers
    # (pdfium, and therefore most rasterising pipelines) discard the existing tick
    # appearance and draw an empty box instead.
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


def _split_registered_address(addr: str) -> dict[str, str]:
    """'1 Raffles Place, #32-01, Singapore 048616' -> block / street / floor / unit /
    city / postal_code / country. The form has separate Block/Street/Floor/Unit
    boxes (not one combined address line), so this pulls all four out instead of
    dumping the whole line into Street."""
    addr = (addr or "").strip()
    city = ""
    postal_code = ""
    postal_m = re.search(r",\s*Singapore\s*(\d{6})\s*$", addr, flags=re.I)
    if postal_m:
        city = "Singapore"
        postal_code = postal_m.group(1)
        addr = addr[: postal_m.start()].strip().rstrip(",")

    floor, unit = "", ""
    unit_m = re.search(r"#\s*(\w+)\s*-\s*(\w+)", addr)
    if unit_m:
        floor, unit = unit_m.group(1), unit_m.group(2)
        addr = addr[: unit_m.start()] + addr[unit_m.end():]
        addr = re.sub(r",\s*,", ",", addr).strip(", ").strip()

    block, street = "", addr
    block_m = re.match(r"(\S+)\s+(.*)", addr)
    if block_m:
        block, street = block_m.group(1), block_m.group(2).strip(", ").strip()

    return {
        "block": block,
        "street": street,
        "floor": floor,
        "unit": unit,
        "city": city,
        "postal_code": postal_code,
        "country": "Singapore",
    }


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
            "is_branch": False,
            "us_tin": "",
            "registered_address": _split_registered_address(facts["registered_address"]),
            "tax_residences": [{"country": "Singapore", "tin": facts["uen"]}],
            "fatca_classification": "active_nffe",
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

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx")
    ap.add_argument("--outdir", default="filled")
    ap.add_argument("--cases", nargs="*", help="case IDs to include; default is all")
    ap.add_argument("--no-strict", action="store_true", help="warn instead of failing on unmapped fields")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    personas = build_personas(args.xlsx, args.cases)

    manifest = []
    for p in personas:
        case_id = p["case_id"]
        out = outdir / f"{case_id}_selfcert.pdf"
        fill_form(p, GLDB_BLANK_PDF, out, strict=not args.no_strict)
        manifest.append({"case_id": case_id, "entity_name": p["entity_name"], "file": out.name})
        print(f"{case_id}: -> {out}")

    manifest_path = outdir / "selfcert_manifest.csv"
    with open(manifest_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["case_id", "entity_name", "file"])
        writer.writeheader()
        writer.writerows(manifest)
    print(f"wrote {len(manifest)} self-cert(s); manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
