from __future__ import annotations

from app.langgraph.cache_gate import PromptCacheGate


async def test_run_calls_through_for_a_first_use_of_a_key() -> None:
    gate = PromptCacheGate()
    calls = []

    async def call():
        calls.append(1)
        return "result"

    result = await gate.run("key-1", call)

    assert result == "result"
    assert calls == [1]


async def test_run_lets_a_second_caller_through_once_the_first_has_warmed_the_key() -> None:
    gate = PromptCacheGate()
    calls = []

    async def call():
        calls.append(1)
        return "result"

    await gate.run("key-1", call)
    second_result = await gate.run("key-1", call)

    assert second_result == "result"
    assert calls == [1, 1]


async def test_run_sets_the_key_warm_even_when_the_first_call_fails() -> None:
    gate = PromptCacheGate()

    async def failing_call():
        raise RuntimeError("boom")

    async def succeeding_call():
        return "ok"

    try:
        await gate.run("key-1", failing_call)
    except RuntimeError:
        pass

    # A later caller for the same key must not be wedged waiting forever.
    result = await gate.run("key-1", succeeding_call)
    assert result == "ok"


async def test_claim_first_is_true_only_once_per_key_state() -> None:
    from app.langgraph.cache_gate import _KeyState

    state = _KeyState()

    assert await state.claim_first() is True
    assert await state.claim_first() is False


def test_reset_drops_every_learned_key() -> None:
    gate = PromptCacheGate()
    gate._keys["key-1"]

    gate.reset()

    assert gate._keys == {}
