"""
Every registered agent's prompt renders, and every agent can be fingerprinted.

A registered agent with no prompt entry, or a prompt template with a placeholder its renderer doesn't fill, raises at
runtime. Because every run's end event fingerprints the agents it used (agent_versions.for_run), one such agent once
broke the fingerprint of every run. This catches it in CI instead.
"""

from __future__ import annotations

import re

import pytest

from app import agent_versions
from app.agent_prompts import get_agent_prompt
from app.agent_registry import _list_agents
from app.langgraph.extraction_prompts import KNOWN_DOCUMENT_TYPES

AGENT_IDS = [record.id for record in _list_agents()]
# A placeholder left unfilled by str.format, e.g. "{document_type}": a single-brace name with no surrounding braces.
_UNFILLED = re.compile(r"(?<!\{)\{[a-z_]+\}(?!\})")


def test_the_registry_lists_agents() -> None:
    assert AGENT_IDS and len(AGENT_IDS) == len(set(AGENT_IDS))


@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_every_registered_agent_has_a_prompt_that_renders(agent_id: str) -> None:
    prompt = get_agent_prompt(agent_id)

    assert prompt.sections, f"{agent_id} has no prompt sections"
    for section in prompt.sections:
        assert section.text.strip(), f"{agent_id}: section {section.title!r} is empty"


@pytest.mark.parametrize("document_type", KNOWN_DOCUMENT_TYPES)
def test_the_extractor_prompt_renders_for_every_document_type(document_type: str) -> None:
    prompt = get_agent_prompt("field_extractor", document_type)

    assert prompt.document_type == document_type
    for section in prompt.sections:
        assert section.text.strip()
        assert not _UNFILLED.search(section.text), f"unfilled placeholder in the {document_type} extractor prompt"


def test_an_unknown_agent_or_document_type_is_refused_not_rendered_wrongly() -> None:
    with pytest.raises(KeyError):
        get_agent_prompt("no_such_agent")
    with pytest.raises(ValueError):
        get_agent_prompt("field_extractor", "no_such_type")


def test_every_agent_is_fingerprinted() -> None:
    versions = agent_versions.current()

    assert set(versions) == set(AGENT_IDS)
    for agent_id, version in versions.items():
        assert version.fingerprint and len(version.prompt_sha256) == 64, agent_id


@pytest.mark.parametrize("kind", ["detection", "case_analysis", "extraction_rerun", "document"])
def test_a_run_of_each_kind_gets_its_agents_fingerprints(kind: str) -> None:
    fingerprints = agent_versions.for_run(kind)

    assert fingerprints, f"no fingerprints for a {kind} run"
    assert set(fingerprints) <= set(AGENT_IDS)
