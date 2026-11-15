"""
The forgeries the success-metric dataset carries, and the code that applies them.

The generators only write clean documents; every fraud the mastersheet lists was added afterwards, by hand,
through the injection tool. This file is those same frauds written down as steps, so a dataset can be rebuilt
from scratch for any seed. Each step goes through the app's own injectors (app/injection), and what it changed
(the field, the original value, the forged value) is written into the document's truth file, where the scorer
reads it (scripts/metrics/score.py).

A step names its target by what is printed, taken from the truth file ("the field showing this document's net
pay"), never by a fixed figure, because a seeded run prints different figures.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from app.injection import store
from app.injection.injectors import get_injector
from app.injection.schemas import DefectType, InjectableDocument, InjectOverrides

from doc_gen_scripts.truth import TRUTH_SUFFIX, truth_path

# Mastersheet "Detection Dimension" -> the detection checks expected to raise it. A cross-document forgery is
# found by the case analysis, not by a document's own scan.
DIMENSION_TOOLS: dict[str, tuple[str, ...]] = {
    "Spelling": ("spelling_tool",),
    "Font/Size": ("font_consistency_tool",),
    "Misalignment": ("layout_anomalies_tool",),
    "Metadata": ("metadata_tool",),
    "Date Anomalies": ("date_consistency_tool", "numerical_checks_tool", "external_validation_tool"),
    "Numerical": ("numerical_checks_tool",),
    "Image Manipulation": ("image_manipulation_tool", "image_tampering_tool"),
    "Text Overlay": ("layout_anomalies_tool", "hidden_text_tool"),
    "AI-Generated": ("ai_generation_tool",),
    "Cross-Document": ("cross_document_analysis", "address_consistency_tool", "company_name_consistency_tool"),
}

Truth = dict[str, Any]
PHOTOSHOP = {"creator": "Adobe Photoshop 25.0", "producer": "Adobe Photoshop 25.0"}


@dataclass
class Step:
    """One edit to one document."""

    defect: str  # an app.injection DefectType value
    dimension: str  # the mastersheet's Detection Dimension for it
    # The text of the field to change, worked out from the truth file. None: any field (metadata needs none).
    find: Callable[[Truth], str | list[str]] | None = None
    # "first", "all" (every field showing the text), or "image:top" (the image nearest the top of page 1).
    pick: str = "first"
    # (the field's text, truth) -> the text to print instead.
    new_text: Callable[[str, Truth], str] | None = None
    font: str | None = None
    font_size_delta: float | None = None
    dx: float | None = None
    dy: float | None = None
    metadata: dict[str, str] | None = None
    indicator: str | None = None
    scale_x: float | None = None
    scale_y: float | None = None
    # The extraction field the edit changes, when it changes one: (field name, truth -> the forged value).
    changes: tuple[str, Callable[[Truth], Any]] | None = None
    # An overlay leaves the original underneath, so an extractor may read either value.
    keeps_original: bool = False
    # For a change inside a table: rewrites the truth in place and returns (what changed, original, forged).
    amend: Callable[[Truth], tuple[str, Any, Any]] | None = None
    note: str = ""


@dataclass
class Fraud:
    case_id: str
    filename: str  # as the mastersheet names it; an NRIC image is "<stem>_front.<ext>" on disk
    steps: list[Step] = field(default_factory=list)
    # The file the steps are applied to, when it isn't `filename` itself.
    target: str | None = None


# --------------------------------------------------------------------------
# Reading values out of a truth file, as they are printed
# --------------------------------------------------------------------------

def _value(truth: Truth, name: str) -> Any:
    for section in ("fields", "not_printed", "reference"):
        if name in (truth.get(section) or {}):
            return truth[section][name]
    raise KeyError(f"{truth.get('filename')}: no '{name}' in its truth file")


def money(name: str, decimals: int = 2) -> Callable[[Truth], str]:
    return lambda truth: f"{float(_value(truth, name)):,.{decimals}f}"


def text(name: str, upper: bool = False) -> Callable[[Truth], str]:
    return lambda truth: str(_value(truth, name)).upper() if upper else str(_value(truth, name))


def dates(name: str) -> Callable[[Truth], list[str]]:
    """Every way the date may be printed; the first one found on the page is the one changed."""
    def shapes(truth: Truth) -> list[str]:
        day = date.fromisoformat(str(_value(truth, name)))
        return [day.strftime(f) for f in ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d")] + [
            f"{day.day} {day:%B %Y}", f"{day.day} {day:%b %Y}",
        ]
    return shapes


def unit(name: str) -> Callable[[Truth], str]:
    def find(truth: Truth) -> str:
        match = re.search(r"#\s*\w+\s*-\s*\w+", str(_value(truth, name)))
        if not match:
            raise ValueError(f"{truth.get('filename')}: no unit number in {name}")
        return match.group(0)
    return find


def _swap_last_two(digits: str) -> str:
    return digits[:-2] + digits[-1] + digits[-2] if len(digits) >= 2 and digits[-1] != digits[-2] else digits[::-1]


def swap_unit(current: str, _: Truth) -> str:
    """#07-136 -> #07-163: the last two digits of the unit swapped."""
    return re.sub(r"(#\s*\w+\s*-\s*)(\w+)", lambda m: m.group(1) + _swap_last_two(m.group(2)), current, count=1)


