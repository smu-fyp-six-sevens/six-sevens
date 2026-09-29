from __future__ import annotations

from app.langgraph import text_cache


def test_put_stores_text_and_returns_its_hash() -> None:
    returned_hash = text_cache.put("doc-1", "Net pay: 5000")

    assert returned_hash == text_cache.text_hash("Net pay: 5000")
    assert text_cache.get("doc-1") == "Net pay: 5000"

    text_cache.discard("doc-1")


def test_text_hash_is_deterministic_and_short() -> None:
    first = text_cache.text_hash("same text")
    second = text_cache.text_hash("same text")

    assert first == second
    assert len(first) == 16


def test_get_returns_none_for_an_unknown_document() -> None:
    assert text_cache.get("never-put-anything-here") is None


def test_discard_removes_a_cached_entry_without_raising_if_absent() -> None:
    text_cache.put("doc-2", "some raw text")

    text_cache.discard("doc-2")
    assert text_cache.get("doc-2") is None

    # Discarding again (already gone) must not raise.
    text_cache.discard("doc-2")
