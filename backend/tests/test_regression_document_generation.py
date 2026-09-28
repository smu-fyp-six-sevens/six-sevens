"""
Regression tests (SCRUM-206) for the mock-document tooling — "Test Cases -
Sprint 1.xlsx" and "Test Cases - Sprint 2.xlsx", Test Document and
Development Test sheets:

  SCRUM-10  Create document templates             DT-001 – DT-003
  SCRUM-12  Generate non-fraudulent documents      DT-004, DT-005
  SCRUM-13  Masterlist records                     TD-003 – TD-006, DT-006 – DT-009
  SCRUM-15  Unrecognised documents                 TD-001, TD-002, DT-010, DT-011
  SCRUM-112 Generate optional documents            DT-020 – DT-022
  SCRUM-120 Update mastersheet                     DT-023 – DT-026
  SCRUM-115 Generate additional file formats       DT-027, DT-028

Fraud injection (SCRUM-14 DT-012/013, SCRUM-121 DT-014 – DT-019) is covered by
the existing tests/test_fraud_injections.py, test_injectors.py and
test_injection_router.py.

Documents are generated for real into a temporary folder from the committed
mastersheet (doc_gen_scripts/v16_document_mastersheet.xlsx), once per test
session: C001 (one of the original 36 sets) and C064 (a SCRUM-120 case with
every optional document type and image-format NRIC/passports). No network,
no Supabase, no AI.
"""
from __future__ import annotations

import re
import warnings
from collections import defaultdict
from pathlib import Path

import openpyxl
import pymupdf
import pytest

import scripts.generate_from_mastersheet as generator
import scripts.verify_documents as verify_documents
from app.models import DocumentType

pytestmark = pytest.mark.filterwarnings("ignore:Data Validation extension")

MASTERSHEET = generator.MASTERSHEET
ORIGINAL_CASES = [f"C{n:03d}" for n in range(1, 37)]  # the 36 SCRUM-12 sets
NEW_CASES_FROM = 37  # SCRUM-120 added C037 onwards

# The 11 document-type sheets every case has a row in (SCRUM-12's "11 types").
DOCUMENT_SHEETS = [
    "Cert of Incorporation", "Cert of Incumbency", "Company Constitution", "Credit Reports",
    "List of Loans", "Bank Statements", "FATCA Self-Declaration", "Proof of Address",
    "NRIC-Passport", "Source of Wealth Docs", "Payslips",
]
UNRECOGNISED_SHEETS = ["Budget Proposal (Unrecognised Documents)", "Purchase Order (Unrecognised)"]
ALL_DOCUMENT_SHEETS = DOCUMENT_SHEETS + ["Notice of Assessment", "Optional Documents", *UNRECOGNISED_SHEETS]

# Generated-filename marker for each of the 11 document types in an original case.
TYPE_FILE_MARKERS = {
    "Bank statement": "BankStatement",
    "Certificate of incorporation": "CertIncorp",
    "Certificate of incumbency": "CertIncumbency",
    "Company constitution": "Constitution",
    "Credit report": "CreditReport",
    "List of loans": "ListLoans",
    "FATCA/CRS": "FATCA",
    "Proof of address": "ProofAddress",
    "NRIC/passport": "NRICPassport",
    "Source of wealth": "SourceWealth",
    "Payslip": "Payslip",
}


# Known defects found by this regression run (reported, not fixed here). The
# main tests skip exactly these so they still catch anything new; each has
# its own strict-xfail test below that starts failing once it's fixed —
# the signal to delete the entry.
REG_004_NONDETERMINISTIC_FILES = {"C001_CertIncorp.pdf"}  # random receipt number
REG_005_UNTAGGED_FRAUD_ROWS = {("Cert of Incorporation", "BizFile_994116.pdf")}  # C042


# --- mastersheet ------------------------------------------------------------


@pytest.fixture(scope="session")
def workbook():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return openpyxl.load_workbook(MASTERSHEET, read_only=True, data_only=True)


