from pathlib import Path

import pymupdf
import pytest

PDF_DOC_ID = "C001_CertIncumbency"
IMAGE_DOC_ID = "ocbc_sample"


def _write_sample_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((72, 100), "Golden Harvest Trading Pte Ltd", fontsize=12, fontname="helv")
    page.insert_text((72, 140), "100 000", fontsize=12, fontname="helv")
    doc.set_metadata(
        {
            "title": "Certificate of Incumbency",
            "author": "Corporate Secretary",
            "subject": "KYC",
            "creator": "pymupdf",
        }
    )
    doc.save(path)
    doc.close()


def _write_sample_png(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    lines = (
        "OCBC",
        "STATEMENT",
        "Account Number",
        "12345678",
        "Available Balance",
        "1000.00",
        "Customer Name",
        "Jane Citizen",
        "Branch Code",
        "001",
    )
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=22, fontname="helv")
        y += 40
    pix = page.get_pixmap(dpi=150)
    pix.save(str(path))
    doc.close()


@pytest.fixture
def isolated_injection_templates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use generated PDF/PNG fixtures instead of backend/data/templates."""
    templates = tmp_path / "templates"
    working = tmp_path / "working"
    templates.mkdir()
    working.mkdir()

    _write_sample_pdf(templates / f"{PDF_DOC_ID}.pdf")
    _write_sample_png(templates / f"{IMAGE_DOC_ID}.png")

    monkeypatch.setattr("app.injection.pdf.TEMPLATES_DIR", templates)
    monkeypatch.setattr("app.injection.pdf.WORKING_DIR", working)
    monkeypatch.setattr("app.injection.store.TEMPLATES_DIR", templates)
    monkeypatch.setattr("app.injection.store.WORKING_DIR", working)
    return templates
