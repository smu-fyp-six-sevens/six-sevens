"""
Which version of each agent ran. A fingerprint hashes what the agent directory shows for an agent (its prompt
templates, model, tools and call budget): change any of them and the fingerprint changes, so a version can't be
forgotten. Computed once per process (prompts change only with a deploy).

Each run's end event carries the fingerprints of the agents it used (`agent_versions` in its detail, so the
audit trail answers "which version produced this?"). The `agent_versions` table holds one row per fingerprint,
with the commit it first appeared in; each Claude-calling role adds its rows at start-up and skips any already
stored. Neither is read to decide anything.
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache

from pydantic import BaseModel

from .agent_prompts import get_agent_prompt
from .agent_registry import AgentRecord, list_agents
from .config import settings
from .langgraph.extraction_prompts import KNOWN_DOCUMENT_TYPES
from .supabase_client import service_supabase_client

logger = logging.getLogger("classifier_pipeline.agent_versions")

TABLE = "agent_versions"
_FINGERPRINT_CHARS = 16
# The agents each kind of run uses (its work label in workers/).
_DOCUMENT_AGENTS = ("document_classifier", "field_extractor")
_RUN_AGENTS = {
    "detection": ("detection_lead", "layout_rendering", "content_validation", "digital_authenticity"),
    "case_analysis": ("hypothesis_planner", "hypothesis_judge"),
    "extraction_rerun": ("field_extractor",),
}


class AgentVersion(BaseModel):
    agent_id: str
    fingerprint: str
    model: str
    tools: list[str]
    prompt_sha256: str
    call_budget: str


def _prompt_text(agent_id: str) -> str:
    """Every prompt template the agent can be sent; the extractor's for each document type, plus its second look."""
    if agent_id == "field_extractor":
        from .langgraph.extract_graph import _SYSTEM_PROMPT as second_look

        parts = [s.text for t in KNOWN_DOCUMENT_TYPES for s in get_agent_prompt(agent_id, t).sections]
        return "\n\n".join([*parts, second_look])
    return "\n\n".join(s.text for s in get_agent_prompt(agent_id).sections)


def _version(record: AgentRecord) -> AgentVersion:
    prompt_sha = hashlib.sha256(_prompt_text(record.id).encode("utf-8")).hexdigest()
    identity = json.dumps(
        {"id": record.id, "model": record.model, "tools": sorted(record.tools), "prompt": prompt_sha,
         "budget": record.call_budget},
        sort_keys=True,
    )
    return AgentVersion(
        agent_id=record.id, fingerprint=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:_FINGERPRINT_CHARS],
        model=record.model, tools=list(record.tools), prompt_sha256=prompt_sha, call_budget=record.call_budget,
    )


@lru_cache(maxsize=1)
def current() -> dict[str, AgentVersion]:
    return {record.id: _version(record) for record in list_agents()}


def for_run(kind: str) -> dict[str, str]:
    """The fingerprints of the agents a run of this kind uses, for its end event. Empty if they can't be worked out."""
    try:
        versions = current()
        return {a: versions[a].fingerprint for a in _RUN_AGENTS.get(kind, _DOCUMENT_AGENTS) if a in versions}
    except Exception:
        logger.exception("Couldn't fingerprint the agents for a %s run", kind)
        return {}


def register() -> None:
    """Stores a row for each fingerprint not seen before. Best-effort: a failure is logged. Blocking."""
    try:
        rows = [{**v.model_dump(), "git_commit": settings.GIT_COMMIT} for v in current().values()]
        service_supabase_client().table(TABLE).upsert(rows, on_conflict="fingerprint", ignore_duplicates=True).execute()
        logger.info("Agent versions recorded: %s", ", ".join(f"{r['agent_id']}={r['fingerprint']}" for r in rows))
    except Exception as exc:
        logger.warning("Agent versions not recorded: %s", exc)