def _rows(workbook, sheet: str) -> list[dict]:
    rows = workbook[sheet].iter_rows(values_only=True)
    header = next(rows)
    return [dict(zip(header, r)) for r in rows if any(c not in (None, "") for c in r)]


def _sheet_name(workbook, prefix: str) -> str:
    return next(name for name in workbook.sheetnames if name.startswith(prefix))


def _case_of(row: dict) -> str | None:
    return row.get("Case ID") or row.get("Case ID (auto)")


@pytest.fixture(scope="session")
def registry(workbook) -> dict[str, dict]:
    return {r["Case ID"]: r for r in _rows(workbook, "Case Registry")}


# --- generated documents ----------------------------------------------------


def _generate(out_root: Path, case_id: str) -> Path:
    original = generator.OUT_DIR
    generator.OUT_DIR = out_root
    try:
        generator.generate_for_case(case_id)
    finally:
        generator.OUT_DIR = original
    return out_root / case_id


@pytest.fixture(scope="session")
def generated(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("documents_generated")
    return {case_id: _generate(root, case_id) for case_id in ("C001", "C064")}


def _text(path: Path) -> str:
    with pymupdf.open(path) as document:
        return " ".join(page.get_text() for page in document)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).upper()


def _find(folder: Path, marker: str) -> list[Path]:
    return sorted(p for p in folder.iterdir() if marker in p.name)


# === SCRUM-10 Create document templates =====================================


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-10", "DT-001")
@pytest.mark.parametrize("document_type, marker", TYPE_FILE_MARKERS.items())
def test_a_template_produces_each_of_the_11_document_types(generated, document_type, marker):
    files = _find(generated["C001"], marker)
    assert files, f"no {document_type} generated for C001"
    for path in files:
        assert path.stat().st_size > 1000
        with pymupdf.open(path) as document:
            assert document.page_count >= 1


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-10", "DT-002")
def test_templates_contain_the_supplied_synthetic_values(generated, registry, workbook):
    case = registry["C001"]
    cert = _normalise(_text(_find(generated["C001"], "CertIncorp")[0]))
    assert _normalise(case["Company Name"]) in cert
    assert str(case["Company UEN / Reg No."]).upper() in cert

    payslip_row = next(r for r in _rows(workbook, "Payslips") if r["Case ID (auto)"] == "C001")
    payslip = _normalise(_text(_find(generated["C001"], "Payslip")[0]))
    assert _normalise(payslip_row["Individual Name (auto)"]) in payslip


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-10", "DT-003")
def test_the_same_inputs_produce_the_same_documents(generated, tmp_path):
    again = _generate(tmp_path, "C001")

    first = sorted(p.name for p in generated["C001"].iterdir())
    assert sorted(p.name for p in again.iterdir()) == first
    for name in first:
        if name in REG_004_NONDETERMINISTIC_FILES:
            continue
        if name.endswith(".pdf"):
            assert _text(again / name) == _text(generated["C001"] / name), name
        else:
            assert (again / name).read_bytes() == (generated["C001"] / name).read_bytes(), name


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-10", "DT-003")
@pytest.mark.xfail(
    strict=True,
    reason="REG-004 (known defect): the Certificate of Incorporation's receipt number uses "
    "unseeded random (coi_generator._generate_receipt_no), so it changes on every run.",
)
def test_certificate_of_incorporation_is_reproducible(generated, tmp_path):
    again = _generate(tmp_path, "C001")
    for name in REG_004_NONDETERMINISTIC_FILES:
        assert _text(again / name) == _text(generated["C001"] / name)


