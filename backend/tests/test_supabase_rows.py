from __future__ import annotations

from unittest.mock import MagicMock, patch

from app import supabase_rows


def _builder(pages: list[list[dict]]) -> MagicMock:
    builder = MagicMock()
    builder.order.return_value.range.return_value.execute.side_effect = [MagicMock(data=page) for page in pages]
    return builder


def test_fetch_all_reads_page_after_page_until_one_comes_back_short() -> None:
    builder = _builder([[{"id": 1}, {"id": 2}], [{"id": 3}, {"id": 4}], [{"id": 5}]])
    with patch.object(supabase_rows, "PAGE", 2):
        rows = supabase_rows.fetch_all(lambda: builder, "id")

    assert [r["id"] for r in rows] == [1, 2, 3, 4, 5]
    assert [c.args for c in builder.order.return_value.range.call_args_list] == [(0, 1), (2, 3), (4, 5)]
    builder.order.assert_called_with("id")


def test_fetch_all_stops_after_one_page_when_it_is_short() -> None:
    builder = _builder([[{"id": 1}]])
    assert supabase_rows.fetch_all(lambda: builder, "id") == [{"id": 1}]
    assert builder.order.return_value.range.call_count == 1


def test_fetch_all_asks_again_after_a_full_last_page() -> None:
    builder = _builder([[{"id": 1}, {"id": 2}], []])
    with patch.object(supabase_rows, "PAGE", 2):
        assert len(supabase_rows.fetch_all(lambda: builder, "id")) == 2

    assert builder.order.return_value.range.call_count == 2
