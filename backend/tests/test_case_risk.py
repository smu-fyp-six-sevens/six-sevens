from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app import case_service
from app.case_pipeline.risk.case_risk import compute_case_risk
from app.models import CaseRecord, DocumentRecord, DocumentStatus


def _document(
    document_id: str,
    status: DocumentStatus = DocumentStatus.DETECTED,
    document_type: str | None = "bank_statement",
) -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename=f"{document_id}.pdf",
        document_type=document_type,
        status=status,
        uploaded_at="2026-10-01T00:00:00Z",
    )


def _finding(
    severity: str, tool: str = "spelling_tool", n: int = 1, rationale: str = "Looks off."
) -> dict:
    return {
        "id": f"{tool}-{n}",
        "tool": tool,
        "severity": severity,
        "summary": f"{severity} finding from {tool}",
        "rationale": rationale,
        "specialists": ["content_validation"],
        "via_second_look": False,
    }


def _report(*findings: dict, unable: list[dict] | None = None) -> dict:
    return {"verdict": "flagged" if findings else "clean", "findings": list(findings), "unable_to_assess": unable or []}


# --- gate -------------------------------------------------------------------


def test_case_with_no_documents_is_not_scored() -> None:
    risk = compute_case_risk([], {}, set())

    assert risk.state == "no_documents"
    assert risk.level is None


def test_unrecognised_rejected_and_cancelled_documents_are_left_out() -> None:
    documents = [
        _document("other", DocumentStatus.APPROVED, document_type="unrecognised"),
        _document("rejected", DocumentStatus.REJECTED),
        _document("cancelled", DocumentStatus.CANCELLED),
        _document("scanned"),
    ]

    risk = compute_case_risk(documents, {"scanned": _report()}, set())

    assert risk.state == "scored"
    assert (risk.documents_scanned, risk.documents_total) == (1, 1)


def test_only_ineligible_documents_reads_as_no_documents() -> None:
    # Approved as Other before Other documents were scanned: no run, no report.
    risk = compute_case_risk(
        [_document("other", DocumentStatus.APPROVED, document_type="unrecognised")], {}, set()
    )

    assert risk.state == "no_documents"


def test_a_scanned_other_document_counts_towards_the_risk() -> None:
    documents = [_document("other", document_type="unrecognised")]

    risk = compute_case_risk(documents, {"other": _report(_finding("high", "font_consistency_tool"))}, set())

    assert risk.state == "scored"
    assert (risk.documents_scanned, risk.documents_total) == (1, 1)
    assert risk.level == "High"


def test_an_other_document_being_scanned_holds_the_case_at_pending() -> None:
    documents = [_document("done"), _document("other", DocumentStatus.DETECTING, document_type="unrecognised")]

    risk = compute_case_risk(documents, {"done": _report()}, set())

    assert risk.state == "pending"


def test_an_other_document_whose_scan_crashed_fails_the_case() -> None:
    documents = [_document("other", DocumentStatus.APPROVED, document_type="unrecognised")]

    risk = compute_case_risk(documents, {}, {"other"})

    assert risk.state == "failed"


@pytest.mark.parametrize(
    "status", [DocumentStatus.ANALYZED, DocumentStatus.APPROVED, DocumentStatus.DETECTING]
)
def test_a_document_not_yet_detected_holds_the_case_at_pending(status: DocumentStatus) -> None:
    documents = [_document("done"), _document("waiting", status)]

    risk = compute_case_risk(documents, {"done": _report(_finding("high", "font_consistency_tool"))}, set())

    assert risk.state == "pending"
    assert risk.level is None
    assert risk.contributing_findings == []
    assert (risk.documents_scanned, risk.documents_total) == (1, 2)


def test_detected_status_without_a_stored_report_is_still_pending() -> None:
    risk = compute_case_risk([_document("doc")], {}, set())

    assert risk.state == "pending"


def test_a_failed_detection_run_reads_as_failed() -> None:
    documents = [_document("done"), _document("crashed", DocumentStatus.APPROVED)]

    risk = compute_case_risk(documents, {"done": _report()}, {"crashed"})

    assert risk.state == "failed"