# === SCRUM-12 Generate non-fraudulent documents =============================


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-12", "DT-004")
def test_at_least_36_complete_sets_across_all_11_document_types(workbook, registry):
    cases_by_sheet = {sheet: {_case_of(r) for r in _rows(workbook, sheet)} for sheet in DOCUMENT_SHEETS}
    complete = [c for c in registry if all(c in cases for cases in cases_by_sheet.values())]
    assert len(complete) >= 36
    assert set(ORIGINAL_CASES) <= set(complete)


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-12", "DT-005")
@pytest.mark.parametrize(
    "marker",
    ["CertIncorp", "CertIncumbency", "Constitution", "ListLoans", "NOA", "SourceWealth"],
)
def test_every_company_document_in_a_set_matches_the_mastersheet(generated, registry, marker):
    case = registry["C001"]
    for path in _find(generated["C001"], marker):
        text = _normalise(_text(path))
        assert _normalise(case["Company Name"]).replace(" PTE. LTD.", "").replace(" PTE LTD", "") in text, path.name


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-12", "DT-005")
def test_personal_documents_in_a_set_match_the_applicant(generated, registry):
    # The credit report is on the applicant (Director & UBO), not the company.
    applicant = registry["C001"]
    text = _normalise(_text(_find(generated["C001"], "CreditReport")[0]))
    assert _normalise(applicant["Applicant Name (Director & UBO)"]) in text
    assert str(applicant["Applicant NRIC No."]).upper() in text


# === SCRUM-13 Masterlist records =============================================


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-13", "TD-003 / DT-006")
@pytest.mark.xfail(
    strict=True,
    raises=FileNotFoundError,
    reason="REG-003 (known defect): scripts/verify_documents.py still points at "
    "v9_document_mastersheet.xlsx; only v16 is in the repo, so the masterlist check can't run.",
)
def test_masterlist_check_script_runs_against_the_committed_mastersheet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        openpyxl.load_workbook(verify_documents.MASTERSHEET, read_only=True)


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-13", "TD-003 / DT-006")
def test_every_generated_document_is_logged_in_the_masterlist(generated, workbook):
    logged = defaultdict(set)
    for prefix in ALL_DOCUMENT_SHEETS:
        for row in _rows(workbook, _sheet_name(workbook, prefix[:25])):
            if row.get("Filename"):
                logged[_case_of(row)].add(Path(str(row["Filename"])).stem)
    for case_id, folder in generated.items():
        for path in folder.iterdir():
            assert path.stem in logged[case_id], f"{case_id}/{path.name} has no masterlist row"


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-13", "TD-004 / DT-007")
def test_every_case_record_has_the_required_company_and_applicant_details(registry):
    required = [
        "Company Name", "Company UEN / Reg No.", "Registered Address",
        "Applicant Name (Director & UBO)", "Applicant NRIC No.",
    ]
    incomplete = {
        case_id: [field for field in required if not str(row.get(field) or "").strip()]
        for case_id, row in registry.items()
    }
    assert {c: f for c, f in incomplete.items() if f} == {}


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-13", "TD-005 / DT-008")
def test_every_document_row_is_linked_to_an_existing_case(workbook, registry):
    individuals = {r["Individual ID"]: r["Case ID"] for r in _rows(workbook, "Individuals")}
    orphans = []
    for prefix in ALL_DOCUMENT_SHEETS:
        sheet = _sheet_name(workbook, prefix[:25])
        for row in _rows(workbook, sheet):
            case_id = _case_of(row)
            if case_id not in registry:
                orphans.append((sheet, row.get("Filename"), case_id))
            individual = row.get("Individual ID")
            if individual and individuals.get(individual) != case_id:
                orphans.append((sheet, row.get("Filename"), f"{individual} not in {case_id}"))
    assert orphans == []


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-13", "TD-006 / DT-009")
def test_every_document_row_records_its_fraud_details_or_none(workbook):
    dimensions = {r[0] for r in workbook["Fraud Dimensions"].iter_rows(values_only=True) if r and r[0]}
    problems = []
    for prefix in ALL_DOCUMENT_SHEETS:
        sheet = _sheet_name(workbook, prefix[:25])
        for row in _rows(workbook, sheet):
            if (sheet, row.get("Filename")) in REG_005_UNTAGGED_FRAUD_ROWS:
                continue
            injected = str(row.get("Fraud Injected? (Y/N)") or "").strip().upper()
            dimension = row.get("Detection Dimension")
            if injected == "Y" and (dimension not in dimensions or not row.get("Fraud Description")):
                problems.append((sheet, row.get("Filename"), "fraud injected but type/description missing"))
            elif injected == "N" and dimension:
                problems.append((sheet, row.get("Filename"), "no fraud but a dimension recorded"))
            elif injected not in ("Y", "N"):
                problems.append((sheet, row.get("Filename"), f"Fraud Injected? is {injected!r}"))
    assert problems == []


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-13", "TD-006 / DT-009")
@pytest.mark.xfail(
    strict=True,
    reason="REG-005 (known defect): mastersheet row BizFile_994116.pdf (C042) is marked "
    "Fraud Injected = Y with no Detection Dimension or Fraud Description.",
)
def test_known_untagged_fraud_rows_have_been_tagged(workbook):
    for sheet, filename in REG_005_UNTAGGED_FRAUD_ROWS:
        row = next(r for r in _rows(workbook, sheet) if r.get("Filename") == filename)
        assert row.get("Detection Dimension") and row.get("Fraud Description")


