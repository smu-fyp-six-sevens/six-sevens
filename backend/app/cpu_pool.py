"""
Executor for PDF/OCR/forensic work, kept off the default pool that every Supabase call uses.
One worker by default: PyMuPDF isn't thread-safe, even across separate Document objects.
"""

from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from .config import settings

T = TypeVar("T")

_executor = ThreadPoolExecutor(
    max_workers=settings.CPU_WORKER_CONCURRENCY, thread_name_prefix="cpu-work"
)


async def run_cpu(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, functools.partial(fn, *args, **kwargs))
