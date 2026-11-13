"""
An in-memory Supabase client for repository tests: tables are lists of rows, and a query runs against them the way
PostgREST would, so a test asserts what was stored and read back rather than which mock was called.

It answers the part of the query builder the repositories use:
* select with "*", columns, "alias:column", JSON paths ("result->risk->score") and an inner join to another table
  ("cases!inner(id)"), which joins on this table's "<table, singular>_id" column (findings.case_id -> cases.id) and
  lets a filter name the joined table's columns ("cases.created_at");
* eq, neq, is_ (only "null"), not_.is_, gte, lt, in_, filter(path, "cs", json) for JSON containment, order, limit, range;
* insert, update and delete (each returning the rows it wrote, as PostgREST does with return=representation), upsert
  on the table's key (or on_conflict), updating only the columns given;
* rpc, answered by a function the test registers.

Anything else raises AttributeError, so a repository using more than this fails loudly rather than passing.
"""
from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

Row = dict[str, Any]


@dataclass
class Result:
    data: Any
    count: int | None = None  # the rows the query matched before limit and range, when select asked for a count


def _path(row: Row, path: str) -> Any:
    """A column, or a JSON path into one ("evidence->document_ids")."""
    value: Any = row
    for key in path.replace("->>", "->").split("->"):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _split_top_level(columns: str) -> list[str]:
    """The select list's items, leaving the commas inside an embedded table's brackets alone."""
    items, depth, current = [], 0, ""
    for char in columns:
        if char == "," and depth == 0:
            items.append(current.strip())
            current = ""
            continue
        depth += {"(": 1, ")": -1}.get(char, 0)
        current += char
    return [item for item in [*items, current.strip()] if item]


class Query:
    def __init__(self, client: FakeSupabase, table: str) -> None:
        self._client = client
        self._table = table
        self._action = "select"
        self._columns = "*"
        self._joins: list[str] = []
        self._filters: list[Callable[[Row], bool]] = []
        self._order: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._range: tuple[int, int] | None = None
        self._values: Any = None
        self._on_conflict: tuple[str, ...] | None = None
        self._negate_next = False
        self._count = False

    # --- what the query does ---------------------------------------------------------------------------

    def select(self, columns: str = "*", count: Any = None) -> Query:
        self._count = count is not None
        self._columns = columns
        self._joins = [item.split("!")[0] for item in _split_top_level(columns) if "!inner(" in item]
        return self

    def insert(self, values: Row | list[Row]) -> Query:
        self._action, self._values = "insert", values
        return self

    def update(self, values: Row) -> Query:
        self._action, self._values = "update", values
        return self

    def upsert(self, values: Row | list[Row], on_conflict: str | None = None) -> Query:
        self._action, self._values = "upsert", values
        self._on_conflict = tuple(on_conflict.split(",")) if on_conflict else None
        return self

    def delete(self) -> Query:
        self._action = "delete"
        return self

    # --- filters ---------------------------------------------------------------------------------------

    @property
    def not_(self) -> Query:
        self._negate_next = True
        return self

    def _where(self, test: Callable[[Row], bool]) -> Query:
        negate, self._negate_next = self._negate_next, False
        self._filters.append((lambda row: not test(row)) if negate else test)
        return self

    def _value(self, row: Row, column: str) -> Any:
        """A column of the row, or of the row it joins to ("cases.status")."""
        table, _, rest = column.partition(".")
        if rest and table in self._joins:
            joined = self._client.joined(self._table, table, row)
            return _path(joined, rest) if joined is not None else None
        return _path(row, column)

    def eq(self, column: str, value: Any) -> Query:
        return self._where(lambda row: self._value(row, column) == value)

    def neq(self, column: str, value: Any) -> Query:
        return self._where(lambda row: self._value(row, column) != value)

    def is_(self, column: str, value: str) -> Query:
        if value != "null":
            raise AttributeError(f"is_ supports only 'null', not {value!r}")
        return self._where(lambda row: self._value(row, column) is None)

    def gte(self, column: str, value: Any) -> Query:
        return self._where(lambda row: (v := self._value(row, column)) is not None and v >= value)

    def lt(self, column: str, value: Any) -> Query:
        return self._where(lambda row: (v := self._value(row, column)) is not None and v < value)

    def in_(self, column: str, values: list[Any]) -> Query:
        return self._where(lambda row: self._value(row, column) in values)

    def filter(self, column: str, operator: str, value: str) -> Query:
        if operator != "cs":
            raise AttributeError(f"filter supports only 'cs', not {operator!r}")
        wanted = json.loads(value)
        return self._where(lambda row: all(item in (self._value(row, column) or []) for item in wanted))

    # --- shape of the result ---------------------------------------------------------------------------

    def order(self, column: str, desc: bool = False) -> Query:
        self._order.append((column, desc))
        return self

    def limit(self, count: int) -> Query:
        self._limit = count
        return self

    def range(self, start: int, end: int) -> Query:
        self._range = (start, end)
        return self

    def _project(self, row: Row) -> Row:
        if self._columns.strip() == "*":
            return copy.deepcopy(row)
        projected: Row = {}
        for item in _split_top_level(self._columns):
            if "(" in item:  # an embedded table: joined on, not returned
                continue
            alias, _, path = item.rpartition(":")
            projected[alias or path.split("->")[-1]] = copy.deepcopy(_path(row, path))
        return projected

    def execute(self) -> Result:
        rows = self._client.tables.setdefault(self._table, [])
        if self._action == "insert":
            return Result([copy.deepcopy(self._client.add(self._table, values)) for values in self._listed()])
        if self._action == "upsert":
            return Result([copy.deepcopy(self._upsert_one(values)) for values in self._listed()])
        matched = [row for row in rows if all(test(row) for test in self._filters)]
        if self._joins:
            matched = [row for row in matched if all(self._client.joined(self._table, t, row) for t in self._joins)]
        if self._action == "update":
            for row in matched:
                row.update(copy.deepcopy(self._values))
            return Result(copy.deepcopy(matched))
        if self._action == "delete":
            self._client.tables[self._table] = [row for row in rows if row not in matched]
            return Result(copy.deepcopy(matched))
        for column, desc in reversed(self._order):
            matched.sort(key=lambda row, column=column: (self._value(row, column) is None, self._value(row, column)), reverse=desc)
        total = len(matched)
        if self._range is not None:
            matched = matched[self._range[0] : self._range[1] + 1]
        if self._limit is not None:
            matched = matched[: self._limit]
        return Result([self._project(row) for row in matched], total if self._count else None)

    def _listed(self) -> list[Row]:
        return self._values if isinstance(self._values, list) else [self._values]

    def _upsert_one(self, values: Row) -> Row:
        key = self._on_conflict or self._client.keys.get(self._table, ("id",))
        for row in self._client.tables[self._table]:
            if all(row.get(column) == values.get(column) for column in key):
                row.update(copy.deepcopy(values))
                return row
        return self._client.add(self._table, values)


