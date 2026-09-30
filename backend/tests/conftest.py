from pathlib import Path

import pymupdf
import pytest

from app.auth import require_auth
from app.main import app

PDF_DOC_ID = "C001_CertIncumbency"
IMAGE_DOC_ID = "ocbc_sample"

# app/injection/* is a dev-only synthetic fraud-injection tool used to
# generate test documents for the detector — not part of the graded
# detection pipeline (see CLAUDE.md). Deliberately not unit-tested for now;
# the files below are left in place (not deleted) in case that changes.
collect_ignore = [
    "test_field_text.py",
    "test_fraud_injections.py",
    "test_injection_router.py",
    "test_injectors.py",
    "test_pdf.py",
    "test_store.py",
]


@pytest.fixture(autouse=True)
def _bypass_auth():
    """
    Every route except /health and /auth/login requires require_auth (see
    app/main.py) — existing tests exercise route behavior, not the shared-
    credential login gate itself, so bypass it here by default. test_auth.py
    clears this override locally to test the real login/token flow.
    """
    app.dependency_overrides[require_auth] = lambda: None
    yield
    app.dependency_overrides.pop(require_auth, None)


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


class RealAICallBlocked(RuntimeError):
    pass


def _refuse_real_claude(*args, **kwargs):
    raise RealAICallBlocked(
        "Test attempted a real Claude request. Mock the AI response instead: patch "
        "the model (e.g. patch('langchain_anthropic.ChatAnthropic', return_value=<fake>) "
        "or patch('app.langgraph._anthropic.build_chat_anthropic', return_value=<fake>)), "
        "or patch the step itself (extract.run_extraction, the classify subgraph)."
    )


@pytest.fixture(autouse=True)
def _block_real_ai_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Same idea as _block_real_supabase_network below, for Claude: a local
    .env carries a real ANTHROPIC_API_KEY, so a test that forgets to mock
    the AI would quietly call Claude and spend credits.

    Blocks the point where a real request would be sent — ChatAnthropic's
    generate/stream methods — rather than where the client is built, so a
    test may fake the model at any level (the ChatAnthropic class,
    build_chat_anthropic, or a whole pipeline step) and only a genuinely
    unmocked call is refused. Belt and braces: no API key, and the SDK
    pointed at an unroutable host.
    """
    from langchain_anthropic import ChatAnthropic

    from app.config import settings

    for method in ("_generate", "_agenerate", "_stream", "_astream"):
        monkeypatch.setattr(ChatAnthropic, method, _refuse_real_claude)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://anthropic.invalid")


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
    case_service.sync_submission_finalized in test_orchestrator.py: it
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
    page.draw_rect(
        pymupdf.Rect(430, 36, 560, 120),
        color=(0.85, 0.05, 0.05),
        fill=(0.85, 0.05, 0.05),
    )
    page.draw_rect(
        pymupdf.Rect(444, 50, 492, 106),
        color=(0.05, 0.05, 0.85),
        fill=(0.05, 0.05, 0.85),
    )
    pix = page.get_pixmap(dpi=150)
    pix.save(str(path))
    doc.close()


@pytest.fixture(scope="session")
def _injection_template_files(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Session-scoped so the PDF/PNG live at a stable path for the whole test
    run. app.injection.pdf.discover_fields_from_image caches OCR results by
    (path, mtime, size) — with a fresh tmp_path (and therefore a fresh OCR
    run) on every single test, that cache never hit, and RapidOCR ran once
    per test (~5-14s each, dominating test_fraud_injections.py's runtime).
    Reusing the same files across the session lets the existing cache do
    its job: only the first test pays the OCR cost.
    """
    templates = tmp_path_factory.mktemp("document_templates")
    _write_sample_pdf(templates / f"{PDF_DOC_ID}.pdf")
    _write_sample_png(templates / f"{IMAGE_DOC_ID}.png")
    return templates


@pytest.fixture
def isolated_injection_templates(
    tmp_path: Path, _injection_template_files: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Use generated PDF/PNG fixtures instead of document_templates/."""
    generated = tmp_path / "documents_generated"
    generated.mkdir()

    monkeypatch.setattr("app.injection.pdf.TEMPLATES_DIR", _injection_template_files)
    monkeypatch.setattr("app.injection.pdf.DOCUMENTS_GENERATED_DIR", generated)
    monkeypatch.setattr("app.injection.store.TEMPLATES_DIR", _injection_template_files)
    monkeypatch.setattr("app.injection.store.DOCUMENTS_GENERATED_DIR", generated)
    return _injection_template_files


# --- Regression suite tags (SCRUM-206) ----------------------------------------
# Existing tests that also serve as regression tests for a sprint's manual test
# cases, tagged here centrally (rather than editing each file) so that
# `pytest -m sprint1` / `pytest -m sprint2` run each sprint's full set. The
# regression-specific test files tag themselves with @pytest.mark.sprintN.
# Keys are a test file, or file::test_name for a single test.
_EXISTING_REGRESSION_TESTS: dict[str, list[tuple[str, str, str]]] = {
    # SCRUM-6 Upload Documents (backend side of DI-008/009/012/013)
    "test_documents.py::test_upload_batch_partial_failure": [("sprint1", "SCRUM-6", "DI-013")],
    "test_documents.py::test_upload_batch_rejects_too_many_files": [("sprint1", "SCRUM-6", "DI-004")],
    "test_ingestion.py::test_ingest_rejects_unsupported_type": [("sprint1", "SCRUM-6", "DI-008")],
    # SCRUM-17 Auto-trigger classification: stored document is queued (MA-001)
    "test_queue_service.py::test_enqueue_marks_queued_and_puts_document_id_on_queue": [
        ("sprint1", "SCRUM-17", "MA-001"),
    ],
    # Document generation, the mastersheet and fraud injection (TD-001 – TD-006,
    # DT-001 – DT-028) are out of scope: test-data tooling, checked manually.
    # SCRUM-78 / SCRUM-96 case management API (CM-001 – CM-013)
    "test_cases.py::test_list_cases": [("sprint2", "SCRUM-78", "CM-001")],
    "test_cases.py::test_get_case_returns_summary_with_documents_status": [("sprint2", "SCRUM-83", "CM-005")],
    "test_cases.py::test_create_case_endpoint_with_company_name": [("sprint2", "SCRUM-96", "CM-010 / CM-012")],
    # SCRUM-115 generated PDF/PNG/JPG documents go through ingestion (DT-029)
    "test_ingestion.py::test_ingest_native_pdf": [("sprint2", "SCRUM-115", "DT-029")],
    "test_ingestion.py::test_ingest_scanned_pdf_uses_ocr": [("sprint2", "SCRUM-115", "DT-029")],
    "test_ingestion.py::test_ingest_image": [("sprint2", "SCRUM-115", "DT-029")],
    # SCRUM-114 / SCRUM-118 checklist mapping
    "test_mapper.py": [("sprint2", "SCRUM-118", "DI-035 – DI-038")],
}


# tryfirst: the tags must be on each test before pytest's own -m filtering
# (also a collection hook) decides what to deselect.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(str(item.fspath)).name
        name = item.originalname or item.name
        for key in (path, f"{path}::{name}"):
            for sprint, story, test_case in _EXISTING_REGRESSION_TESTS.get(key, []):
                item.add_marker(getattr(pytest.mark, sprint))
                item.add_marker(pytest.mark.story(story, test_case))
