"""
Saves each case's risk score to the existing `risk_scores` table and reads
the latest back (through the `latest_risk_scores` view).

Written once per completed case analysis (the only place a case's risk is
computed), so the table is a history of scores, one row per run. The case
list reads the latest row instead of opening every run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, cast

from ...models import CaseRisk, RiskLevel, RiskState
from ...supabase_client import supabase_client

logger = logging.getLogger("classifier_pipeline.case_pipeline.risk_store")

_TABLE = "risk_scores"
_LATEST_VIEW = "latest_risk_scores"
_CHUNK = 100
# risk_level is NOT NULL text, so a case that has no score yet is stored under its state's label.
_STATE_LABELS = {"pending": "Pending", "failed": "Failed", "no_documents": "No documents"}
_STATE_BY_LABEL = {label: state for state, label in _STATE_LABELS.items()}


def save(case_id: str, risk: CaseRisk | None) -> None:
    """Records an analysis run's score. Never raises: the run's own result is the source of truth."""
    if risk is None:
        return
    try:
        supabase_client.table(_TABLE).insert({
            "case_id": case_id,
            "overall_score": risk.score if risk.state == "scored" and risk.score is not None else 0,
            "risk_level": risk.level if risk.state == "scored" and risk.level else _STATE_LABELS.get(risk.state, risk.state),
            "reasoning": " ".join(risk.reasons) or None,
            # Real flags-table ids: findings no specialist approved, and case-level ones, have none.
            "flag_ids": [f.flag_id for f in risk.contributing_findings if f.flag_id],
            "scored_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        logger.exception("Failed to save the risk score of case %s", case_id)


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
            .select("case_id, overall_score, risk_level, reasoning")
            .in_("case_id", case_ids[start : start + _CHUNK])
            .execute()
            .data
        )
        for raw in rows:
            row = cast(dict, raw)
            latest[row["case_id"]] = _to_risk(row)
    return latest