# --- bands ------------------------------------------------------------------


def test_clean_case_is_low_with_no_findings() -> None:
    risk = compute_case_risk([_document("doc")], {"doc": _report()}, set())

    assert (risk.state, risk.level, risk.score, risk.points) == ("scored", "Low", 0, 0)
    assert risk.contributing_findings == []


def test_info_findings_are_not_listed_or_scored() -> None:
    risk = compute_case_risk([_document("doc")], {"doc": _report(_finding("info"))}, set())

    assert risk.level == "Low"
    assert risk.contributing_findings == []


@pytest.mark.parametrize(
    "severities, expected_score, expected_level",
    [
        (["low", "low"], 2, "Low"),
        (["medium"], 3, "Medium"),
        (["medium", "low", "low"], 5, "Medium"),
        (["high"], 8, "High"),
    ],
)
def test_score_bands(severities: list[str], expected_score: float, expected_level: str) -> None:
    findings = [_finding(severity, f"check_{i}_tool") for i, severity in enumerate(severities)]

    risk = compute_case_risk([_document("doc")], {"doc": _report(*findings)}, set())

    assert risk.points == expected_score
    assert risk.level == expected_level


def test_mediums_across_documents_reach_high_but_never_critical() -> None:
    documents = [_document(f"doc-{i}") for i in range(4)]
    reports = {f"doc-{i}": _report(_finding("medium")) for i in range(4)}

    risk = compute_case_risk(documents, reports, set())

    assert risk.points == 12
    assert risk.level == "High"


# --- noise cap ----------------------------------------------------------------


def test_many_minor_findings_on_one_document_are_capped() -> None:
    findings = [_finding("low", f"check_{i}_tool") for i in range(12)]

    risk = compute_case_risk([_document("doc")], {"doc": _report(*findings)}, set())

    assert risk.points == 6
    assert risk.level == "Medium"
    assert len(risk.contributing_findings) == 12
    assert all(f.points == 0.5 for f in risk.contributing_findings)


def test_high_findings_are_not_capped() -> None:
    findings = [_finding("high", "font_consistency_tool"), *(_finding("medium", f"check_{i}_tool") for i in range(4))]

    risk = compute_case_risk([_document("doc")], {"doc": _report(*findings)}, set())

    # 8 for the high + 12 medium points capped to 6.
    assert risk.points == 14


# --- repeats from one check -----------------------------------------------------


def test_repeats_from_one_check_on_one_document_add_a_quarter() -> None:
    findings = [_finding("high", "layout_anomalies_tool", n=i) for i in range(4)]

    risk = compute_case_risk([_document("doc")], {"doc": _report(*findings)}, set())

    assert [f.points for f in risk.contributing_findings] == [8, 2, 2, 2]
    assert risk.points == 14


def test_a_checks_strongest_finding_is_the_one_counted_in_full() -> None:
    report = _report(_finding("medium", "font_consistency_tool", 1), _finding("high", "font_consistency_tool", 2))

    risk = compute_case_risk([_document("doc")], {"doc": report}, set())

    assert [(f.severity, f.points) for f in risk.contributing_findings] == [("high", 8), ("medium", 0.75)]


def test_different_checks_each_count_in_full() -> None:
    report = _report(_finding("high", "font_consistency_tool"), _finding("high", "metadata_tool"))

    risk = compute_case_risk([_document("doc")], {"doc": report}, set())

    assert risk.points == 16


def test_the_same_check_on_different_documents_counts_in_full() -> None:
    documents = [_document("a"), _document("b")]
    reports = {doc.id: _report(_finding("high", "font_consistency_tool")) for doc in documents}

    risk = compute_case_risk(documents, reports, set())

    assert risk.points == 16


# --- critical -----------------------------------------------------------------


