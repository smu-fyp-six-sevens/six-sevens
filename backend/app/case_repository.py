from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Iterator

from postgrest import CountMethod

from .models import AdditionalDocuments, CaseRecord
from .supabase_client import service_supabase_client, supabase_client
from .supabase_rows import fetch_all, row as _row

# A request reads its case once: the router's dependency, the readiness check, the stage sync and the snapshot all
# ask for it. Kept for _REQUEST_CASE_SECONDS only (a push handler can run for minutes), and dropped on any write here.
_REQUEST_CASE_SECONDS = 2.0
_request_cases: ContextVar[dict[str, tuple[float, CaseRecord | None]] | None] = ContextVar(
    "_request_cases", default=None,
)


@contextmanager
def request_scope() -> Iterator[None]:
    """Shares case reads within one request (main.py's middleware)."""
    token = _request_cases.set({})
    try:
        yield
    finally:
        _request_cases.reset(token)


def _forget(case_id: str) -> None:
    cached = _request_cases.get()
    if cached is not None:
        cached.pop(case_id, None)


def create_case(
    company_name: str | None = None, *, checklist_enforced: bool = False, reviews_required: bool = True,
    created_by: str | None = None,
) -> CaseRecord:
    now = datetime.now(timezone.utc)

    # greenlink_case_id is intentionally omitted — it's assigned by a DB
    # trigger (`set_greenlink_case_id`, which skips an id already taken) and
    # comes back populated in the inserted row's representation below.
    row = {
        "id": str(uuid.uuid4()),
        "company_name": company_name,
        "status": "open",
        "checklist_enforced": checklist_enforced,
        "reviews_required": reviews_required,
        "created_by": created_by,
        "recommended_additional_documents": [],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    result = supabase_client.table("cases").insert(row).execute()

    return CaseRecord(**_row(result.data[0]))


def get_case(case_id: str) -> CaseRecord | None:
    cached = _request_cases.get()
    hit = cached.get(case_id) if cached is not None else None
    if hit is not None and time.monotonic() - hit[0] < _REQUEST_CASE_SECONDS:
        return hit[1].model_copy(deep=True) if hit[1] is not None else None
    result = supabase_client.table("cases").select("*").eq("id", case_id).execute()
    case = CaseRecord(**_row(result.data[0])) if result.data else None
    if cached is not None:
        cached[case_id] = (time.monotonic(), case.model_copy(deep=True) if case is not None else None)
    return case


def get_case_by_number(number: str) -> CaseRecord | None:
    """By the number officers see (GLDB-137); the id also works, for a case that has no number."""
    for column in ("greenlink_case_id", "id"):
        result = supabase_client.table("cases").select("*").eq(column, number).limit(1).execute()
        if result.data:
            return CaseRecord(**_row(result.data[0]))
    return None


def _quoted(value: str) -> str:
    """A value inside a PostgREST filter list, double-quoted so commas and brackets in it aren't read as syntax."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _matching(pattern: str) -> str:
    """The case number or the company name matches the pattern (* is the wildcard; ilike ignores case)."""
    quoted = _quoted(pattern)
    return f"company_name.ilike.{quoted},greenlink_case_id.ilike.{quoted}"


def list_cases_page(
    q: str | None, *, newest: bool, limit: int, offset: int, statuses: list[str] | None = None,
    risk_ranks: list[int] | None = None, highest_risk_first: bool | None = None,
) -> tuple[list[CaseRecord], int]:
    """
    One page of cases, and how many cases match in all. `q` is a part of a number or name; `statuses`, when given,
    the stages to keep; `risk_ranks`, the saved risk ranks to keep (cases.risk_rank). Ordered by risk rank first
    when `highest_risk_first` is set, then by creation date.
    """
    query = supabase_client.table("cases").select("*", count=CountMethod.exact)
    if q:
        query = query.or_(_matching(f"*{q}*"))
    if statuses:
        query = query.in_("status", statuses)
    if risk_ranks:
        query = query.in_("risk_rank", risk_ranks)
    if highest_risk_first is not None:
        query = query.order("risk_rank", desc=highest_risk_first)
    result = query.order("created_at", desc=newest).order("id").range(offset, offset + limit - 1).execute()
    return [CaseRecord(**_row(item)) for item in result.data], result.count or 0


def list_unfinalised_case_ids() -> list[str]:
    """Every case whose stage can still move: a finalised case's never does."""
    return [
        item["id"]
        for item in fetch_all(lambda: supabase_client.table("cases").select("id").neq("status", "finalised"), "id")
    ]


def count_cases_by_status() -> dict[str, int]:
    """How many cases are at each stage, counted by the database (the case_stage_counts view)."""
    rows = supabase_client.table("case_stage_counts").select("status, cases").execute().data or []
    return {str(_row(item)["status"]): int(_row(item)["cases"]) for item in rows}


def find_cases_starting_with(q: str, limit: int) -> list[CaseRecord]:
    """Newest first: the cases whose number or name begins with q."""
    result = (
        supabase_client.table("cases").select("*").or_(_matching(f"{q}*"))
        .order("created_at", desc=True).order("id").limit(limit).execute()
    )
    return [CaseRecord(**_row(item)) for item in result.data]


def find_case_numbered(q: str) -> CaseRecord | None:
    """The case whose number (or id) is exactly q, ignoring case. Numbers are upper case (GLDB-…), ids lower."""
    result = (
        supabase_client.table("cases").select("*")
        .or_(f"greenlink_case_id.eq.{_quoted(q.upper())},id.eq.{_quoted(q.lower())}").limit(1).execute()
    )
    return CaseRecord(**_row(result.data[0])) if result.data else None


def update_case(case_id: str, company_name: str) -> CaseRecord | None:
    """A loan officer editing the case's own details (currently just company_name)."""
    update = {
        "company_name": company_name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _forget(case_id)
    result = (
        supabase_client.table("cases")
        .update(update)
        .eq("id", case_id)
        .execute()
    )
    if not result.data:
        return None
    return CaseRecord(**_row(result.data[0]))


def delete_case(case_id: str) -> None:
    """
    Removes the case's row; the database cascades it to everything keyed by the case (documents, analysis runs,
    risk scores, findings, notes: backend/sql/schema.sql). Callers delete its documents first, for their stored
    files. audit_events stays.

    Uses the service client so row-level security cannot turn this into a
    no-op that still looks successful. An empty result means the row is
    still there.
    """
    _forget(case_id)
    result = service_supabase_client().table("cases").delete().eq("id", case_id).execute()
    if not result.data:
        raise RuntimeError(f"Case {case_id} was not deleted")


def set_recommended_additional_documents(case_id: str, additional: AdditionalDocuments | None) -> None:
    """Stores the latest complete analysis's whole list of documents to request on the case (jsonb); [] clears it."""
    value = additional.model_dump(mode="json") if additional else []
    _forget(case_id)
    supabase_client.table("cases").update({"recommended_additional_documents": value}).eq("id", case_id).execute()


def set_risk_rank(case_id: str, risk_rank: int) -> None:
    """The latest saved risk as a rank (risk/store.rank), kept on the row so the case list sorts and filters by it."""
    _forget(case_id)
    supabase_client.table("cases").update({"risk_rank": risk_rank}).eq("id", case_id).execute()


def set_processing_ms(case_id: str, processing_ms: int) -> None:
    """The case's automated processing time, kept on the row for the case list (audit.refresh_processing_time)."""
    _forget(case_id)
    supabase_client.table("cases").update({"processing_ms": processing_ms}).eq("id", case_id).execute()


def set_status(case_id: str, expected: str, status: str) -> bool:
    """
    Moves the case to a new stage only if it is still at `expected` — a
    compare-and-swap, so two processes syncing the same case can't both
    record the same change. Returns whether this call made the change.
    """
    _forget(case_id)
    result = (
        supabase_client.table("cases")
        .update({"status": status})
        .eq("id", case_id)
        .eq("status", expected)
        .execute()
    )
    return bool(result.data)
