"""
Extractor (Phase 3) — one Sonnet call, replacing the Extractor.yml workflow.
Prompt building (`build_extraction_prompt`) and output parsing are reused
as-is from extraction_prompts.py / orchestrator.py rather than duplicated:
they are pure functions with no Dify dependency, so both the Dify path and
this one can share them while the migration is judged.
"""

from __future__ import annotations

import logging

from .. import orchestrator
from ..config import settings
from ..extraction_prompts import build_extraction_prompt

logger = logging.getLogger("classifier_pipeline.langgraph.extract")


def _split_prompt_for_caching(document_type: str, raw_text: str) -> tuple[str, str]:
    """
    build_extraction_prompt always ends with the schema/instructions
    followed by "Document text:\n{raw_text}". Building the same prompt with
    an empty raw_text gives a byte-stable prefix (same document_type -> same
    prefix across every document and run) to put cache_control on, per
    LANGGRAPH_MIGRATION_PLAN.md section 5 ("schema first, raw_text last").
    """
    prefix = build_extraction_prompt(document_type, "")
    return prefix, raw_text


async def run_extraction(document_type: str, raw_text: str) -> dict:
    from langchain_anthropic import ChatAnthropic  # local import: optional dep until phase 0 is installed

    from .cache_gate import cache_gate

    prefix, text = _split_prompt_for_caching(document_type, raw_text)
    model = ChatAnthropic(
        model=settings.LANGGRAPH_SONNET_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        temperature=0,
        max_tokens=4096,
    )
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": text},
        ],
    }
    # Gated per document_type (the actual cache key — see
    # _split_prompt_for_caching): the first document of a given type in a
    # batch pays for the cache write, the rest of that type wait for it
    # rather than racing it. Different types never block each other.
    response = await cache_gate.run(f"extract:{document_type}", lambda: model.ainvoke([message]))
    llm_output = response.content if isinstance(response.content, str) else str(response.content)
    # Reuses orchestrator's own parser (cleans <think>/``` fences, splits
    # {value, confidence} wrappers, averages field confidence) — see its
    # docstring in orchestrator.py.
    return orchestrator._parse_extraction_output(llm_output)
