from __future__ import annotations

from .fraud_detection.spelling_anomaly import SpellingAnomaly
from .supabase_client import supabase_client

# public.flags is a shared table across every detection check (font
# consistency, spelling anomaly, others to come) — these two values are
# this check's own identity within it. detection_dimension is the broader
# category a reviewer/aggregator might group by; check_name is the specific
# check that produced the row.
CHECK_NAME = "spelling_anomaly"
DETECTION_DIMENSION = "content_validation"


def _describe(anomaly: SpellingAnomaly) -> str:
    if anomaly.suggested_correction:
        return f"Possible spelling anomaly: '{anomaly.word}' (suggested: '{anomaly.suggested_correction}')"
    return f"Unrecognised word with no clear correction: '{anomaly.word}'"


def replace_spelling_anomalies(
    document_id: str, case_id: str | None, anomalies: list[SpellingAnomaly]
) -> None:
    """
    Delete-then-insert, same "replace" semantics as
    extracted_fields_repository, so re-running the on-demand check leaves
    exactly the current scan's findings. Scoped to check_name as well as
    document_id — flags is shared across every detection check, so an
    unscoped delete-by-document_id here would wipe out another check's
    (e.g. font_consistency's) rows for the same document.

    severity and confidence are deliberately left at their table defaults /
    null rather than a fabricated number — the check's own local heuristics
    (SymSpell edit distance, the proper-noun filter — see
    spelling_anomaly._looks_like_probable_proper_noun) aren't calibrated
    probabilities, so reporting one here would just be theatre.
    status/reviewed_by/reviewed_at and related_document_ids are likewise
    left untouched (table defaults / null) — this check has no reviewer-
    workflow or cross-document correlation to report.
    """
    (
        supabase_client.table("flags")
        .delete()
        .eq("document_id", document_id)
        .eq("check_name", CHECK_NAME)
        .execute()
    )

    if not anomalies:
        return

    rows = [
        {
            "document_id": document_id,
            "case_id": case_id,
            "detection_dimension": DETECTION_DIMENSION,
            "check_name": CHECK_NAME,
            "description": _describe(anomaly),
            "evidence": {
                "word": anomaly.word,
                "page": anomaly.page,
                "section": anomaly.section,
                "surrounding_text": anomaly.surrounding_text,
                "suggested_correction": anomaly.suggested_correction,
            },
        }
        for anomaly in anomalies
    ]
    supabase_client.table("flags").insert(rows).execute()
