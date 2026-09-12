from pathlib import Path

import pymupdf
import pytest

PDF_DOC_ID = "C001_CertIncumbency"
IMAGE_DOC_ID = "ocbc_sample"


class _BlockedSupabaseAccess:
    """
    Stands in for supabase_client.table/.storage in every test. Both
    __call__ and __getattr__ raise, so it fails loudly whether code calls
    it directly (supabase_client.table(...)) or chains through it
    (supabase_client.storage.from_(...).download(...)).
    """

    def __init__(self, entry_point: str) -> None:
        self._entry_point = entry_point

    def _raise(self):
        raise RuntimeError(
            f"Test attempted a real Supabase call via supabase_client.{self._entry_point}(...). "
            "Mock the repository/service function that calls it (e.g. "
            'patch("app.<module>.document_repository") or similar) instead of letting '
            "it reach the network."
        )

    def __call__(self, *args, **kwargs):
        self._raise()

    def __getattr__(self, name):
        self._raise()


@pytest.fixture(autouse=True)
def _block_real_supabase_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Every repository/service module binds the same shared supabase_client
    instance (see app/supabase_client.py — each does
    `from .supabase_client import supabase_client`), so patching attributes
    on that one object blocks real network access everywhere it's used,
    regardless of which module holds the name.

    Without this, a test that forgets to mock a call site silently
    succeeds locally (a real .env with real credentials is present) but
    fails in CI (no network egress) — exactly what happened with
    case_status.sync_submission_finalized in test_orchestrator.py: it
    wasn't mocked, "passed" locally against the real dev database, and
    only broke in CI. Failing loudly here makes that reproducible locally
    the moment it happens, instead of only in CI.
    """
    from app.supabase_client import supabase_client

    monkeypatch.setattr(supabase_client, "table", _BlockedSupabaseAccess("table"))
    # .storage is a read-only property (no setter) whose body just returns
    # self._storage once set — patch that plain backing attribute instead
    # of the property itself, which monkeypatch can't assign to.
    monkeypatch.setattr(supabase_client, "_storage", _BlockedSupabaseAccess("storage"))


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
