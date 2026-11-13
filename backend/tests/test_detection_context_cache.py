"""The per-run cache the three specialists share (langgraph/detection/tools.py _once, prime, clear_context)."""

from __future__ import annotations

import asyncio

import pytest

from app.langgraph.detection import tools

DOC = "doc-cache"


@pytest.fixture(autouse=True)
def _clean():
    tools.clear_context(DOC)
    yield
    tools.clear_context(DOC)


async def test_callers_that_start_together_share_one_load() -> None:
    calls = 0

    async def load() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "the file"

    results = await asyncio.gather(*[tools._once(DOC, "file", load) for _ in range(5)])

    assert results == ["the file"] * 5 and calls == 1
    assert await tools._once(DOC, "file", load) == "the file" and calls == 1  # and a later caller reads it too


async def test_a_load_that_fails_is_tried_again_by_the_next_caller() -> None:
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("storage hiccup")
        return "the file"

    with pytest.raises(RuntimeError):
        await tools._once(DOC, "file", flaky)
    await asyncio.sleep(0)  # the failure is forgotten as the load finishes

    assert await tools._once(DOC, "file", flaky) == "the file" and attempts == 2


async def test_one_caller_giving_up_does_not_cancel_the_load_the_others_wait_on() -> None:
    release = asyncio.Event()

    async def slow() -> str:
        await release.wait()
        return "the file"

    impatient = asyncio.create_task(tools._once(DOC, "file", slow))
    patient = asyncio.create_task(tools._once(DOC, "file", slow))
    await asyncio.sleep(0)
    impatient.cancel()
    release.set()

    assert await patient == "the file"
    with pytest.raises(asyncio.CancelledError):
        await impatient


async def test_what_the_first_step_fetched_is_seeded_and_never_loaded_again() -> None:
    document = object()
    tools.prime(DOC, document=document, fields=[1, 2], guard_result={"risk": "none"})

    async def never() -> None:
        raise AssertionError("loaded what was already there")

    assert await tools._once(DOC, "document", never) is document
    assert await tools._once(DOC, "document_metadata", never) is document  # the metadata tool reads the same row
    assert await tools._once(DOC, "fields", never) == [1, 2]
    assert await tools._once(DOC, "saved_guard_result", never) == {"risk": "none"}


async def test_priming_again_does_not_replace_what_is_already_cached() -> None:
    tools.prime(DOC, fields=["first"])
    tools.prime(DOC, fields=["second"], document="doc")

    async def never() -> None:
        raise AssertionError("loaded")

    assert await tools._once(DOC, "fields", never) == ["first"]
    assert await tools._once(DOC, "document", never) == "doc"


async def test_clearing_a_run_forgets_it() -> None:
    tools.prime(DOC, fields=["old"])
    tools.clear_context(DOC)

    async def load() -> list[str]:
        return ["new"]

    assert await tools._once(DOC, "fields", load) == ["new"]
