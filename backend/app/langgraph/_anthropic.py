"""
Shared ChatAnthropic constructor.

Every node that talks to Claude builds the model the same way (model name,
api_key, temperature=0, max_tokens) and hits the same pyright false positive:
ChatAnthropic's pydantic fields declare aliases (`model`'s alias is
`model_name`, `max_tokens`'s is `max_tokens_to_sample`) and pyright's
pydantic-plugin overload resolution picks the alias-named overload instead of
honouring the model's `populate_by_name` config that makes both spellings
valid at runtime — a known pyright/pydantic limitation, not a real bug (this
call already works in production). Centralising the constructor here means
that's suppressed once instead of at every call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import settings

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic


def build_chat_anthropic(model: str, max_tokens: int, *, temperature: float = 0) -> "ChatAnthropic":
    from langchain_anthropic import ChatAnthropic  # local import: optional dep until phase 0 is installed

    return ChatAnthropic(  # pyright: ignore[reportCallIssue]
        model=model,  # pyright: ignore[reportCallIssue]
        api_key=settings.ANTHROPIC_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,  # pyright: ignore[reportCallIssue]
    )
