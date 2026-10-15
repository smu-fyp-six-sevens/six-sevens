"""
Shared ChatAnthropic constructor.

Every node that talks to Claude builds the model the same way (model name,
api_key, temperature=0, max_tokens). ChatAnthropic's pydantic fields declare
aliases (`model` is `model_name`, `max_tokens` is `max_tokens_to_sample`) and
pyright's pydantic plugin only accepts the alias spellings, so the
constructor is called with those.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from ..config import settings
from ..query_stats import llm_callback

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic


# One instance per (class, model, max_tokens, temperature), so calls share its HTTP connection pool
# instead of a new client and TLS handshake each time. The class is in the key so a patched one isn't cached over.
_models: dict[tuple, Any] = {}


def warm_up() -> None:
    """Pays the import cost of the Anthropic client at startup, not on the first request's event loop."""
    import langchain_anthropic  # noqa: F401


def build_chat_anthropic(model: str, max_tokens: int, *, temperature: float = 0) -> "ChatAnthropic":
    from langchain_anthropic import ChatAnthropic  # local import: keeps the SDK out of module import time

    key = (ChatAnthropic, model, max_tokens, temperature)
    if key not in _models:
        # Pydantic alias names: pyright's plugin only accepts these, and they are
        # always valid at runtime. timeout/stop are required by that view of the
        # signature; None is the library default.
        _models[key] = ChatAnthropic(
            model_name=model,
            api_key=SecretStr(settings.ANTHROPIC_API_KEY),
            temperature=temperature,
            max_tokens_to_sample=max_tokens,
            timeout=None,
            stop=None,
            callbacks=[llm_callback],  # counts calls and tokens per unit of work (query_stats.py)
        )
    return _models[key]
