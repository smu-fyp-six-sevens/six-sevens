from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .config import settings
from .models import CaseRecord
from .supabase_client import service_supabase_client, supabase_client
from .supabase_rows import row as _row


def create_case(company_name: str | None = None) -> CaseRecord:
    now = datetime.now(timezone.utc)

    # greenlink_case_id is intentionally omitted — it's assigned by a DB
    # trigger (`set_greenlink_case_id`, which skips an id already taken) and
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


def get_case(case_id: str) -> CaseRecord | None:
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
    Removes the case's saved risk history and then its row. Callers delete
    its documents first. Risk scores are historical per-analysis rows, but
    their foreign key prevents deleting the case until they are removed.

    Uses the service client so row-level security cannot turn this into a
    no-op that still looks successful. An empty result means the row is
    still there.
    """
    client = service_supabase_client()
    client.table("risk_scores").delete().eq("case_id", case_id).execute()
    result = client.table("cases").delete().eq("id", case_id).execute()
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
