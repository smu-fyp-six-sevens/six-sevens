from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from .models import CaseRecord
from .supabase_client import supabase_client


def create_case(company_name: Optional[str] = None) -> CaseRecord:
    now = datetime.now(timezone.utc)
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