# === SCRUM-15 Unrecognised documents =========================================


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-15", "TD-001 / DT-010")
def test_unrecognised_templates_match_no_defined_document_type(workbook):
    defined = {t.value.replace("_", " ") for t in DocumentType} - {"unrecognised"}
    for prefix in UNRECOGNISED_SHEETS:
        sheet = _sheet_name(workbook, prefix[:25])
        subject = sheet.split("(")[0].strip().lower()  # "budget proposal", "purchase order"
        assert subject not in defined
        assert not any(subject in d or d in subject for d in defined)


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-15", "TD-002 / DT-011")
def test_unrecognised_documents_are_listed_under_the_unrecognised_sheets(workbook):
    for prefix in UNRECOGNISED_SHEETS:
        sheet = _sheet_name(workbook, prefix[:25])
        assert "Unrecognised" in sheet
        rows = _rows(workbook, sheet)
        assert len(rows) >= 36
        assert all(row.get("Filename") for row in rows)


# === SCRUM-112 Generate optional documents ===================================


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-112", "DT-020 – DT-022")
@pytest.mark.parametrize("document_type", [
    "Source of Wealth (Individual) - Director & UBO",
    "Source of Wealth (Individual) - Shareholder",
    "Proof of Address (Company)",
])
def test_optional_documents_are_generated_from_the_mastersheet(generated, workbook, document_type):
    rows = [r for r in _rows(workbook, "Optional Documents")
            if r["Case ID"] == "C064" and r["Document Type"] == document_type]
    assert rows
    for row in rows:
        path = generated["C064"] / row["Filename"]
        assert path.exists(), f"{row['Filename']} not generated"
        assert path.stat().st_size > 1000


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-112", "DT-022")
def test_corporate_noa_is_generated_with_its_mastersheet_fields(generated, workbook):
    row = next(r for r in _rows(workbook, "Notice of Assessment") if r["Case ID"] == "C064")
    text = _normalise(_text(generated["C064"] / row["Filename"]))
    assert str(row["Tax Reference Number (UEN) (auto)"]).upper() in text
    year = re.sub(r"\D", "", str(row["Year of Assessment"]))  # sheet "YA2025", PDF "Year of Assessment 2025"
    assert f"YEAR OF ASSESSMENT {year}" in text


# === SCRUM-120 Update mastersheet ============================================


