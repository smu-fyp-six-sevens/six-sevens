"""
Shared ChatAnthropic constructor.

Every node that talks to Claude builds the model the same way (model name,
api_key, temperature=0, max_tokens). ChatAnthropic's pydantic fields declare
aliases (`model` is `model_name`, `max_tokens` is `max_tokens_to_sample`) and
pyright's pydantic plugin only accepts the alias spellings, so the
constructor is called with those.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from ..config import settings

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic


def build_chat_anthropic(model: str, max_tokens: int, *, temperature: float = 0) -> "ChatAnthropic":
    from langchain_anthropic import ChatAnthropic  # local import: optional dep until phase 0 is installed

    # Pydantic alias names: pyright's plugin only accepts these, and they are
    # always valid at runtime. timeout/stop are required by that view of the
    # signature; None is the library default.
    return ChatAnthropic(
        model_name=model,
        api_key=SecretStr(settings.ANTHROPIC_API_KEY),
        temperature=temperature,
        max_tokens_to_sample=max_tokens,
        timeout=None,
        stop=None,
    )
