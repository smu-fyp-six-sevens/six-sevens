from pathlib import Path

import pytest

from app.auth import require_auth
from app.main import app

TEST_USER = "test-user"  # who the bypassed login says is acting


@pytest.fixture(autouse=True)
def _sync_case_immediately(monkeypatch: pytest.MonkeyPatch):
    """document_repository batches the case stage refresh in production; tests expect it after each write."""
    from app import document_repository

    monkeypatch.setattr(document_repository, "SYNC_DEBOUNCE_SECONDS", 0)


@pytest.fixture(autouse=True)
def _no_correction_hint_cache(monkeypatch: pytest.MonkeyPatch):
    """The hints are cached for minutes in production; a test that patches the corrections must see its own."""
    from app.config import settings

    monkeypatch.setattr(settings, "CORRECTION_HINTS_CACHE_SECONDS", 0)


def _real_case_chain(request: pytest.FixtureRequest) -> bool:
    """A test marked real_case_chain keeps the stage sync, the automatic analysis and the finalised check real."""
    return request.node.get_closest_marker("real_case_chain") is not None


@pytest.fixture(autouse=True)
def _skip_case_stage_sync(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """
    Document writes and analysis runs move the case's stage, which reads the
    case back from Supabase. Tests of those writes aren't about the stage, so
    it is a no-op here; test_case_stage.py exercises the real one, and so does
    any test marked real_case_chain (test_regression_approval_chain.py).
    """
    from app import case_stage

    if not _real_case_chain(request):
        monkeypatch.setattr(case_stage, "sync_case_stage", lambda case_id: None)


@pytest.fixture(autouse=True)
def _no_automatic_case_analysis(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """
    Detection runs and an officer's document changes may start the case analysis, which reads the case back.
    Tests of those aren't about it; test_case_auto_analysis.py and real_case_chain tests exercise the real one.
    """
    from app.case_pipeline import service

    async def _skip(case_id):
        return None

    if not _real_case_chain(request):
        monkeypatch.setattr(service, "start_if_due", _skip)


@pytest.fixture(autouse=True)
def _cases_are_not_finalised(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """
    Every route that changes a document or case first checks its case isn't
    finalised, which reads the case. Tests of those routes aren't about that,
    so every case reads as open; test_case_stage.py covers the refusal, and
    real_case_chain tests keep the real check.
    """
    from app import case_service

    if not _real_case_chain(request):
        monkeypatch.setattr(case_service, "is_case_finalised", lambda case_id: False)


@pytest.fixture(autouse=True)
def _bypass_auth():
    """
    Every route except /health and /auth/login requires require_auth (see
    app/main.py) — existing tests exercise route behavior, not the shared-
    credential login gate itself, so bypass it here by default. test_auth.py
    clears this override locally to test the real login/token flow.
    """
    app.dependency_overrides[require_auth] = lambda: TEST_USER
    yield
    app.dependency_overrides.pop(require_auth, None)


@pytest.fixture(autouse=True)
def published_events(monkeypatch: pytest.MonkeyPatch) -> list:
    """Events published during a test, in order. No handler runs: a test that wants one calls it directly."""
    from app.events import bus

    events: list = []
    monkeypatch.setattr(bus, "_dispatch_in_process", events.append)
    return events


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
    from app import supabase_client as supabase_module
    from app.supabase_client import supabase_client

    monkeypatch.setattr(supabase_client, "table", _BlockedSupabaseAccess("table"))
    # .storage is a read-only property (no setter) whose body just returns
    # self._storage once set — patch that plain backing attribute instead
    # of the property itself, which monkeypatch can't assign to.
    monkeypatch.setattr(supabase_client, "_storage", _BlockedSupabaseAccess("storage"))
    # Secret-key client: every DELETE goes through it, and it bypasses RLS.
    monkeypatch.setattr(supabase_module, "_service_client", _BlockedSupabaseAccess("service_supabase_client"))
    monkeypatch.setattr(supabase_module, "acreate_client", _refuse_real_realtime)


@pytest.fixture(autouse=True)
def _no_land_use_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    URA zoning (registry_lookups/land_use.py) reads an index built on disk and,
    when there is none, downloads ~175 MB from data.gov.sg in a background
    thread. Tests get neither: zone lookups report "unavailable" unless a test
    patches land_use.zone_at with the zone it needs.
    """
    from app.fraud_detection.external_validation.registry_lookups import land_use
    from app.fraud_detection.external_validation.registry_lookups.http import LookupUnavailable

    async def _no_index():
        raise LookupUnavailable("no land-use index in tests")

    monkeypatch.setattr(land_use, "current_index", _no_index)
    # Same for URA's Approved Residential Use (registry_lookups/ura.py): no access key, so it's
    # never called; a test sets one and patches ura.approved_residential_use.
    from app.config import settings

    monkeypatch.setattr(settings, "URA_API_KEY", "")


async def _refuse_real_realtime(*args, **kwargs):
    raise RuntimeError(
        "Test attempted a real Supabase Realtime connection via acreate_client(...). "
        "Patch realtime_listener.start / build_async_supabase_client instead."
    )


# --- Regression suite tags (SCRUM-206, SCRUM-246, SCRUM-296, SCRUM-323) -----
# Existing tests that also serve as regression tests for a sprint's manual test
# cases, tagged here centrally (rather than editing each file) so that
# `pytest -m sprint1` … `-m sprint5` run each sprint's full set. The
# regression-specific test files tag themselves with @pytest.mark.sprintN.
# Keys are a test file, or file::test_name for a single test.
_EXISTING_REGRESSION_TESTS: dict[str, list[tuple[str, str, str]]] = {
    # SCRUM-6 Upload Documents (backend side of DI-008/009/012/013)
    "test_documents.py::test_upload_batch_partial_failure": [("sprint1", "SCRUM-6", "DI-013")],
    "test_documents.py::test_upload_batch_rejects_too_many_files": [("sprint1", "SCRUM-6", "DI-004")],
    "test_ingestion.py::test_ingest_rejects_unsupported_type": [("sprint1", "SCRUM-6", "DI-008")],
    # SCRUM-17 Auto-trigger classification: stored document is queued (MA-001)
    "test_documents.py::test_upload_batch_marks_queued_and_publishes_uploaded": [
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
    # --- Sprint 3 (SCRUM-246) ---
    # SCRUM-116 Update case (CM-014 – CM-023)
    "test_cases.py::test_update_case_saves_company_name_and_returns_fresh_summary": [
        ("sprint3", "SCRUM-116", "CM-015"),
    ],
    "test_cases.py::test_update_case_trims_surrounding_whitespace": [("sprint3", "SCRUM-116", "CM-021")],
    "test_cases.py::test_update_case_rejects_blank_company_name": [("sprint3", "SCRUM-116", "CM-020 / CM-021")],
    # SCRUM-117 Delete case / documents (CM-024 – CM-028)
    "test_cases.py::test_delete_case_calls_case_service_delete_case": [("sprint3", "SCRUM-117", "CM-025")],
    "test_cases.py::test_delete_case_rejects_when_a_document_is_in_flight": [("sprint3", "SCRUM-117", "CM-027")],
    "test_documents.py::test_delete_document_removes_it_and_resyncs_the_case": [("sprint3", "SCRUM-117", "CM-024")],
    # SCRUM-26 Trigger detection (MA-005 – MA-012)
    "test_documents.py::test_delete_document_rejects_an_approved_document": [("sprint3", "SCRUM-26", "MA-007")],
    "test_documents.py::test_locked_document_cannot_be_changed": [("sprint3", "SCRUM-26", "MA-007 / MA-010")],
    "test_documents.py::test_approve_document_rejects_unreviewed_low_confidence_field": [
        ("sprint3", "SCRUM-26", "MA-008"),
    ],
    "test_documents.py::test_reject_document_success": [("sprint3", "SCRUM-26", "MA-011")],
    # SCRUM-110 Case match (DI-039 – DI-047)
    "test_case_match.py": [("sprint3", "SCRUM-110", "DI-039 – DI-047")],
    # SCRUM-119 Lead Detection Agent verdict (FD-017 – FD-019)
    "test_detection_verdict.py": [("sprint3", "SCRUM-119", "FD-017 – FD-019")],
    # Fraud detection checks (FD-001 – FD-026), the deterministic layer under each tool
    "test_image_tampering.py": [("sprint3", "SCRUM-65", "FD-001 / FD-002")],
    "test_text_overlay_anomalies.py": [("sprint3", "SCRUM-63", "FD-003 / FD-004")],
    "test_layout_anomalies.py": [("sprint3", "SCRUM-62", "FD-005 / FD-006")],
    "test_metadata_analysis.py": [("sprint3", "SCRUM-66", "FD-007 / FD-008")],
    "test_numerical_validation.py": [("sprint3", "SCRUM-68", "FD-009 / FD-010")],
    "test_date_anomalies.py": [("sprint3", "SCRUM-69", "FD-011 / FD-012")],
    "test_image_manipulation.py": [("sprint3", "SCRUM-64", "FD-013 / FD-014")],
    "test_font_consistency.py": [("sprint3", "SCRUM-61", "FD-021 – FD-024")],
    "test_spelling_anomaly.py": [("sprint3", "SCRUM-70", "FD-025 / FD-026")],
    "test_company_identity.py": [("sprint3", "SCRUM-70", "FD-025"), ("sprint4", "SCRUM-255", "CD-001 – CD-007")],
    # --- Sprint 4 (SCRUM-296) ---
    # SCRUM-81 Attach officer notes (CM-029 – CM-036)
    "test_notes.py::test_note_is_signed_by_the_officer_and_timestamped": [("sprint4", "SCRUM-81", "CM-031 / CM-033")],
    "test_notes.py::test_note_on_a_document_from_another_case_is_refused": [("sprint4", "SCRUM-81", "CM-030")],
    "test_notes.py::test_reload_returns_the_saved_notes_unchanged": [("sprint4", "SCRUM-81", "CM-034 / CM-035")],
    "test_notes.py::test_note_on_an_approved_document_is_saved_and_leaves_the_document_alone": [
        ("sprint4", "SCRUM-81", "CM-036"),
    ],
    "test_notes.py::test_reply_takes_its_notes_document_and_keeps_the_original": [("sprint4", "SCRUM-81", "CM-036")],
    "test_notes.py::test_author_can_edit_their_note_and_it_shows_as_edited": [("sprint4", "SCRUM-81", "CM-036")],
    # SCRUM-84 / SCRUM-90 Case risk score and its reasons (RS-001 – RS-008)
    "test_case_risk.py": [("sprint4", "SCRUM-84", "RS-001 – RS-008")],
    # SCRUM-52 Run case analysis only once every approved document is scanned (MA-013 / MA-014)
    "test_case_analysis.py": [("sprint4", "SCRUM-52", "MA-013 / MA-014")],
    # SCRUM-54 Handle invalid agent output (MA-024 – MA-027): every agent's reply is validated, never raised
    "test_output_validation.py": [("sprint4", "SCRUM-54", "MA-024 – MA-027")],
    # SCRUM-253 / SCRUM-254 / SCRUM-255 Cross-document comparisons (CD-001 – CD-007, CD-012 – CD-021)
    "test_cross_document_deterministic.py": [("sprint4", "SCRUM-253", "CD-001 – CD-021")],
    "test_cross_document_mismatches.py": [("sprint4", "SCRUM-253", "CD-012 – CD-021")],
    "test_address_matching.py": [("sprint4", "SCRUM-254", "CD-018 – CD-021")],
    # SCRUM-256 Public reference checks: addresses against OneMap/HDB (CD-008 / CD-009)
    "test_registry_lookups.py": [("sprint4", "SCRUM-256", "CD-008 / CD-009")],
    # SCRUM-67 AI-generated content (FD-027 / FD-028), the deterministic layer under ai_generation_tool
    "test_ai_generation.py": [("sprint4", "SCRUM-67", "FD-027 / FD-028")],
    # --- Sprint 5 (SCRUM-323) ---
    # SCRUM-282 Cross-page consistency (CD-022 – CD-037), the deterministic layer under page_consistency_tool
    "test_page_consistency.py": [("sprint5", "SCRUM-282", "CD-022 – CD-037")],
    # SCRUM-288 Agentic guardrails (AE-001 – AE-012): the content guard at ingest, and detection's injection check
    "test_content_guard.py": [("sprint5", "SCRUM-288", "AE-001 – AE-007")],
    "test_prompt_injection.py": [("sprint5", "SCRUM-288", "AE-008 – AE-012")],
    # SCRUM-297 Detection accuracy: precision, recall and flags per check (CM-060 – CM-063, AE-013 – AE-015)
    "test_finding_reviews.py": [("sprint5", "SCRUM-297", "CM-060 – CM-063 / AE-013 – AE-015")],
    # SCRUM-55 Agent observability: a cancelled run keeps its error and can be retried (MA-029)
    "test_events.py::test_an_officer_cancel_saves_the_error_and_ends_the_run": [("sprint5", "SCRUM-55", "MA-029")],
    "test_detection.py::test_retry_detection_relaunches_after_a_cancelled_run": [("sprint5", "SCRUM-55", "MA-029")],
    # SCRUM-298 Case audit trail: each entry names its officer and what changed (CM-064 – CM-074)
    "test_audit_trail.py": [("sprint5", "SCRUM-298", "CM-064 – CM-072")],
    # SCRUM-82 Export case report (CM-075 – CM-085)
    "test_case_report.py": [("sprint5", "SCRUM-82", "CM-075 – CM-085")],
    # SCRUM-80 Case stages (CM-086 – CM-092)
    "test_case_stage.py": [("sprint5", "SCRUM-80", "CM-086 – CM-092")],
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
