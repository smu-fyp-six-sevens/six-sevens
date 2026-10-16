"""
Cache breakpoint coordinator: parallel identical requests can all miss the
prompt cache, so one caller per distinct prompt goes first.

Anthropic's prompt cache is written by a request as it completes; a second
request that started concurrently with the first (before that write lands)
has nothing to read yet and also misses. With QUEUE_WORKER_CONCURRENCY
workers pulling a freshly-uploaded batch off the queue at once, every worker
can hit the same static system prompt (llm_classify, the classifier agent, a
given document_type's extraction schema) in the same few hundred
milliseconds — exactly the case that defeats caching.

The fix here is not a queue or a rate limiter: for each distinct static
prompt (`key`), exactly one caller is let through immediately (the "first
document alone" call, which pays for the cache write); every other caller
that arrives before that first call finishes waits for it, then proceeds
once the cache is warm. Once a key has been served once, `run` never blocks
for it again — this only ever costs latency on a key's very first use.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class _KeyState:
    def __init__(self) -> None:
        self._claim_lock = asyncio.Lock()
        self._claimed = False
        self.warm = asyncio.Event()

    async def claim_first(self) -> bool:
        async with self._claim_lock:
            if self._claimed:
                return False
            self._claimed = True
            return True


class PromptCacheGate:
    def __init__(self) -> None:
        self._keys: dict[str, _KeyState] = defaultdict(_KeyState)

    async def run(self, key: str, call: Callable[[], Awaitable[T]]) -> T:
        state = self._keys[key]
        if await state.claim_first():
            try:
                return await call()
            finally:
                # Set even on failure: a failed first call must not wedge
                # every later caller for this key waiting forever.
                state.warm.set()
        await state.warm.wait()
        return await call()

    def reset(self) -> None:
        """Test-only: drop all learned keys."""
        self._keys.clear()


cache_gate = PromptCacheGate()
