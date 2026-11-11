"""
Claude list prices in USD per million tokens (platform.claude.com/docs/en/about-claude/pricing, read 2026-10-30), for
the cost metrics and trace attributes. A call's cost is its exact token counts times these prices, so it matches the
bill unless the organisation has a discount. Matched by the longest model-name prefix; a model missing here costs 0
and is logged once. Server-side web searches are billed per search on top of the tokens (WEB_SEARCH_USD).
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


class Usage(NamedTuple):
    model: str
    uncached_input: int
    output: int  # includes thinking tokens, which are billed as output
    cache_read: int
    cache_write: int  # 5-minute cache writes
    cache_write_1h: int = 0
    # Server-side web searches the call ran, billed per search on top of the tokens.
    web_searches: int = 0

    @property
    def total_input(self) -> int:
        return self.uncached_input + self.cache_read + self.cache_write + self.cache_write_1h


def usage(message: Any) -> Usage | None:
    """A langchain AIMessage's usage, or None when it carries none."""
    meta = getattr(message, "usage_metadata", None)
    if not meta:
        return None
    response = getattr(message, "response_metadata", None) or {}
    model = response.get("model_name") or response.get("model") or "unknown"
    details = meta.get("input_token_details") or {}
    cache_read = details.get("cache_read", 0) or 0
    # The per-lifetime keys split the writes; without them, cache_creation is all 5-minute writes.
    write_5m = details.get("ephemeral_5m_input_tokens", 0) or 0
    write_1h = details.get("ephemeral_1h_input_tokens", 0) or 0
    if not write_5m and not write_1h:
        write_5m = details.get("cache_creation", 0) or 0
    # langchain-anthropic's input_tokens already includes cache reads and writes.
    uncached = max(0, (meta.get("input_tokens", 0) or 0) - cache_read - write_5m - write_1h)
    server_tools = ((response.get("usage") or {}).get("server_tool_use") or {}) if isinstance(response, dict) else {}
    searches = server_tools.get("web_search_requests", 0) or 0
    return Usage(model, uncached, meta.get("output_tokens", 0) or 0, cache_read, write_5m, write_1h, searches)


class Price(NamedTuple):
    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute cache writes
    cache_write_1h: float


# Haiku 5.5 charges more for a prompt over 100,000 tokens, counting every input token (cached ones included).
_LONG_PROMPT_TOKENS = 100_000

_PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4, 20, 0.20, 5, 8),
    "claude-opus-4-5": Price(5, 25, 0.50, 6.25, 10),
    "claude-opus-4-6": Price(5, 25, 0.50, 6.25, 10),
    "claude-opus-4": Price(15, 75, 1.50, 18.75, 30),
    "claude-sonnet-5-5": Price(2, 10, 0.10, 2.50, 4),
    "claude-sonnet-5": Price(2, 10, 0.20, 2.50, 4),
    "claude-sonnet-4": Price(3, 15, 0.30, 3.75, 6),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01, 0.125, 0.20),
    "claude-haiku-4": Price(1, 5, 0.10, 1.25, 2),
    "claude-3-5-haiku": Price(0.80, 4, 0.08, 1, 1.6),
}
_LONG_PROMPT_PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(0.50, 2.50, 0.05, 0.625, 1),
}
# The web search tool's fee: $10 per 1,000 searches, the same on every model (a failed search isn't billed or counted).
WEB_SEARCH_USD = 0.01
_unpriced: set[str] = set()


def price_for(model: str, prompt_tokens: int = 0) -> Price | None:
    """The price of a call to `model` whose prompt (every input token) is `prompt_tokens` long."""
    matches = [prefix for prefix in _PRICES if model.startswith(prefix)]
    if not matches:
        if model not in _unpriced:
            _unpriced.add(model)
            logger.warning("No price for model %r; its calls are counted at $0", model)
        return None
    prefix = max(matches, key=len)
    if prompt_tokens > _LONG_PROMPT_TOKENS and prefix in _LONG_PROMPT_PRICES:
        return _LONG_PROMPT_PRICES[prefix]
    return _PRICES[prefix]


class Cost(NamedTuple):
    usd: float
    saved_by_cache_usd: float  # what the cache reads would have cost as plain input, minus what they cost
    cache_write_extra_usd: float = 0.0  # what the cache writes cost above plain input

    @property
    def net_cache_saving_usd(self) -> float:
        return self.saved_by_cache_usd - self.cache_write_extra_usd


def cost(
    model: str, uncached_input: int, output: int, cache_read: int, cache_write: int, cache_write_1h: int = 0,
    web_searches: int = 0,
) -> Cost:
    searches_usd = web_searches * WEB_SEARCH_USD
    price = price_for(model, uncached_input + cache_read + cache_write + cache_write_1h)
    if price is None:
        return Cost(searches_usd, 0.0)
    usd = searches_usd + (
        uncached_input * price.input + output * price.output + cache_read * price.cache_read
        + cache_write * price.cache_write + cache_write_1h * price.cache_write_1h
    ) / 1_000_000
    return Cost(
        usd,
        cache_read * (price.input - price.cache_read) / 1_000_000,
        (cache_write * (price.cache_write - price.input) + cache_write_1h * (price.cache_write_1h - price.input)) / 1_000_000,
    )
