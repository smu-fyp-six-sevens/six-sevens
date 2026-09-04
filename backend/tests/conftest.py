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
    page.insert_text((72, 180), "Account No. 12345678", fontsize=12, fontname="helv")
    page.insert_text((72, 220), "Opening Balance", fontsize=12, fontname="helv")
    logo = pymupdf.open()
    logo_page = logo.new_page(width=64, height=32)
    logo_page.draw_rect(
        logo_page.rect, color=(0.8, 0.1, 0.1), fill=(0.8, 0.1, 0.1)
    )
    pixmap = logo_page.get_pixmap()
    logo.close()
    page.insert_image(pymupdf.Rect(24, 18, 88, 50), pixmap=pixmap)
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
        "Account No. 87654321",
        "Available Balance",
        "1000.00",
        "Customer Name",
        "Jane Citizen",
        "Opening Balance",
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
    """Use generated PDF/PNG fixtures instead of document_templates/."""
    templates = tmp_path / "document_templates"
    generated = tmp_path / "documents_generated"
    templates.mkdir()
    generated.mkdir()

    _write_sample_pdf(templates / f"{PDF_DOC_ID}.pdf")
    _write_sample_png(templates / f"{IMAGE_DOC_ID}.png")

    monkeypatch.setattr("app.injection.pdf.TEMPLATES_DIR", templates)
    monkeypatch.setattr("app.injection.pdf.DOCUMENTS_GENERATED_DIR", generated)
    monkeypatch.setattr("app.injection.store.TEMPLATES_DIR", templates)
    monkeypatch.setattr("app.injection.store.DOCUMENTS_GENERATED_DIR", generated)
    return templates