def test_high_findings_on_three_documents_are_critical() -> None:
    documents = [_document("statement"), _document("nric", document_type="id_document"), _document("payslip")]
    reports = {doc.id: _report(_finding("high", "font_consistency_tool")) for doc in documents}

    risk = compute_case_risk(documents, reports, set())

    assert risk.level == "Critical"
    assert risk.reasons == ["Rated Critical: high-severity findings on 3 documents."]


def test_high_findings_on_two_documents_stay_high() -> None:
    documents = [_document("statement"), _document("nric", document_type="id_document")]
    reports = {doc.id: _report(_finding("high", "font_consistency_tool")) for doc in documents}

    risk = compute_case_risk(documents, reports, set())

    assert risk.level == "High"


def test_highs_from_different_checks_on_one_document_stay_high() -> None:
    report = _report(
        _finding("high", "font_consistency_tool"),
        _finding("high", "image_tampering_tool"),
        _finding("high", "metadata_tool"),
    )

    risk = compute_case_risk([_document("doc")], {"doc": report}, set())

    assert risk.level == "High"


# --- score out of 100 -----------------------------------------------------------


@pytest.mark.parametrize(
    "severities, expected_score",
    [
        ([], 0),
        (["medium"], 25),
        (["high"], 50),
        (["low"], 9),
        # 8 high points + 12 medium points capped to 6.
        (["high", "medium", "medium", "medium", "medium"], 57),
    ],
)
def test_score_is_out_of_100_and_starts_at_the_levels_floor(severities: list[str], expected_score: int) -> None:
    findings = [_finding(severity, f"check_{i}_tool") for i, severity in enumerate(severities)]

    risk = compute_case_risk([_document("doc")], {"doc": _report(*findings)}, set())

    assert risk.score == expected_score


def test_score_flattens_towards_but_never_past_the_levels_ceiling() -> None:
    def high_score(check_count: int) -> int | None:
        findings = [_finding("high", f"check_{i}_tool") for i in range(check_count)]
        risk = compute_case_risk([_document("doc")], {"doc": _report(*findings)}, set())
        assert risk.level == "High"
        return risk.score

    # 8, 24, 40 and 160 points: each step adds less than the one before.
    assert [high_score(n) for n in (1, 3, 5, 20)] == [50, 64, 72, 79]


def test_a_critical_case_always_outscores_a_high_one() -> None:
    critical = compute_case_risk(
        [_document("a"), _document("b"), _document("c")],
        {doc_id: _report(_finding("high", "font_consistency_tool")) for doc_id in ("a", "b", "c")},
        set(),
    )
    high = compute_case_risk(
        [_document("doc")],
        {"doc": _report(*(_finding("high", f"check_{i}_tool") for i in range(10)))},
        set(),
    )

    assert critical.points is not None and high.points is not None
    assert critical.points < high.points
    assert (critical.level, critical.score) == ("Critical", 80)
    assert high.score == 78


def test_bigger_critical_cases_keep_ranking_higher_without_hitting_100() -> None:
    def critical_score(document_count: int) -> int | None:
        documents = [_document(f"doc-{i}") for i in range(document_count)]
        reports = {doc.id: _report(_finding("high", "font_consistency_tool")) for doc in documents}
        risk = compute_case_risk(documents, reports, set())
        assert risk.level == "Critical"
        return risk.score

    assert [critical_score(n) for n in (3, 6, 15)] == [80, 87, 96]


# --- what's listed --------------------------------------------------------------


def test_contributing_findings_are_listed_highest_points_first() -> None:
    documents = [_document("payslip", document_type="payslip"), _document("statement")]
    reports = {
        "payslip": _report(_finding("low")),
        "statement": _report(_finding("high", "font_consistency_tool"), _finding("medium", "date_consistency_tool")),
    }

    risk = compute_case_risk(documents, reports, set())

    assert [(f.filename, f.severity) for f in risk.contributing_findings] == [
        ("statement.pdf", "high"),
        ("statement.pdf", "medium"),
        ("payslip.pdf", "low"),
    ]
    top = risk.contributing_findings[0]
    assert (top.document_id, top.document_type, top.tool, top.points) == (
        "statement", "bank_statement", "font_consistency_tool", 8,
    )


