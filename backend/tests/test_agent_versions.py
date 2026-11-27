"""Agent fingerprints (agent_versions), the table that records them, and the agent directory (agent_registry, GET /agents)."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import agent_versions
from app.agent_registry import AgentRecord, _list_agents, list_agents
from app.case_pipeline.cross_document.evidence import CaseContext
from app.case_pipeline.cross_document.tools import build_tools
from app.langgraph.detection.specialists import SPECIALISTS
from app.main import app

client = TestClient(app)
SPECIALIST = "content_validation"


def _by_id() -> dict[str, AgentRecord]:
    return {record.id: record for record in _list_agents()}


def _fingerprints(records: list[AgentRecord], prompt_for=None) -> dict[str, str]:
    with patch.object(agent_versions, "_prompt_text", prompt_for or (lambda agent_id: f"prompt of {agent_id}")):
        return {record.id: agent_versions._version(record).fingerprint for record in records}


# --- fingerprints -------------------------------------------------------------------------------------------------


def test_a_fingerprint_is_the_same_every_time_it_is_worked_out() -> None:
    records = _list_agents()
    assert _fingerprints(records) == _fingerprints(records)


def test_a_specialists_prompt_change_alters_only_its_fingerprint() -> None:
    records = _list_agents()
    before = _fingerprints(records)
    after = _fingerprints(records, lambda agent_id: f"prompt of {agent_id}" + (" edited" if agent_id == SPECIALIST else ""))
    assert [a for a in before if before[a] != after[a]] == [SPECIALIST]


@pytest.mark.parametrize("change", ["model", "tools"])
def test_a_specialists_model_or_tools_change_alters_only_its_fingerprint(change: str) -> None:
    records = _list_agents()
    before = _fingerprints(records)
    update = {"model": "another-model"} if change == "model" else {"tools": [*_by_id()[SPECIALIST].tools, "new_tool"]}
    changed = [r.model_copy(update=update) if r.id == SPECIALIST else r for r in records]
    after = _fingerprints(changed)
    assert [a for a in before if before[a] != after[a]] == [SPECIALIST]


def test_the_tool_details_are_not_part_of_a_fingerprint() -> None:
    record = _by_id()[SPECIALIST]
    details = list_agents()
    with_details = next(r for r in details if r.id == SPECIALIST)
    assert with_details.tool_details
    assert _fingerprints([record]) == _fingerprints([with_details])


def test_for_run_gives_no_fingerprints_when_fingerprinting_raises(caplog: pytest.LogCaptureFixture) -> None:
    with patch.object(agent_versions, "current", side_effect=RuntimeError("boom")), caplog.at_level(logging.ERROR):
        assert agent_versions.for_run("detection") == {}
    assert "Couldn't fingerprint" in caplog.text


# --- the table ----------------------------------------------------------------------------------------------------


def test_register_upserts_each_fingerprint_ignoring_duplicates() -> None:
    supabase = MagicMock()
    with patch.object(agent_versions, "service_supabase_client", return_value=supabase):
        agent_versions.register()
    supabase.table.assert_called_once_with("agent_versions")
    upsert = supabase.table.return_value.upsert
    rows = upsert.call_args.args[0]
    assert {row["agent_id"] for row in rows} == set(agent_versions.current())
    assert all(row["fingerprint"] and "git_commit" in row for row in rows)
    assert upsert.call_args.kwargs == {"on_conflict": "fingerprint", "ignore_duplicates": True}


def test_a_failed_register_only_logs(caplog: pytest.LogCaptureFixture) -> None:
    with patch.object(agent_versions, "service_supabase_client", side_effect=RuntimeError("down")), caplog.at_level(logging.WARNING):
        agent_versions.register()  # must not raise
    assert "Agent versions not recorded" in caplog.text


# --- the directory ------------------------------------------------------------------------------------------------


def test_get_agents_returns_a_fingerprint_per_agent() -> None:
    response = client.get("/agents")
    assert response.status_code == 200
    body = response.json()
    assert {a["id"] for a in body} == {r.id for r in _list_agents()}
    for agent in body:
        assert agent["fingerprint"] == agent_versions.current()[agent["id"]].fingerprint


def test_every_agent_has_a_prompt_version() -> None:
    assert all(record.prompt_version for record in list_agents())


def test_every_tool_of_every_agent_has_a_description() -> None:
    for record in list_agents():
        for tool in record.tool_details:
            assert tool.name and tool.description.strip(), f"{record.id}: {tool.name!r}"


def test_the_specialists_tool_details_match_their_tools() -> None:
    by_id = {record.id: record for record in list_agents()}
    for name, spec in SPECIALISTS.items():
        assert [t.name for t in by_id[name].tool_details] == [t.name for t in spec.tools]
        assert by_id[name].tool_details and by_id[name].tools == [t.name for t in spec.tools]


def test_the_case_agents_tool_details_match_the_tools_they_are_built_with() -> None:
    built = [tool.name for tool in build_tools(CaseContext(case_id="", docs={}), [])]
    by_id = {record.id: record for record in list_agents()}
    for name in ("hypothesis_planner", "hypothesis_judge"):
        assert [t.name for t in by_id[name].tool_details] == built
