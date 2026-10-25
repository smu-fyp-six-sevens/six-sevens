"""
Claude list prices in USD per million tokens (anthropic.com/pricing), for the cost metrics and trace attributes.
Matched by the longest model-name prefix; a model missing here costs 0 and is logged once.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


class Usage(NamedTuple):
    model: str
    uncached_input: int
    output: int
    cache_read: int
    cache_write: int

    @property
    def total_input(self) -> int:
        return self.uncached_input + self.cache_read + self.cache_write


def usage(message: Any) -> Usage | None:
    """A langchain AIMessage's usage, or None when it carries none."""
    meta = getattr(message, "usage_metadata", None)
    if not meta:
        return None
    response = getattr(message, "response_metadata", None) or {}
    model = response.get("model_name") or response.get("model") or "unknown"
    details = meta.get("input_token_details") or {}
    cache_read = details.get("cache_read", 0) or 0
    # The API may report a write only under its per-lifetime keys, leaving the total at 0.
    cache_write = max(
        details.get("cache_creation", 0) or 0,
        (details.get("ephemeral_5m_input_tokens", 0) or 0) + (details.get("ephemeral_1h_input_tokens", 0) or 0),
    )
    # langchain-anthropic's input_tokens already includes cache reads and writes.
    uncached = max(0, (meta.get("input_tokens", 0) or 0) - cache_read - cache_write)
    return Usage(model, uncached, meta.get("output_tokens", 0) or 0, cache_read, cache_write)


class Price(NamedTuple):
    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute cache writes, the only kind this app makes


_PRICES: dict[str, Price] = {
    "claude-opus-4-5": Price(5, 25, 0.50, 6.25),
    "claude-opus-4-6": Price(5, 25, 0.50, 6.25),
    "claude-opus-4": Price(15, 75, 1.50, 18.75),
    "claude-sonnet-4": Price(3, 15, 0.30, 3.75),
    "claude-sonnet-5": Price(3, 15, 0.30, 3.75),
    "claude-haiku-4": Price(1, 5, 0.10, 1.25),
    "claude-haiku-5": Price(1, 5, 0.10, 1.25),
    "claude-3-5-haiku": Price(0.80, 4, 0.08, 1),
}
_unpriced: set[str] = set()


def price_for(model: str) -> Price | None:
    matches = [prefix for prefix in _PRICES if model.startswith(prefix)]
    if not matches:
        if model not in _unpriced:
            _unpriced.add(model)
            logger.warning("No price for model %r; its calls are counted at $0", model)
        return None
    return _PRICES[max(matches, key=len)]


class Cost(NamedTuple):
    usd: float
    saved_by_cache_usd: float  # what the cache reads would have cost as plain input, minus what they cost


def cost(model: str, uncached_input: int, output: int, cache_read: int, cache_write: int) -> Cost:
    price = price_for(model)
    if price is None:
        return Cost(0.0, 0.0)
    usd = (
        uncached_input * price.input + output * price.output
        + cache_read * price.cache_read + cache_write * price.cache_write
    ) / 1_000_000
    return Cost(usd, cache_read * (price.input - price.cache_read) / 1_000_000)