def test_findings_the_specialist_never_assessed_are_marked_unreviewed() -> None:
    report = _report(_finding("medium", rationale="Not assessed by the specialist."))

    risk = compute_case_risk([_document("doc")], {"doc": report}, set())

    assert risk.contributing_findings[0].unreviewed is True
    assert risk.points == 3


def test_checks_that_could_not_run_are_listed_but_not_scored() -> None:
    report = _report(unable=[{"specialist": "layout_rendering", "tool": "layout_anomalies_tool", "reason": "No native text"}])

    risk = compute_case_risk([_document("doc")], {"doc": report}, set())

    assert risk.level == "Low"
    assert [(c.tool, c.reason) for c in risk.unassessed_checks] == [("layout_anomalies_tool", "No native text")]


# --- case summary wiring -------------------------------------------------------


def _case() -> CaseRecord:
    return CaseRecord(
        id="case-1",
        greenlink_case_id="GLDB-1",
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-01T00:00:00Z",
    )


def _analysis(risk, status: str = "complete"):
    from app.case_pipeline.models import CaseAnalysis

    return CaseAnalysis(
        run_id="run-1", case_id="case-1", status=status, fingerprint="",
        started_at="2026-10-01T00:00:00Z", risk=risk,
    )


def test_case_summary_shows_the_risk_its_analysis_produced() -> None:
    risk = compute_case_risk([_document("doc")], {"doc": _report(_finding("high", "font_consistency_tool"))}, set())
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.langgraph_trace") as trace,
        patch("app.case_service.analysis_repository") as analyses,
    ):
        case_repo.get_case.return_value = _case()
        doc_repo.list_documents.return_value = [_document("doc")]
        trace.get_detection_states.return_value = ({"doc": _report()}, set())
        analyses.get_latest_summary.return_value = _analysis(risk)

        summary = case_service.get_case_summary("case-1")

    assert summary is not None and summary.risk is not None
    assert summary.risk.level == "High"


def test_case_never_analysed_has_no_risk() -> None:
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.langgraph_trace") as trace,
        patch("app.case_service.analysis_repository") as analyses,
    ):
        case_repo.get_case.return_value = _case()
        doc_repo.list_documents.return_value = [_document("doc")]
        trace.get_detection_states.return_value = ({"doc": _report(_finding("high", "font_consistency_tool"))}, set())
        analyses.get_latest_summary.return_value = None

        summary = case_service.get_case_summary("case-1")

    assert summary is not None
    assert summary.risk is None
    assert summary.supporting_document_recommendations == []


def test_case_summary_keeps_the_last_complete_risk_while_a_rerun_is_going() -> None:
    risk = compute_case_risk([_document("doc")], {"doc": _report(_finding("high", "font_consistency_tool"))}, set())
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.langgraph_trace") as trace,
        patch("app.case_service.analysis_repository") as analyses,
    ):
        case_repo.get_case.return_value = _case()
        doc_repo.list_documents.return_value = [_document("doc")]
        trace.get_detection_states.return_value = ({"doc": _report()}, set())
        analyses.get_latest_summary.return_value = _analysis(None, status="running")
        analyses.get_latest_complete.return_value = _analysis(risk)
        # A run reads as running only within the time limit after it started.
        running = _analysis(None, status="running")
        running.started_at = datetime.now(timezone.utc)
        analyses.get_latest_summary.return_value = running
        summary = case_service.get_case_summary("case-1")

    assert summary is not None and summary.risk is not None
    assert summary.risk.level == "High"
    assert summary.analysis is not None and summary.analysis.status == "running"


