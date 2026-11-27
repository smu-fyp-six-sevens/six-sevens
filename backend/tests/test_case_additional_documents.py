"""The documents to request live on the case row (cases.recommended_additional_documents), written by the analysis run."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from app import case_repository, case_service
from app.case_pipeline import service
from app.case_pipeline.models import CaseAnalysis
from app.case_pipeline.repository import finish_run
from app.case_pipeline.risk import outlook
from app.models import AdditionalDocument, AdditionalDocuments, CaseListItem, CaseRecord, CaseRisk

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def _additional(title: str = "Latest ACRA profile", reason=None) -> AdditionalDocuments:
    items = [AdditionalDocument(title=title, why="UEN differs", source="finding")] if title else []
    return AdditionalDocuments(items=items, reason=reason, current_score=62, current_level="High")


def _case(column) -> CaseRecord:
    return CaseRecord(
        id="case-1", greenlink_case_id="GLDB-1", created_at=NOW, updated_at=NOW,
        recommended_additional_documents=column,
    )


def _analysis(status: str = "complete", additional: AdditionalDocuments | None = None) -> CaseAnalysis:
    return CaseAnalysis(
        run_id="run-1", case_id="case-1", status=status, fingerprint="f", started_at=NOW,
        additional_documents=additional,
    )


# --- the column as a case sees it ---------------------------------------------------------------


def test_a_stored_result_comes_back_as_additional_documents_and_the_column_is_not_a_field() -> None:
    case = _case(_additional().model_dump(mode="json"))

    assert case.additional_documents == _additional()
    assert not hasattr(case, "recommended_additional_documents")


@pytest.mark.parametrize("column", [[], {}], ids=["never analysed", "empty object"])
def test_an_unanalysed_case_has_no_additional_documents(column) -> None:
    assert _case(column).additional_documents is None


def test_the_stored_result_is_not_returned_by_the_case_record_or_the_case_list() -> None:
    column = _additional().model_dump(mode="json")
    case = _case(column)
    row = {**case.model_dump(), "recommended_additional_documents": column}

    item = CaseListItem(**row, documents_status=case_service.compute_documents_status([]))

    assert item.additional_documents is not None
    assert "additional_documents" not in case.model_dump()
    assert "additional_documents" not in item.model_dump()


# --- what the case summary shows ------------------------------------------------------------------


def test_the_summary_shows_the_list_stored_on_the_case() -> None:
    case = _case(_additional("Latest ACRA profile").model_dump(mode="json"))

    summary = case_service.build_summary(case, [])

    assert [item.title for item in summary.additional_documents.items] == ["Latest ACRA profile"]


def test_the_case_row_is_the_only_source() -> None:
    case = _case(_additional("Stored on the case").model_dump(mode="json"))
    run_with_a_copy = _analysis(additional=_additional("Run copy"))

    summary = case_service.build_summary(case, [], None, run_with_a_copy, run_with_a_copy)

    assert [item.title for item in summary.additional_documents.items] == ["Stored on the case"]


def test_a_copy_left_in_a_run_is_not_shown_when_the_case_has_none() -> None:
    run_with_a_copy = _analysis(additional=_additional("Run copy"))

    summary = case_service.build_summary(_case([]), [], None, run_with_a_copy, run_with_a_copy)

    assert summary.additional_documents is None


def test_a_case_never_analysed_shows_none() -> None:
    assert case_service.build_summary(_case([]), []).additional_documents is None


def test_the_summary_lists_the_missing_checklist_under_its_own_name() -> None:
    summary = case_service.build_summary(_case(_additional().model_dump(mode="json")), [])

    assert summary.missing_requirements
    assert all(isinstance(item, str) for item in summary.missing_requirements)
    assert "recommended_additional_documents" not in summary.model_dump()


# --- writing it ---------------------------------------------------------------------------------


def test_the_whole_result_is_stored_on_the_case_row() -> None:
    with patch("app.case_repository.supabase_client") as client:
        case_repository.set_recommended_additional_documents("case-1", _additional("Latest ACRA profile"))

        update = client.table.return_value.update
        stored = update.call_args[0][0]["recommended_additional_documents"]
        assert stored["items"][0]["title"] == "Latest ACRA profile"
        assert stored["current_score"] == 62
        client.table.assert_called_with("cases")
        update.return_value.eq.assert_called_once_with("id", "case-1")


def test_storing_nothing_clears_the_column() -> None:
    with patch("app.case_repository.supabase_client") as client:
        case_repository.set_recommended_additional_documents("case-1", None)

        assert client.table.return_value.update.call_args[0][0] == {"recommended_additional_documents": []}


def test_a_runs_own_result_no_longer_carries_the_list() -> None:
    with patch("app.case_pipeline.repository.supabase_client") as client, patch(
        "app.case_pipeline.repository.latest_run_id", return_value="run-1"
    ):
        finish_run(_analysis(additional=_additional()))

        result = client.table.return_value.update.call_args[0][0]["result"]
        assert "additional_documents" not in result
        assert "findings" in result


@pytest.mark.parametrize(
    ("published", "status", "writes"),
    [(True, "complete", True), (False, "complete", False), (True, "failed", False)],
    ids=["newest complete run", "superseded run", "failed run"],
)
async def test_only_the_newest_complete_run_updates_the_case(published: bool, status: str, writes: bool) -> None:
    analysis = _analysis(status=status, additional=_additional())
    with (
        patch("app.case_pipeline.service.repository.finish_run", return_value=published),
        patch("app.case_pipeline.service.risk_store.save") as save_risk,
        patch("app.case_pipeline.service.case_repository.set_recommended_additional_documents") as save_documents,
    ):
        await service._store_result(analysis)

    assert save_documents.called is writes
    assert save_risk.called is writes
    if writes:
        save_documents.assert_called_once_with("case-1", analysis.additional_documents)


# --- Open questions reach the requests -------------------------------------------------------


class ScriptedRequests:
    """build_chat_anthropic(...).bind_tools(...): answers once, and keeps the prompt it was sent."""

    def __init__(self, reply: AIMessage) -> None:
        self.reply = reply
        self.prompts: list[list] = []

    def bind_tools(self, tools, tool_choice=None) -> ScriptedRequests:
        return self

    async def ainvoke(self, messages):
        self.prompts.append(list(messages))
        return self.reply


def _reply(*requests: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "submit_requests", "args": {"requests": list(requests)}, "id": "c1"}])


@pytest.fixture
def requests_model(monkeypatch):
    def script(reply: AIMessage) -> ScriptedRequests:
        model = ScriptedRequests(reply)
        monkeypatch.setattr(outlook, "build_chat_anthropic", lambda *args, **kwargs: model)
        return model
    return script


NO_FINDINGS = CaseRisk(state="scored", level="Low", score=5, points=1.0)
QUESTION = {"question": "Net pay does not arrive on the statement", "would_settle_with": "The October payroll register"}


async def test_open_questions_alone_are_enough_to_ask_for_documents(requests_model) -> None:
    model = requests_model(_reply({"title": "October payroll register", "what_it_shows": "The pay run", "addresses": []}))

    requests, status = await outlook.propose_requests_with_status(
        NO_FINDINGS, overview="- payslip_1: payslip (pay date 28 Oct 2026)", open_questions=[QUESTION])

    assert (status, [r.title for r in requests]) == ("ok", ["October payroll register"])
    [prompt] = model.prompts[0]
    assert ('<document_data source="open_questions">\n- Net pay does not arrive on the statement '
            'Would settle it: The October payroll register\n</document_data>') in prompt.content
    assert '<document_data source="case_documents">\n- payslip_1: payslip (pay date 28 Oct 2026)\n</document_data>' in prompt.content
    assert '<document_data source="findings">\n- none\n</document_data>' in prompt.content


@pytest.mark.parametrize("questions", [None, [], [{"question": "Unsettled", "would_settle_with": ""}]],
                         ids=["none", "empty", "no document named"])
async def test_with_no_finding_and_no_usable_question_nothing_is_asked(requests_model, questions) -> None:
    model = requests_model(_reply())

    assert await outlook.propose_requests_with_status(NO_FINDINGS, open_questions=questions) == ([], "not_asked")
    assert model.prompts == []


async def test_a_question_touching_a_protected_attribute_is_withheld_from_the_prompt(requests_model) -> None:
    model = requests_model(_reply())

    await outlook.propose_requests_with_status(NO_FINDINGS, open_questions=[
        {"question": "The director's name suggests he is foreign", "would_settle_with": "Payroll register"}])

    sent = model.prompts[0][0].content
    assert "foreign" not in sent and outlook.withhold_if_protected("foreign") in sent


async def test_no_more_questions_are_sent_than_requests_may_be_made(requests_model) -> None:
    model = requests_model(_reply())

    await outlook.propose_requests_with_status(NO_FINDINGS, open_questions=[
        {"question": f"Question number {n}", "would_settle_with": "A document"} for n in range(outlook.MAX_REQUESTS + 3)])

    sent = model.prompts[0][0].content
    assert f"Question number {outlook.MAX_REQUESTS - 1}" in sent and f"Question number {outlook.MAX_REQUESTS}" not in sent