def add(amount: float) -> Callable[[str, Truth], str]:
    """The first figure in the field, raised by `amount`, in the same number format."""
    def change(current: str, _: Truth) -> str:
        match = re.search(r"\d[\d,]*(?:\.\d+)?", current)
        if not match:
            raise ValueError(f"no figure in {current!r}")
        old = match.group(0)
        decimals = len(old.split(".")[1]) if "." in old else 0
        new = f"{float(old.replace(',', '')) + amount:,.{decimals}f}" if "," in old else (
            f"{float(old.replace(',', '')) + amount:.{decimals}f}")
        return current[: match.start()] + new + current[match.end():]
    return change


def year(change: Callable[[int], int]) -> Callable[[str, Truth], str]:
    def apply(current: str, _: Truth) -> str:
        matches = list(re.finditer(r"(?:19|20)\d{2}", current))
        if not matches:
            raise ValueError(f"no year in {current!r}")
        last = matches[-1]
        return current[: last.start()] + str(change(int(last.group(0)))) + current[last.end():]
    return apply


def replace(old: str, new: str) -> Callable[[str, Truth], str]:
    def apply(current: str, _: Truth) -> str:
        if old not in current:
            raise ValueError(f"{old!r} not in {current!r}")
        return current.replace(old, new, 1)
    return apply


def _shifted(truth: Truth, name: str, amount: float) -> float:
    return round(float(_value(truth, name)) + amount, 2)


def _moved_year(truth: Truth, name: str, change: Callable[[int], int]) -> str:
    day = date.fromisoformat(str(_value(truth, name)))
    return day.replace(year=change(day.year)).isoformat()