def test_a_running_analysis_past_its_time_limit_reads_as_interrupted() -> None:
    risk = compute_case_risk([_document("doc")], {"doc": _report(_finding("high", "font_consistency_tool"))}, set())
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.langgraph_trace") as trace,
        patch("app.case_service.analysis_repository") as analyses,
    ):
        case_repo.get_case.return_value = _case()
        doc_repo.list_documents.return_value = [_document("doc")]
        trace.get_detection_states.return_value = ({"doc": _report()}, set())
        analyses.get_latest_summary.return_value = _analysis(None, status="running")
        analyses.get_latest_complete.return_value = _analysis(risk)

        summary = case_service.get_case_summary("case-1")

    assert summary is not None and summary.risk is not None  # the last complete score is kept
    assert summary.analysis is not None and summary.analysis.status == "failed"


def test_case_list_reads_saved_scores_and_never_detection_reports() -> None:
    from app.models import CaseRisk

    other_case = _case().model_copy(update={"id": "case-2", "greenlink_case_id": "GLDB-2"})
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.langgraph_trace") as trace,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_cases_page.return_value = ([_case(), other_case], 2)
        doc_repo.list_document_statuses.return_value = [_document("doc")]
        store.latest_many.return_value = {"case-1": CaseRisk(state="scored", level="High", score=70)}

        items = case_service.list_cases_page(limit=25, offset=0, sort="newest", statuses=[], q=None).items

    assert [(i.id, i.risk.level if i.risk else None) for i in items] == [("case-1", "High"), ("case-2", None)]
    trace.get_detection_states.assert_not_called()


# --- the same fact on several documents ----------------------------------------


def _uen_finding(severity: str = "high", n: int = 1, stated: str = "201298459R") -> dict:
    return {
        **_finding(severity, "external_validation_tool", n),
        "check_code": "REG.UEN_EXISTS",
        "stated": stated,
        "summary": f"UEN {stated} is not in ACRA's register",
    }


def test_the_same_unregistered_uen_on_three_documents_scores_once() -> None:
    documents = [_document(d) for d in ("a", "b", "c")]
    reports = {d.id: _report(_uen_finding()) for d in documents}

    risk = compute_case_risk(documents, reports, set())
    deduped = {f.document_id: f for f in risk.contributing_findings}

    assert risk.points == 8
    assert sum(1 for f in risk.contributing_findings if f.points > 0) == 1
    scoring = next(f for f in risk.contributing_findings if f.points > 0)
    assert scoring.also_on == 2
    assert all(f.repeat_of == f"{scoring.document_id[:8]}:{scoring.finding_id.split(':', 1)[1]}"
               for f in deduped.values() if f.points == 0)
    assert len(risk.contributing_findings) == 3  # the copies still show
    assert risk.level == "High"  # one fact is not corroborated tampering


def test_a_different_stated_value_is_a_different_fact() -> None:
    documents = [_document("a"), _document("b")]
    reports = {"a": _report(_uen_finding(stated="201298459R")), "b": _report(_uen_finding(stated="202054321M"))}

    risk = compute_case_risk(documents, reports, set())

    assert risk.points == 16


def test_findings_without_a_stated_value_are_never_merged() -> None:
    documents = [_document(d) for d in ("a", "b", "c")]
    reports = {d.id: _report(_finding("high", "font_consistency_tool")) for d in documents}

    risk = compute_case_risk(documents, reports, set())

    assert risk.level == "Critical" and risk.points == 24


def test_repeats_can_be_turned_off_for_comparison() -> None:
    documents = [_document(d) for d in ("a", "b", "c")]
    reports = {d.id: _report(_uen_finding()) for d in documents}

    assert compute_case_risk(documents, reports, set(), dedupe=False).points == 24
    assert compute_case_risk(documents, reports, set()).points == 8


def test_a_repeat_does_not_use_up_the_documents_noise_cap() -> None:
    documents = [_document("a"), _document("b")]
    own = [_finding("medium", f"check_{i}_tool") for i in range(2)]
    reports = {
        "a": _report(_uen_finding("medium")),
        "b": _report(_uen_finding("medium"), *own),
    }

    risk = compute_case_risk(documents, reports, set())

    # a scores the shared UEN (3); b scores its own two mediums in full (6), not scaled down by the repeat.
    assert risk.points == 9
