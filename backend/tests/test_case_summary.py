"""
The case summary (case_pipeline/risk/summary.py): a headline, two to four reasons that each cite one finding, and at
most one line on what would help. A reply that breaks a limit, cites nothing, restates the score or pastes a finding
back in is replaced by the deterministic summary, which does none of those either.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from app.case_pipeline.models import OutlookRequest, RiskOutlook
from app.case_pipeline.risk import summary
from app.models import CaseRisk, ContributingFinding

ADDRESS = "Registered address differs between the BizFile and the bank statement"
PAYSLIP = "Payslip net pay does not match the salary credits on the bank statement"


def _finding(finding_id: str, text: str, *, category: str, filename: str, points: float = 12.0) -> ContributingFinding:
    return ContributingFinding(
        document_id=f"doc-{finding_id}", filename=filename, finding_id=finding_id, severity="high",
        summary=text, rationale="why", points=points, category=category,
    )


RISK = CaseRisk(
    state="scored", level="High", score=72,
    contributing_findings=[
        _finding("xdoc-1", ADDRESS, category="cross_document_variance", filename="bizfile.pdf"),
        _finding("pay-1", PAYSLIP, category="unsupported_claim", filename="payslip.pdf"),
        _finding("font-1", "Mixed fonts in the header", category="tampering_forensic", filename="noa.pdf", points=0),
    ],
)
OUTLOOK = RiskOutlook(
    current_level="High", best_case_level="Medium",
    requests=[OutlookRequest(title="A recent utility bill", what_it_shows="the current address", level_if_supports="Medium")],
)
GOOD = {
    "headline": "Two documents disagree on the company's address, and the payslip doesn't match the bank credits.",
    "reasons": [
        {"text": "The address on file isn't the same everywhere.", "finding_id": "F1"},
        {"text": "Declared pay isn't reflected in the account.", "finding_id": "F2"},
    ],
    "would_help": "A recent utility bill would help.",
}


async def _write(reply: dict, risk: CaseRisk = RISK, outlook: RiskOutlook = OUTLOOK):
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=AIMessage(content=json.dumps(reply)))
    with (
        patch.object(summary, "build_chat_anthropic", return_value=model),
        patch.object(summary, "spend_model_call"),
    ):
        return await summary.write_summary(risk, outlook, ["metadata_tool"], [])


async def test_a_reply_within_the_limits_is_kept() -> None:
    result = await _write(GOOD)

    assert result.source == "model"
    assert result.headline == GOOD["headline"]
    # The model cites F1 and F2; what is stored is the finding itself, document and all.
    assert [(r.text, r.document_id, r.finding_id) for r in result.reasons] == [
        ("The address on file isn't the same everywhere.", "doc-xdoc-1", "xdoc-1"),
        ("Declared pay isn't reflected in the account.", "doc-pay-1", "pay-1"),
    ]
    assert result.would_help == "A recent utility bill would help."


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"headline": " ".join(["word"] * 21) + "."}, "headline over 20 words"),
        ({"headline": "Addresses differ. Pay doesn't match."}, "headline is two sentences"),
        ({"reasons": GOOD["reasons"][:1]}, "fewer than two reasons"),
        ({"reasons": [{"text": "The address isn't the same everywhere."}, GOOD["reasons"][1]]}, "a reason without a citation"),
        ({"reasons": [{"text": "Something else.", "finding_id": "F9"}, GOOD["reasons"][1]]}, "an unknown finding"),
        ({"reasons": [{"text": "Something else.", "finding_id": "xdoc-1"}, GOOD["reasons"][1]]}, "a raw id, not a reference"),
        ({"reasons": [GOOD["reasons"][0], {**GOOD["reasons"][0], "text": "Again."}]}, "the same finding twice"),
        ({"reasons": [{"text": " ".join(["word"] * 17), "finding_id": "F1"}, GOOD["reasons"][1]]}, "a long reason"),
        ({"reasons": [{"text": f"{ADDRESS}.", "finding_id": "F1"}, GOOD["reasons"][1]]}, "finding text pasted back"),
        ({"headline": "The case scores 72 because two documents disagree."}, "the score restated"),
        ({"headline": "Two documents disagree, putting it at 72/100."}, "the score restated as /100"),
        ({"headline": "This is a Critical case because two documents disagree."}, "a level the result doesn't contain"),
        ({"would_help": "A utility bill would help.\nSo would a lease."}, "would_help over one line"),
    ],
)
async def test_a_reply_that_breaks_a_limit_falls_back(change: dict, why: str) -> None:
    result = await _write({**GOOD, **change})

    assert result.source == "fallback", why


async def test_would_help_naming_evidence_nobody_asked_for_falls_back() -> None:
    result = await _write(GOOD, outlook=RiskOutlook(current_level="High"))

    assert result.source == "fallback"
    assert result.would_help is None


async def test_the_fallback_cites_each_finding_without_its_text_or_the_score() -> None:
    result = summary.deterministic_summary(RISK, OUTLOOK)

    assert result.source == "fallback"
    assert result.headline == "2 findings raised the risk, the most serious on bizfile.pdf."
    assert [(r.text, r.document_id, r.finding_id) for r in result.reasons] == [
        ("The documents disagree with each other", "doc-xdoc-1", "xdoc-1"),
        ("A claim on payslip.pdf isn't backed by the other documents", "doc-pay-1", "pay-1"),
    ]
    assert result.would_help == "A recent utility bill would help."
    texts = [result.headline, *(r.text for r in result.reasons)]
    assert not any("72" in text or ADDRESS in text or PAYSLIP in text for text in texts)


async def test_the_same_finding_id_on_two_documents_is_cited_apart() -> None:
    risk = CaseRisk(state="scored", level="Medium", score=40, contributing_findings=[
        _finding("spelling_tool-1", "Misspelt employer name", category="tampering_forensic", filename="payslip.pdf"),
        _finding("spelling_tool-1", "Misspelt bank name", category="tampering_forensic", filename="statement.pdf"),
    ])
    risk.contributing_findings[1].document_id = "doc-statement"
    reply = {"headline": "Both documents carry spelling slips.", "reasons": [
        {"text": "The employer's name is spelt oddly.", "finding_id": "F1"},
        {"text": "The bank's name is spelt oddly.", "finding_id": "F2"},
    ], "would_help": ""}

    result = await _write(reply, risk=risk, outlook=RiskOutlook(current_level="Medium"))

    assert [(r.document_id, r.finding_id) for r in result.reasons] == [
        ("doc-spelling_tool-1", "spelling_tool-1"), ("doc-statement", "spelling_tool-1"),
    ]


async def test_a_case_that_is_not_scored_has_nothing_to_explain() -> None:
    result = await _write(GOOD, risk=CaseRisk(state="pending"))

    assert result.source == "fallback" and result.reasons == []
    assert "not been fully scanned" in result.headline


async def test_a_model_error_falls_back() -> None:
    model = MagicMock()
    model.ainvoke = AsyncMock(side_effect=RuntimeError("overloaded"))
    with patch.object(summary, "build_chat_anthropic", return_value=model), patch.object(summary, "spend_model_call"):
        result = await summary.write_summary(RISK, OUTLOOK, [], [])

    assert result.source == "fallback"
