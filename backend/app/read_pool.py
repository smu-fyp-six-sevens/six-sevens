"""A shared pool for running one request's independent Supabase reads side by side instead of one after another."""

from __future__ import annotations

import concurrent.futures
import contextvars
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="case-reads")


def submit(fn: Callable[..., T], *args: Any, **kwargs: Any) -> concurrent.futures.Future[T]:
    """In a copy of the caller's context, so the read still counts in its perf log (query_stats)."""
    return _pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)
