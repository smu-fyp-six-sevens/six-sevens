from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from .models import CaseRecord
from .supabase_client import supabase_client


def create_case(company_name: Optional[str] = None) -> CaseRecord:
    now = datetime.now(timezone.utc)

    # greenlink_case_id is intentionally omitted — it's assigned by a DB
    # trigger (see migrations/001_add_greenlink_case_id_generation.sql) and
    # comes back populated in the inserted row's representation below.
    row = {
        "id": str(uuid.uuid4()),
        "company_name": company_name,
        "status": "open",
        "submission_finalized": False,
        "recommended_additional_documents": [],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    result = supabase_client.table("cases").insert(row).execute()

    return CaseRecord(**result.data[0])


def get_case(case_id: str) -> Optional[CaseRecord]:
    result = supabase_client.table("cases").select("*").eq("id", case_id).execute()
    if not result.data:
        return None
    return CaseRecord(**result.data[0])


def list_cases() -> list[CaseRecord]:
    result = supabase_client.table("cases").select("*").execute()
    return [CaseRecord(**row) for row in result.data]


def set_submission_finalized(case_id: str, finalized: bool) -> None:
    """
    Kept in sync with actual requirement coverage rather than being a
    one-way, officer-triggered action — see case_status.sync_submission_finalized,
    called right after every classification result is recorded. Reversible:
    if a later reclassification (e.g. via the /retry endpoint) drops
    coverage back below 100%, this flips back to False/None automatically.
    """
    update = {
        "submission_finalized": finalized,
        "finalized_at": datetime.now(timezone.utc).isoformat() if finalized else None,
    }
    supabase_client.table("cases").update(update).eq("id", case_id).execute()