class FakeSupabase:
    """
    keys: a table's primary key columns, for upsert (default "id").
    defaults: a table's column defaults, applied to an inserted row's missing columns (an "id" is always generated
    when the row has none, and created_at set to now).
    """

    def __init__(
        self, *, keys: dict[str, tuple[str, ...]] | None = None, defaults: dict[str, Row] | None = None,
    ) -> None:
        self.tables: dict[str, list[Row]] = {}
        self.keys = keys or {}
        self.defaults = defaults or {}
        self.functions: dict[str, Callable[[Row], Any]] = {}
        self.rpc_calls: list[tuple[str, Row]] = []

    def table(self, name: str) -> Query:
        return Query(self, name)

    def rpc(self, name: str, params: Row) -> Query:
        """Calls the function the test registered under `name`; its return value is the result's data."""
        self.rpc_calls.append((name, copy.deepcopy(params)))
        query = Query(self, name)
        query.execute = lambda: Result(self.functions[name](params))  # type: ignore[method-assign]
        return query

    def add(self, table: str, values: Row) -> Row:
        row = {
            "id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
            **copy.deepcopy(self.defaults.get(table, {})), **copy.deepcopy(values),
        }
        self.tables.setdefault(table, []).append(row)
        return row

    def joined(self, table: str, other: str, row: Row) -> Row | None:
        """The row of `other` this row points to through its "<other, singular>_id" column."""
        foreign_key = f"{other.removesuffix('s')}_id"
        return next((r for r in self.tables.get(other, []) if r.get("id") == row.get(foreign_key)), None)

    def rows(self, table: str, **match: Any) -> list[Row]:
        """The stored rows of a table, optionally only those whose columns equal `match`."""
        return [row for row in self.tables.get(table, []) if all(row.get(k) == v for k, v in match.items())]
