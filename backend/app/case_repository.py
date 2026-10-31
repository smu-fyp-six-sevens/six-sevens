from __future__ import annotations

import uuid
from datetime import datetime, timezone

from postgrest import CountMethod

from .models import AdditionalDocuments, CaseRecord
from .supabase_client import service_supabase_client, supabase_client
from .supabase_rows import fetch_all, row as _row


def create_case(
    company_name: str | None = None, *, checklist_enforced: bool = False, reviews_required: bool = True,
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
        "recommended_additional_documents": [],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    result = supabase_client.table("cases").insert(row).execute()

    return CaseRecord(**_row(result.data[0]))


def get_case(case_id: str) -> CaseRecord | None:
    result = supabase_client.table("cases").select("*").eq("id", case_id).execute()
    if not result.data:
        return None
    return CaseRecord(**_row(result.data[0]))


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
) -> tuple[list[CaseRecord], int]:
    """
    One page of cases by creation date, and how many cases match in all. `q` is a part of a number or name;
    `statuses`, when given, the stages to keep.
    """
    query = supabase_client.table("cases").select("*", count=CountMethod.exact)
    if q:
        query = query.or_(_matching(f"*{q}*"))
    if statuses:
        query = query.in_("status", statuses)
    result = query.order("created_at", desc=newest).order("id").range(offset, offset + limit - 1).execute()
    return [CaseRecord(**_row(item)) for item in result.data], result.count or 0


def list_matching_cases(q: str | None, statuses: list[str] | None = None) -> list[CaseRecord]:
    """Every matching case, newest first. Only for what the database can't sort or filter itself (risk)."""

    def build():
        query = supabase_client.table("cases").select("*")
        if statuses:
            query = query.in_("status", statuses)
        return query.or_(_matching(f"*{q}*")) if q else query

    cases = [CaseRecord(**item) for item in fetch_all(build, "id")]
    return sorted(cases, key=lambda case: case.created_at, reverse=True)


def list_case_ids() -> list[str]:
    return [item["id"] for item in fetch_all(lambda: supabase_client.table("cases").select("id"), "id")]


def list_case_statuses() -> list[str]:
    """Every case's stage, for the queue's counts."""
    return [str(item["status"]) for item in fetch_all(lambda: supabase_client.table("cases").select("id, status"), "id")]


def find_cases_starting_with(q: str, limit: int) -> list[CaseRecord]:
    """Newest first: the cases whose number or name begins with q."""
    result = (
        supabase_client.table("cases").select("*").or_(_matching(f"{q}*"))
        .order("created_at", desc=True).order("id").limit(limit).execute()
    )
    return [CaseRecord(**_row(item)) for item in result.data]


def find_case_numbered(q: str) -> CaseRecord | None:
    """The case whose number (or id) is exactly q, ignoring case."""
    quoted = _quoted(q)
    result = (
        supabase_client.table("cases").select("*")
        .or_(f"greenlink_case_id.ilike.{quoted},id.eq.{quoted}").limit(1).execute()
    )
    return CaseRecord(**_row(result.data[0])) if result.data else None


def update_case(case_id: str, company_name: str) -> CaseRecord | None:
    """A loan officer editing the case's own details (currently just company_name)."""
    update = {
        "company_name": company_name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
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
    result = service_supabase_client().table("cases").delete().eq("id", case_id).execute()
    if not result.data:
        raise RuntimeError(f"Case {case_id} was not deleted")


def set_recommended_additional_documents(case_id: str, additional: AdditionalDocuments | None) -> None:
    """Stores the latest complete analysis's whole list of documents to request on the case (jsonb); [] clears it."""
    value = additional.model_dump(mode="json") if additional else []
    supabase_client.table("cases").update({"recommended_additional_documents": value}).eq("id", case_id).execute()


def set_status(case_id: str, expected: str, status: str) -> bool:
    """
    Moves the case to a new stage only if it is still at `expected` — a
    compare-and-swap, so two processes syncing the same case can't both
    record the same change. Returns whether this call made the change.
    """
    result = (
        supabase_client.table("cases")
        .update({"status": status})
        .eq("id", case_id)
        .eq("status", expected)
        .execute()
    )
    return bool(result.data)