def _balance_row(truth: Truth) -> dict[str, Any]:
    """A transaction a third of the way down the statement, where a changed balance breaks the rows after it."""
    rows = _value(truth, "transactions")
    return rows[len(rows) // 3]


def _raise_balance(amount: float) -> Callable[[Truth], tuple[str, Any, Any]]:
    def amend(truth: Truth) -> tuple[str, Any, Any]:
        rows = _value(truth, "transactions")
        index = len(rows) // 3
        original = rows[index]["balance"]
        rows[index]["balance"] = round(original + amount, 2)
        return f"transactions[{index}].balance", original, rows[index]["balance"]
    return amend


def _digits_swapped(value: int) -> int:
    return int(str(value)[:2] + str(value)[2:][::-1])


# --------------------------------------------------------------------------
# The frauds. Twenty from the mastersheet, three single cosmetic edits added for the Medium risk level.
# --------------------------------------------------------------------------

FRAUDS: list[Fraud] = [
    # --- C002 ---------------------------------------------------------------------------------------------
    Fraud("C002", "KFF_COI_scan.pdf", [
        Step("metadata", "Metadata", metadata=PHOTOSHOP,
             note="Creator/Producer set to Adobe Photoshop, inconsistent with an ACRA-issued certificate."),
        Step("numerical", "Date Anomalies", find=dates("incorporation_date"),
             new_text=year(lambda y: y - 10),
             changes=("incorporation_date", lambda t: _moved_year(t, "incorporation_date", lambda y: y - 10)),
             note="Incorporation date moved ten years back, so it predates the year in the UEN."),
    ]),
    Fraud("C002", "NRIC_3844.jpg", target="NRIC_3844_front.jpg", steps=[
        Step("image_manipulation", "Image Manipulation", pick="image:top", indicator="scaling_anomaly",
             scale_x=1.6, scale_y=0.55, note="State crest on the NRIC front stretched to 1.6x wide, 0.55x tall."),
    ]),
    Fraud("C002", "KestrelFreightForwarding_NOA_YA2025.pdf", [
        Step("image_manipulation", "Image Manipulation", pick="image:top", indicator="compression_mismatch",
             note="The IRAS logo recompressed at a different quality from the page."),
        Step("metadata", "Metadata", metadata=PHOTOSHOP,
             note="Creator/Producer set to Adobe Photoshop, inconsistent with an IRAS-issued notice."),
    ]),
    # --- C003 ---------------------------------------------------------------------------------------------
    Fraud("C003", "LIS_Constitution.pdf", [
        Step("font", "Font/Size", find=text("company_name", upper=True), font="tiro",
             note="Cover-page company name changed from Helvetica-Bold to Times Roman."),
    ]),
    Fraud("C003", "LIS_BalanceSheet_Audited.pdf", [
        Step("numerical", "Numerical", find=money("total_assets", 0), new_text=add(50_000),
             changes=("total_assets", lambda t: _shifted(t, "total_assets", 50_000)),
             note="Total assets raised by 50,000, so it no longer equals total equity and liabilities."),
    ]),
    # --- C008 ---------------------------------------------------------------------------------------------
    Fraud("C008", "ACRA_CertIncorp_201651600K.pdf", [
        Step("numerical", "Date Anomalies", find=dates("incorporation_date"),
             new_text=year(_digits_swapped),
             changes=("incorporation_date", lambda t: _moved_year(t, "incorporation_date", _digits_swapped)),
             note="Incorporation year's last two digits swapped to a future date, inconsistent with the UEN."),
    ]),
    Fraud("C008", "CreditReport_CBS_20260808.pdf", [
        Step("misalignment", "Misalignment", find=money("secured_credit_limit", 0), dx=26, dy=2,
             note="Secured Credit Limit value shifted 26pt right and 2pt down, out of line with its column."),
        Step("metadata", "Metadata",
             metadata={"author": "Experian Credit Services", "creator": "Microsoft Word for Microsoft 365"},
             note="Author/Creator inconsistent with the issuer, Credit Bureau (Singapore)."),
    ]),
    # --- C011 ---------------------------------------------------------------------------------------------
    Fraud("C011", "SimTH_Passport_scan.jpg", [
        Step("metadata", "Metadata", metadata={"creator": "Adobe Photoshop 25.0"},
             note="Image EXIF Software set to Adobe Photoshop, inconsistent with a scanned passport."),
    ]),
    Fraud("C011", "TayYS_Salary_Slip_20260925.pdf", [
        Step("text_overlay", "Text Overlay", find=money("net_pay"), new_text=add(5_000),
             changes=("net_pay", lambda t: _shifted(t, "net_pay", 5_000)), keeps_original=True,
             note="Net pay raised by 5,000, drawn over the original figure."),
    ]),
    Fraud("C011", "SP_Utilities_TMS.pdf", [
        Step("spelling", "Cross-Document", find=unit("company_address"), pick="all", new_text=swap_unit,
             changes=("company_address", lambda t: swap_unit(str(_value(t, "company_address")), t)),
             note="Unit number's last two digits swapped in both addresses, so the bill no longer matches the "
                  "company's registered address."),
    ]),
    # C011's AI-generated proof of address is built whole by scripts/metrics/ai_document.py, not edited here.
    # --- C015 ---------------------------------------------------------------------------------------------
    Fraud("C015", "eStmt_download_630.pdf", [
        Step("numerical", "Numerical", find=lambda t: f"{_balance_row(t)['balance']:,.2f}", new_text=add(1_000),
             amend=_raise_balance(1_000),
             note="One row's ledger balance raised by 1,000, so the running balance no longer reconciles."),
        # By now the truth row holds the forged balance, which is what the page shows.
        Step("font_size", "Font/Size", find=lambda t: f"{_balance_row(t)['balance']:,.2f}",
             font_size_delta=-1.0, note="The altered figure set 1pt smaller than the surrounding rows."),
    ]),
    Fraud("C015", "POR_UtilityBill_20260904.png", [
        Step("spelling", "Cross-Document", find=unit("individual_address"), pick="all", new_text=swap_unit,
             changes=("individual_address", lambda t: swap_unit(str(_value(t, "individual_address")), t)),
             note="Unit number's last two digits swapped in both address blocks, so the bill no longer matches "
                  "the holder's address on record."),
    ]),
    Fraud("C015", "Rajesh_Salary_Slip_20260904.pdf", [
        Step("misalignment", "Misalignment", find=money("net_pay"), dx=14, dy=3,
             note="Net pay figure shifted 14pt right and 3pt down, so it overruns its box."),
    ]),
    # --- C018 ---------------------------------------------------------------------------------------------
    Fraud("C018", "eStmt_download_233.pdf", [
        Step("metadata", "Metadata", metadata={**PHOTOSHOP, "creationDate": "D:20260415101500+08'00'"},
             note="Creator/Producer set to Adobe Photoshop, and a creation date before the statement period."),
    ]),
    Fraud("C018", "FATCA_CRS_Entity_95509.pdf", [
        Step("spelling", "Cross-Document", find=lambda t: unit("company_address")(t).split("-")[-1].strip(),
             new_text=lambda current, t: _swap_last_two(current.strip()),
             changes=("company_address", lambda t: swap_unit(str(_value(t, "company_address")), t)),
             note="Registered address unit's last two digits swapped, so the form no longer matches the "
                  "company's address on the case's other documents."),
    ]),
    # --- C020 / C023 --------------------------------------------------------------------------------------
    Fraud("C020", "LoanFacility_17299_1.pdf", [
        Step("text_overlay", "Text Overlay", find=money("loan_amount"), new_text=add(100_000),
             changes=("loan_amount", lambda t: _shifted(t, "loan_amount", 100_000)), keeps_original=True,
             note="Facility amount raised by 100,000, drawn over the original figure."),
    ]),
    Fraud("C023", "ACRA_Incumbency_20260808.pdf", [
        Step("text_overlay", "Text Overlay", find=money("share_capital"), new_text=add(300_000),
             changes=("share_capital", lambda t: _shifted(t, "share_capital", 300_000)), keeps_original=True,
             note="Issued share capital raised by 300,000, drawn over the original; the number of shares is "
                  "unchanged."),
    ]),
    # --- C030 ---------------------------------------------------------------------------------------------
    Fraud("C030", "ACRA_CertIncorp_201078782M.pdf", [
        Step("spelling", "Spelling", find=lambda t: "SEAFOOD", new_text=replace("SEAFOOD", "SEAFOD"),
             changes=("company_name", lambda t: re.sub("seafood", "Seafod", str(_value(t, "company_name")), flags=re.I)),
             note="Company name misspelt: SEAFOOD -> SEAFOD."),
        Step("font_size", "Font/Size", find=lambda t: "SEAFOD", font_size_delta=2.0,
             note="The re-typed name set 2pt larger than the original."),
    ]),
    Fraud("C030", "NOA_YA2025_201078782M.pdf", [
        Step("numerical", "Date Anomalies", find=lambda t: f"Year of Assessment {_value(t, 'year_of_assessment')}",
             new_text=lambda current, t: current.replace(str(_value(t, "year_of_assessment")),
                                                         str(_digits_swapped(int(_value(t, "year_of_assessment")))), 1),
             changes=("year_of_assessment", lambda t: str(_digits_swapped(int(_value(t, "year_of_assessment"))))),
             note="Year of Assessment's last two digits swapped in the header; the assessment dates are unchanged."),
    ]),
    # --- Medium: one cosmetic edit, nothing material changed ---------------------------------------------
    Fraud("C024", "ACRA_CertIncorp_201065233R.pdf", [
        Step("font", "Font/Size", find=lambda t: "ASST REGISTRAR OF COMPANIES", font="cour",
             note="Registrar's title under the signature set in Courier, unlike the lines around it. No company "
                  "detail is changed."),
    ]),
    Fraud("C025", "ACRA_CertIncorp_201013138R.pdf", [
        Step("spelling", "Spelling", find=lambda t: "REGULATORY", new_text=replace("REGULATORY", "REGULATARY"),
             note="Issuer line misspelt: REGULATORY -> REGULATARY. No company detail is changed."),
    ]),
    Fraud("C028", "FS_VPIS_2025.pdf", [
        Step("metadata", "Metadata", metadata={"producer": "iLovePDF", "creator": "Microsoft Word"},
             note="Producer/Creator show the statements were re-saved through a PDF editing tool."),
    ]),
]


def frauds_for(case_id: str) -> list[Fraud]:
    return [f for f in FRAUDS if f.case_id == case_id]


# --------------------------------------------------------------------------
# Applying them
# --------------------------------------------------------------------------

def _alnum(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _load(path: Path) -> InjectableDocument:
    document = store._build_document(path.stem, document_type="custom", filename=path.name, source=path)
    document.inject_path = str(path)
    return document


def _targets(document: InjectableDocument, step: Step, truth: Truth) -> list[str]:
    if step.pick == "image:top":
        images = [(f.page, f.y, name) for name, f in document.fields.items() if f.kind == "image"]
        if not images:
            raise ValueError("no image on the document")
        return [min(images)[2]]
    if step.find is None:
        return [next(iter(document.fields))]
    wanted = step.find(truth)
    for needle in ([wanted] if isinstance(wanted, str) else wanted):
        key = _alnum(needle)
        hits = [name for name, f in document.fields.items() if f.kind == "text" and key and key in _alnum(f.text)]
        if hits:
            return hits if step.pick == "all" else hits[:1]
    raise ValueError(f"nothing on the page shows {wanted!r}")


def _apply_step(path: Path, step: Step, truth: Truth) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    count = len(_targets(_load(path), step, truth))
    for index in range(count):
        document = _load(path)  # the file changed under the last edit, so its fields are found again
        try:
            names = _targets(document, step, truth)
        except ValueError:
            break  # a replacement removes the text it was found by; none left means every one is done
        # After a replacement the next match is first again; an edit that keeps the text moves down the list.
        name = names[0] if step.new_text and step.defect != "text_overlay" else names[min(index, len(names) - 1)]
        target = document.fields[name]
        before = target.text  # the injector rewrites the field it is given
        overrides = InjectOverrides(
            new_text=step.new_text(target.text, truth) if step.new_text else None,
            font=step.font,
            font_size=round(target.font_size + step.font_size_delta, 1) if step.font_size_delta is not None else None,
            x=target.x + step.dx if step.dx is not None else None,
            y=target.y + step.dy if step.dy is not None else None,
            metadata=step.metadata,
            indicator=step.indicator,
            scale_x=step.scale_x,
            scale_y=step.scale_y,
        )
        defect = get_injector(DefectType(step.defect)).inject(document, name, overrides=overrides)
        applied.append({
            "printed_before": before, "printed_after": overrides.new_text, "injector": defect.description,
        })
    return applied


def apply_fraud(case_dir: Path, fraud: Fraud) -> list[str]:
    """Applies one document's steps and records them in its truth file. Returns what went wrong, if anything."""
    path = case_dir / (fraud.target or fraud.filename)
    record = truth_path(path)
    if not path.exists() or not record.exists():
        return [f"{fraud.case_id} {path.name}: not generated, so nothing was injected"]
    truth: Truth = json.loads(record.read_text())
    problems: list[str] = []
    for step in fraud.steps:
        try:
            applied = _apply_step(path, step, truth)
        except Exception as exc:
            problems.append(f"{fraud.case_id} {path.name}: {step.dimension} not injected ({exc})")
            continue
        entry: dict[str, Any] = {
            "dimension": step.dimension,
            "defect_type": step.defect,
            "expected_tools": list(DIMENSION_TOOLS[step.dimension]),
            "description": step.note,
            "edits": applied,
        }
        if step.changes:
            name, forged = step.changes
            original = _value(truth, name)
            entry.update(field=name, original=original, forged=forged(truth))
            if name in truth["fields"]:
                truth.setdefault("accept_also", {})
                if step.keeps_original:
                    truth["accept_also"].setdefault(name, []).append(original)
                # The extractor reads what is printed, so the forged value is the right answer now.
                truth["fields"][name] = entry["forged"]
        if step.amend:
            label, original, forged_value = step.amend(truth)
            entry.update(field=label, original=original, forged=forged_value)
        truth["tampering"].append(entry)
        truth["fraud_expected"] = True
    record.write_text(json.dumps(truth, indent=2, ensure_ascii=False) + "\n")
    return problems


def apply_case(case_dir: Path, case_id: str) -> list[str]:
    problems: list[str] = []
    for fraud in frauds_for(case_id):
        problems.extend(apply_fraud(case_dir, fraud))
    return problems


__all__ = ["FRAUDS", "DIMENSION_TOOLS", "TRUTH_SUFFIX", "apply_case", "frauds_for"]
