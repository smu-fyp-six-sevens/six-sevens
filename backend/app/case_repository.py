from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional, cast

from .config import settings
from .models import CaseRecord
from .supabase_client import service_supabase_client, supabase_client


def _row(mapping: Any) -> dict:
    """postgrest types a row as a loose JSON union rather than dict[str, Any]
    — every row here is one we (or our own migrations) wrote, so this cast
    just tells the type checker what's already true."""
    return cast(dict, mapping)


def create_case(company_name: Optional[str] = None) -> CaseRecord:
    now = datetime.now(timezone.utc)

    # greenlink_case_id is intentionally omitted — it's assigned by a DB
    # trigger (see migrations/001_add_greenlink_case_id_generation.sql) and
    # comes back populated in the inserted row's representation below.
    # dev is driven by which process created it (see settings.DEPLOYMENT_ENV
    # / IS_CLOUD_DEPLOYMENT), never set by the caller or flipped by hand —
    # cloud always creates real (dev=False) cases, everything else creates
    # dev=True ones, so local/test data never needs manual cleanup to stay
    # out of the cloud demo's queue.
    row = {
        "id": str(uuid.uuid4()),
        "company_name": company_name,
        "status": "open",
        "submission_finalized": False,
        "recommended_additional_documents": [],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "dev": not settings.IS_CLOUD_DEPLOYMENT,
    }

    result = supabase_client.table("cases").insert(row).execute()

    return CaseRecord(**_row(result.data[0]))


def get_case(case_id: str) -> Optional[CaseRecord]:
    result = supabase_client.table("cases").select("*").eq("id", case_id).execute()
    if not result.data:
        return None
    return CaseRecord(**_row(result.data[0]))


def list_cases(*, include_dev: bool = True) -> list[CaseRecord]:
    query = supabase_client.table("cases").select("*")
    if not include_dev:
        query = query.eq("dev", False)
    result = query.execute()
    return [CaseRecord(**_row(row)) for row in result.data]


def update_case(case_id: str, company_name: str) -> Optional[CaseRecord]:
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
    Removes the case row. Callers delete its documents first.

    Uses the service client so row-level security cannot turn this into a
    no-op that still looks successful. An empty result means the row is
    still there.
    """
    result = service_supabase_client().table("cases").delete().eq("id", case_id).execute()
    if not result.data:
        raise RuntimeError(f"Case {case_id} was not deleted")


def set_submission_finalized(case_id: str, finalized: bool) -> None:
    """
    Kept in sync with actual requirement coverage rather than being a
    one-way, officer-triggered action — see case_service.sync_submission_finalized,
    called right after every classification result is recorded. Reversible:
    if a later reclassification (e.g. via the /retry endpoint) drops
    coverage back below 100%, this flips back to False/None automatically.
    """
    update = {
        "submission_finalized": finalized,
        "finalized_at": datetime.now(timezone.utc).isoformat() if finalized else None,
    }
    supabase_client.table("cases").update(update).eq("id", case_id).execute()
