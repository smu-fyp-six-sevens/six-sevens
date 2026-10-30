"""
Shared ChatAnthropic constructor.

Every node that talks to Claude builds the model the same way (model name,
api_key, max_tokens, effort). ChatAnthropic's pydantic fields declare
aliases (`model` is `model_name`, `max_tokens` is `max_tokens_to_sample`) and
pyright's pydantic plugin only accepts the alias spellings, so the
constructor is called with those.

No `temperature`: API version 2026-10-21 rejects it on every model. Thinking is
adaptive (the models' default) and `effort` sets how much; thinking blocks come
back beside the text and llm_io.message_text skips them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from langchain_core.callbacks import AsyncCallbackHandler
from pydantic import SecretStr

from ..config import settings
from ..guardrails.leak_check import leak_callback
from ..llm_io import ModelRefused, refusal_reason
from ..query_stats import llm_callback

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic

Effort = Literal["low", "medium"]


class _RefusalCheck(AsyncCallbackHandler):
    """Raises ModelRefused when Claude declines, so a refusal fails with its own reason instead of as unreadable output."""

    raise_error = True

    async def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                reason = refusal_reason(getattr(message, "response_metadata", None)) or refusal_reason(
                    response.llm_output
                )
                if reason:
                    raise ModelRefused(reason)


_refusal_callback = _RefusalCheck()

# One instance per (class, model, max_tokens, effort), so calls share its HTTP connection pool
# instead of a new client and TLS handshake each time. The class is in the key so a patched one isn't cached over.
_models: dict[tuple, Any] = {}


def warm_up() -> None:
    """Pays the import cost of the Anthropic client at startup, not on the first request's event loop."""
    import langchain_anthropic  # noqa: F401


def build_chat_anthropic(model: str, max_tokens: int, *, effort: Effort) -> "ChatAnthropic":
    from langchain_anthropic import ChatAnthropic  # local import: keeps the SDK out of module import time

    key = (ChatAnthropic, model, max_tokens, effort)
    if key not in _models:
        # Pydantic alias names: pyright's plugin only accepts these, and they are
        # always valid at runtime. timeout/stop are required by that view of the
        # signature.
        _models[key] = ChatAnthropic(
            model_name=model,
            api_key=SecretStr(settings.ANTHROPIC_API_KEY),
            max_tokens_to_sample=max_tokens,
            effort=effort,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=settings.LLM_MAX_RETRIES,
            stop=None,
            # Counts calls and tokens per unit of work (query_stats.py); flags a reply that repeats its own
            # instructions (guardrails/leak_check.py); turns a refusal into ModelRefused.
            callbacks=[llm_callback, leak_callback, _refusal_callback],
        )
    return _models[key]
