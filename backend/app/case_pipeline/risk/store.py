"""
Saves each case's risk score to the existing `risk_scores` table and reads
the latest back (through the `latest_risk_scores` view, migration 003).

A row is written only when the result changed, so the table is a history of
what the score was and when, not a row per page view. The case list reads
the latest row instead of recomputing every case's risk.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

from ...models import CaseRisk, RiskLevel, RiskState
from ...supabase_client import supabase_client

logger = logging.getLogger("classifier_pipeline.case_pipeline.risk_store")

_TABLE = "risk_scores"
_LATEST_VIEW = "latest_risk_scores"
_CHUNK = 100
# risk_level is NOT NULL text, so a case that has no score yet is stored under its state's label.
_STATE_LABELS = {"pending": "Pending", "failed": "Failed", "no_documents": "No documents"}
_STATE_BY_LABEL = {label: state for state, label in _STATE_LABELS.items()}

# The last signature written per case in this process, so an unchanged score costs no read or write.
_last_saved: dict[str, tuple] = {}


def _signature(risk: CaseRisk) -> tuple:
    return (risk.state, risk.level, risk.score, tuple(sorted(f.finding_id for f in risk.contributing_findings)))


def save_if_changed(case_id: str, risk: Optional[CaseRisk]) -> bool:
    """Inserts a row when the case's score, level or contributing findings differ from the last one saved."""
    if risk is None:
        return False
    signature = _signature(risk)
    if _last_saved.get(case_id) == signature:
        return False
    try:
        latest_many([case_id])  # seeds the last saved signature from the table when this process hasn't seen the case
        if _last_saved.get(case_id) == signature:
            return False
        supabase_client.table(_TABLE).insert({
            "case_id": case_id,
            "overall_score": risk.score if risk.state == "scored" and risk.score is not None else 0,
            "risk_level": risk.level if risk.state == "scored" and risk.level else _STATE_LABELS.get(risk.state, risk.state),
            "reasoning": " ".join(risk.reasons) or None,
            "flag_ids": [f.finding_id for f in risk.contributing_findings],
            "scored_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        _last_saved[case_id] = signature
        return True
    except Exception:  # noqa: BLE001 - saving is a record, never a reason for a read to fail
        logger.exception("Failed to save the risk score of case %s", case_id)
        return False


def _to_risk(row: dict[str, Any]) -> CaseRisk:
    label = str(row.get("risk_level") or "")
    reasons = [row["reasoning"]] if row.get("reasoning") else []
    state = _STATE_BY_LABEL.get(label)
    if state is not None:
        return CaseRisk(state=cast(RiskState, state), reasons=reasons)
    return CaseRisk(
        state="scored",
        level=cast(RiskLevel, label),
        score=round(float(row["overall_score"])),
        reasons=reasons,
    )


def latest_many(case_ids: list[str]) -> dict[str, CaseRisk]:
    """The newest saved risk of each case. The findings behind it are not stored, only their ids."""
    latest: dict[str, CaseRisk] = {}
    for start in range(0, len(case_ids), _CHUNK):
        rows = (
            supabase_client.table(_LATEST_VIEW)
            .select("case_id, overall_score, risk_level, reasoning, flag_ids")
            .in_("case_id", case_ids[start : start + _CHUNK])
            .execute()
            .data
        )
        for raw in rows:
            row = cast(dict, raw)
            risk = _to_risk(row)
            # The ids are kept on the CaseRisk only to compare with a fresh result, never shown.
            risk.contributing_findings = []
            latest[row["case_id"]] = risk
            _last_saved.setdefault(row["case_id"], _signature_with_ids(risk, row.get("flag_ids")))
    return latest


def _signature_with_ids(risk: CaseRisk, flag_ids: Any) -> tuple:
    ids = tuple(sorted(str(i) for i in flag_ids)) if isinstance(flag_ids, list) else ()
    return (risk.state, risk.level, risk.score, ids)
