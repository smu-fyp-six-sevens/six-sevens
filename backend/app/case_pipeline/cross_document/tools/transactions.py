"""Tools that measure how money moves in a bank statement."""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from langchain_core.tools import tool

from ....fraud_detection.numerical_validation.common import _parse_field_date
from ..evidence import mask_identifiers, signed_amount, transaction_summary
from ...risk.supporting_documents import _counterparty_from_description
from .common import (
    MAX_ROWS, ToolKit, direction_ok, in_range, row_date, statement_period, statement_rows, unavailable,
)

_NO_TRANSACTIONS = "This document has no transactions."


def _counterparty_name(row: dict) -> str:
    """The counterparty a row names, or the one printed in its description."""
    return str(row.get("counterparty") or _counterparty_from_description(str(row.get("description") or "")) or "")


def build_transaction_tools(kit: ToolKit) -> list:
    context = kit.context

    @tool
    async def transaction_stats(
        document: str, direction: str = "any", date_from: Optional[str] = None, date_to: Optional[str] = None,
        is_refutation_probe: bool = False,
    ) -> str:
        """Count, totals, median, 90th percentile, max and min transaction size for a statement. direction is any, credit or debit."""
        view, error = kit.doc(document)
        args = {"document": document, "direction": direction, "date_from": date_from, "date_to": date_to}
        if view is None:
            return kit.log("transaction_stats", args, error or {}, is_refutation_probe)
        rows = statement_rows(view)
        if rows is None:
            return kit.log("transaction_stats", args, unavailable(_NO_TRANSACTIONS), is_refutation_probe, view.key)
        start, end, period = _parse_field_date(date_from), _parse_field_date(date_to), statement_period(view)
        chosen = [r for r in rows if (a := signed_amount(r)) is not None and direction_ok(a, direction)
                  and in_range(r, start, end, period)]
        summary = transaction_summary(chosen)
        ref = context.ledger.issue(view.key, f"transaction_stats[{direction}]", summary, source="transaction_stats")
        return kit.log("transaction_stats", args, {"available": True, "stats": summary, "ref": ref,
                                                   "rows_scanned": len(rows)}, is_refutation_probe, view.key, [ref])

    @tool
    async def query_transactions(
        document: str, direction: str = "any", min_amount: Optional[float] = None, max_amount: Optional[float] = None,
        description_contains: Optional[str] = None, counterparty: Optional[str] = None,
        date_from: Optional[str] = None, date_to: Optional[str] = None, limit: int = 10,
        is_refutation_probe: bool = False,
    ) -> str:
        """Find transactions by direction, absolute amount range, description text, counterparty or date. Returns the true match count and the largest matches, each with a ref id."""
        view, error = kit.doc(document)
        args = {"document": document, "direction": direction, "min_amount": min_amount, "max_amount": max_amount,
                "description_contains": description_contains, "counterparty": counterparty,
                "date_from": date_from, "date_to": date_to}
        if view is None:
            return kit.log("query_transactions", args, error or {}, is_refutation_probe)
        rows = statement_rows(view)
        if rows is None:
            return kit.log("query_transactions", args, unavailable(_NO_TRANSACTIONS), is_refutation_probe, view.key)
        start, end, period = _parse_field_date(date_from), _parse_field_date(date_to), statement_period(view)
        matches = []
        for index, row in enumerate(rows):
            amount = signed_amount(row)
            if amount is None or not direction_ok(amount, direction) or not in_range(row, start, end, period):
                continue
            if min_amount is not None and abs(amount) < min_amount:
                continue
            if max_amount is not None and abs(amount) > max_amount:
                continue
            if description_contains and description_contains.lower() not in str(row.get("description") or "").lower():
                continue
            name = _counterparty_name(row)
            if counterparty and counterparty.lower() not in name.lower():
                continue
            matches.append((index, row, amount, name))
        matches.sort(key=lambda m: -abs(m[2]))
        shown, refs = [], []
        for index, row, amount, name in matches[: max(1, min(limit, MAX_ROWS))]:
            ref = context.ledger.issue(view.key, "transactions", row, row=index, source="query_transactions")
            refs.append(ref)
            shown.append({"ref": ref, "date": row.get("date"), "amount": amount,
                          "description": mask_identifiers(str(row.get("description") or ""))[:120],
                          "counterparty": name or None})
        return kit.log("query_transactions", args, {"available": True, "match_count": len(matches),
                       "rows_scanned": len(rows), "rows": shown}, is_refutation_probe, view.key, refs)

    @tool
    async def top_counterparties(
        document: str, direction: str = "any", n: int = 5, is_refutation_probe: bool = False,
    ) -> str:
        """Who the money goes to or comes from most: count, total and share of volume per counterparty."""
        view, error = kit.doc(document)
        args = {"document": document, "direction": direction, "n": n}
        if view is None:
            return kit.log("top_counterparties", args, error or {}, is_refutation_probe)
        rows = statement_rows(view)
        if rows is None:
            return kit.log("top_counterparties", args, unavailable(_NO_TRANSACTIONS), is_refutation_probe, view.key)
        totals: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
        volume = 0.0
        for row in rows:
            amount = signed_amount(row)
            if amount is None or not direction_ok(amount, direction):
                continue
            name = _counterparty_name(row) or "(unnamed)"
            totals[name][0] += 1
            totals[name][1] += abs(amount)
            volume += abs(amount)
        ranked = sorted(totals.items(), key=lambda kv: -kv[1][1])[: max(1, min(n, MAX_ROWS))]
        out = [{"counterparty": mask_identifiers(name), "count": int(c), "total": round(t, 2),
                "share_of_volume": round(t / volume, 3) if volume else None} for name, (c, t) in ranked]
        ref = context.ledger.issue(view.key, f"top_counterparties[{direction}]", out, source="top_counterparties")
        return kit.log("top_counterparties", args, {"available": True, "counterparties": out, "ref": ref},
                       is_refutation_probe, view.key, [ref])

    @tool
    async def monthly_flow(document: str, is_refutation_probe: bool = False) -> str:
        """Credits, debits and transaction count per month for a statement."""
        view, error = kit.doc(document)
        args = {"document": document}
        if view is None:
            return kit.log("monthly_flow", args, error or {}, is_refutation_probe)
        rows = statement_rows(view)
        if rows is None:
            return kit.log("monthly_flow", args, unavailable(_NO_TRANSACTIONS), is_refutation_probe, view.key)
        months: dict[str, dict[str, float]] = defaultdict(lambda: {"credits": 0.0, "debits": 0.0, "count": 0})
        period = statement_period(view)
        undated = year_from_period = 0
        for row in rows:
            amount, day = signed_amount(row), row_date(row, period)
            if amount is None:
                continue
            if day is None:
                undated += 1
                continue
            if _parse_field_date(row.get("date")) is None:
                year_from_period += 1
            bucket = months[f"{day.year}-{day.month:02d}"]
            bucket["credits" if amount > 0 else "debits"] += abs(amount)
            bucket["count"] += 1
        out = {m: {k: round(v, 2) for k, v in b.items()} for m, b in sorted(months.items())}
        ref = context.ledger.issue(view.key, "monthly_flow", out, source="monthly_flow")
        result: dict = {"available": True, "months": out, "undated_rows": undated, "ref": ref}
        if year_from_period:
            result["year_taken_from_statement_period"] = year_from_period
        if undated and period == (None, None):
            result["note"] = "Rows print no year and the statement has no period, so they could not be dated."
        return kit.log("monthly_flow", args, result, is_refutation_probe, view.key, [ref])

    return [transaction_stats, query_transactions, top_counterparties, monthly_flow]