def _new_cases(registry) -> list[str]:
    return [c for c in registry if int(c[1:]) >= NEW_CASES_FROM]


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-120", "DT-023")
def test_at_least_15_new_cases_were_added(registry):
    assert len(_new_cases(registry)) >= 15


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-120", "DT-024")
def test_new_cases_include_the_new_optional_document_types(workbook, registry):
    new = set(_new_cases(registry))
    optional_types = {r["Document Type"] for r in _rows(workbook, "Optional Documents") if r["Case ID"] in new}
    assert any("Proof of Address (Company)" in t for t in optional_types)
    assert any("Source of Wealth (Individual)" in t for t in optional_types)
    assert new <= {r["Case ID"] for r in _rows(workbook, "Notice of Assessment")}


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-120", "DT-025")
def test_new_cases_use_realistic_submission_filenames(workbook, registry):
    new = set(_new_cases(registry))
    names = [r["Filename"] for r in _rows(workbook, "NRIC-Passport") if r["Case ID (auto)"] in new]
    # Real submissions aren't named after an internal case ID ("C064_...").
    assert names
    assert not [n for n in names if re.match(r"^C\d{3}", str(n))]


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-120", "DT-026")
def test_fraud_cases_are_tagged_with_a_known_fraud_dimension(workbook, registry):
    dimensions = {r[0] for r in workbook["Fraud Dimensions"].iter_rows(values_only=True) if r and r[0]}
    tagged = defaultdict(set)
    known_defect_cases: set[str] = set()
    for prefix in ALL_DOCUMENT_SHEETS:
        for row in _rows(workbook, _sheet_name(workbook, prefix[:25])):
            if (prefix, row.get("Filename")) in REG_005_UNTAGGED_FRAUD_ROWS:
                known_defect_cases.add(_case_of(row))
                continue
            if str(row.get("Fraud Injected? (Y/N)") or "").upper() == "Y":
                tagged[_case_of(row)].add(row.get("Detection Dimension"))
    fraud_cases = [c for c, row in registry.items() if str(row.get("Fraud Case? (Y/N)")).upper() == "Y"]
    assert fraud_cases
    for case_id in fraud_cases:
        if not tagged[case_id] and case_id in known_defect_cases:
            continue  # REG-005 — its only fraud row is the untagged one
        assert tagged[case_id], f"{case_id} is a fraud case with no tagged document"
        assert tagged[case_id] <= dimensions, f"{case_id} has unknown dimension {tagged[case_id] - dimensions}"


# === SCRUM-115 Generate additional file formats ==============================

_SIGNATURES = {
    ".pdf": b"%PDF",
    ".png": b"\x89PNG",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
}


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-115", "DT-027")
def test_documents_are_generated_in_pdf_png_jpg_and_jpeg(generated):
    files = [p for folder in generated.values() for p in folder.iterdir()]
    assert {p.suffix.lower() for p in files} == {".pdf", ".png", ".jpg", ".jpeg"}
    for path in files:
        signature = _SIGNATURES[path.suffix.lower()]
        assert path.read_bytes().startswith(signature), f"{path.name} isn't really a {path.suffix}"


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-115", "DT-028")
def test_generated_formats_follow_the_document_format_assumptions(workbook, registry):
    new = set(_new_cases(registry))
    ids = [r["Filename"] for r in _rows(workbook, "NRIC-Passport") if r["Case ID (auto)"] in new]
    assert ids and all(Path(n).suffix.lower() in {".jpg", ".jpeg", ".png"} for n in ids)

    bills = [r["Filename"] for r in _rows(workbook, "Proof of Address") if r["Case ID (auto)"] in new]
    assert all(Path(n).suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png"} for n in bills)

    for sheet in ["Cert of Incorporation", "Cert of Incumbency", "Company Constitution", "Credit Reports",
                  "List of Loans", "Bank Statements", "FATCA Self-Declaration", "Source of Wealth Docs",
                  "Payslips", "Notice of Assessment"]:
        names = [r["Filename"] for r in _rows(workbook, sheet) if _case_of(r) in new]
        assert all(Path(n).suffix.lower() == ".pdf" for n in names), sheet
