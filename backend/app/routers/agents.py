from fastapi import APIRouter, HTTPException, Query

from ..agent_registry import AgentRecord, list_agents
from ..agent_prompts import AgentPrompt, get_agent_prompt

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentRecord])
async def get_agent_registry() -> list[AgentRecord]:
    """Read-only snapshot of configured agents and the limits currently applied to their pipelines."""
    return list_agents()


@router.get("/{agent_id}/prompt", response_model=AgentPrompt)
async def get_agent_prompt_view(
    agent_id: str, document_type: str | None = Query(default=None),
) -> AgentPrompt:
    """Return source prompt templates for one role, with per-run inputs marked as placeholders."""
    try:
        return get_agent_prompt(agent_id, document_type)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown agent") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
