"""One list of documents to ask the client for: the checklist rules and the agent's requests, merged."""

from __future__ import annotations

import re

from ...models import AdditionalDocument, AdditionalDocuments, CaseRisk, SupportingDocumentRecommendation
from ..models import EvidenceRequest, RiskOutlook
from .case_risk import rate_findings
from .outlook import _adjusted, _reducible, resolve_finding_id

# Checklist rules whose document answers the scored finding they were raised for: a recent statement showing the
# current address settles the address mismatch, so it is re-scored like a request that fully answers it.
_ANSWERS_ITS_FINDING = frozenset({"current_address_evidence"})


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def _rule_effect(rec: SupportingDocumentRecommendation, risk: CaseRisk) -> tuple[list[str], int | None, str | None]:
    """The findings a rule's document answers, and the case's score and level once they're answered."""
    if rec.document_type not in _ANSWERS_ITS_FINDING or risk.state != "scored" or risk.score is None:
        return [], None, None
    raised_for = [item.description for item in rec.items if item.description]
    answered = [
        f.finding_id for f in risk.contributing_findings
        if f.points > 0 and _reducible(f) and any(_same(f.summary, d) for d in raised_for)
    ]
    if not answered:
        return [], None, None
    level, score, _, _ = rate_findings(_adjusted(risk.contributing_findings, {f: 0.0 for f in answered}))
    return answered, score, level


def build_additional_documents(
    risk: CaseRisk,
    requests: list[EvidenceRequest],
    outlook: RiskOutlook,
    supporting_documents: list[SupportingDocumentRecommendation],
    request_status: str,
) -> AdditionalDocuments:
    """
    Every agent request is kept; the ones the outlook re-scored carry their score, the rest are labelled as
    having no effect. Checklist items don't affect the score, except a rule whose document answers the scored
    finding it was raised for (_ANSWERS_ITS_FINDING). Duplicates by title are merged, agent first.
    Ranked by what the officer gains: the requests that would lower the score most first, then the other agent
    requests, then the checklist rules.
    """
    by_id = {f.finding_id: f for f in risk.contributing_findings}
    scored = {r.title: r for r in outlook.requests if r.source == "agent"}

    def gain(request: EvidenceRequest) -> tuple[int, int]:
        hit = scored.get(request.title)
        if hit is None or hit.score_if_supports is None:
            return (1, 0)
        return (0, hit.score_if_supports)

    items: dict[str, AdditionalDocument] = {}
    # sorted() is stable: requests that gain the same keep the model's order.
    for request in sorted(requests, key=gain):
        addressed = [a for a in dict.fromkeys(resolve_finding_id(x, by_id) for x in request.addresses) if a]
        hit = scored.get(request.title)
        items.setdefault(_key(request.title), AdditionalDocument(
            title=request.title, why=request.what_it_shows, source="finding", addresses=addressed,
            affects_score=hit is not None,
            score_if_supports=hit.score_if_supports if hit else None,
            level_if_supports=hit.level_if_supports if hit else None,
        ))
    for rec in supporting_documents:
        answered, score, level = _rule_effect(rec, risk)
        items.setdefault(_key(rec.title), AdditionalDocument(
            title=rec.title, why=rec.reason, source="checklist", recommendation=rec,
            addresses=answered, affects_score=score is not None, score_if_supports=score, level_if_supports=level,
        ))

    scoring = [f for f in by_id.values() if f.points > 0]
    if request_status in ("failed", "budget"):
        reason = "model_failed" if request_status == "failed" else "budget"
    elif not scoring:
        reason = "none_needed"
    elif not any(_reducible(f) for f in scoring):
        reason = "no_reducible_findings"
    else:
        reason = None
    return AdditionalDocuments(
        items=list(items.values()),
        reason=reason,
        current_score=outlook.current_score,
        current_level=outlook.current_level,
        best_case_score=outlook.best_case_score,
        best_case_level=outlook.best_case_level,
    )
