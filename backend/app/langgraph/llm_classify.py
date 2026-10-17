"""
llm_classify — a Haiku classification of the extracted text, plus its
"Parse & Normalise" step.
"""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..config import settings
from ..llm_io import message_text, parse_json_object
from ..run_context import record_repair, spend_model_call
from .classification_confidence import normalise_candidates as _normalise

TOP_K = 3
ALLOWED_TYPES = {
    "bank_statement",
    "certificate_of_incorporation",
    "payslip",
    "passport",
    "nric",
    "fatca_crs_declaration",
    "certificate_of_incumbency",
    "company_constitution",
    "proof_of_address",
    "source_of_wealth",
    "credit_report",
    "list_of_loans",
    "notice_of_assessment",
}

SYSTEM_PROMPT = """You are a document classifier for a KYC/onboarding pipeline. Given the extracted text of a document, return the THREE most likely categories it could belong to, ranked from most to least likely.

Judge by meaning, not just keyword matches. The text may come from noisy OCR or use different phrasing than the category name.

A document that merely mentions or lists other document types (e.g. a cover letter or email saying documents are attached) is NOT itself one of those types. Classify what the text actually is, not what it refers to.

Categories:
- bank_statement (a statement for a bank ACCOUNT — current, savings or deposit — with transaction history, balances, credits and debits; NOT a brokerage, margin or securities statement)
- source_of_wealth (either a company's financial statements — Statement of Comprehensive Income, Statement of Financial Position and notes, a balance sheet or income statement — or an individual's own evidence of wealth, such as a brokerage or margin-account securities statement showing net asset value, portfolio value and holdings, dividend statements, or business valuation reports; NOT an IRAS Notice of Assessment, which is notice_of_assessment)
- certificate_of_incorporation
- payslip
- passport (a passport biodata page)
- nric (an Identity Card, front or back)
- fatca_crs_declaration
- certificate_of_incumbency (includes ACRA BizFile+ business profiles and similar registry extracts)
- company_constitution
- proof_of_address
- credit_report
- list_of_loans (loan or facility offer letters, overdraft/credit facility agreements, loan schedules, drawdown notices, or other documents describing an existing or offered lending facility)
- notice_of_assessment (an IRAS Income Tax Notice of Assessment: "Year of Assessment", chargeable income, exempt amount, tax assessed/payable, tax rebate, issued by the Comptroller of Income Tax)

Distinguishing passport from nric (both are identity documents; never output "id_document"):
- nric: the card shows "REPUBLIC OF SINGAPORE" and "IDENTITY CARD NO.", with Name, Race, Date of birth, Sex and Country/Place of birth on the front. The back shows a residential address, a fingerprint/thumbprint area and Date of issue, often with no name at all. Front and back are often uploaded as separate files; a sparse back with an address block and date of issue is still an nric.
- passport: a biodata page with a "PASSPORT" heading, a Type / Country Code / Passport No row, Nationality, Place of birth, Date of issue, Date of expiry, Authority, and a machine-readable zone at the foot (two lines of capitals, digits and "<" filler, e.g. "P<SGP..."). It has no residential address. It may print a "National ID No" in NRIC format; that does not make it an nric.
- Merely mentioning "NRIC" or "Passport" as a field label (e.g. an "ID type: NRIC" row in a credit report, or an "NRIC/Passport No." form field) does not make the document an nric or passport.
- If it is clearly an identity document but you cannot tell which, list both passport and nric as candidates with modest match_strength rather than guessing high on one.

Distinguishing notice_of_assessment from other tax-related documents:
- notice_of_assessment is an assessment issued BY the tax authority (IRAS) for a given Year of Assessment, with a computation from chargeable income through exempt amounts and rebate to tax payable. A payslip, a bank statement showing a tax payment, or a FATCA/CRS tax residency form is not one, even if it mentions income tax.

Distinguishing bank_statement from source_of_wealth:
- bank_statement: account-level transaction history for a current, savings or deposit account (balances, credits and debits).
- source_of_wealth: margin, brokerage, securities or investment statements (holdings, positions, portfolio value, net asset value), company financial statements, or other documents evidencing net worth.
- The issuer does not decide it: a bank- or broker-issued investment or securities statement is source_of_wealth, even though it is a periodic "Statement".
- Mixed content: classify by the primary content — deposit-account transactions are bank_statement; holdings and valuations are source_of_wealth.

For each of the 3 candidates provide:
- document_type: one of the category values above. All 3 must be different.
- match_strength: a number from 0.0 to 1.0 for how well the document fits THIS category on its own, judged independently of the other candidates. Runner-up candidates will usually be low. If nothing fits well, all three may be low. Do not force the three values to add up to 1.
- evidence: short phrases from the text that support (or weigh against) this category.
- reasoning: one short sentence on why this category is plausible and, for ranks 2 and 3, what makes it less likely than the one above.

Respond with ONLY a JSON object, no other text, of this shape:
{"top_candidates": [{"document_type": "<category>", "match_strength": <float 0.0-1.0>, "evidence": ["short phrase", ...], "reasoning": "<one short sentence>"}, <candidate 2>, <candidate 3>]}"""


def _extract_json(raw):
    parsed = parse_json_object(raw or "")
    if parsed is None:
        raise ValueError("no JSON object found")
    return parsed


def _clamp(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def _as_str_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]



def parse_llm_classification(llm_text: str) -> dict:
    """Port of the "Parse & Normalise" code node — never raises."""
    notes = []
    raw_text = llm_text or ""

    try:
        data = _extract_json(raw_text)
    except Exception as exc:
        record_repair({"site": "llm classifier", "status": "failed", "issues": [str(exc)], "raw": raw_text[:4000], "used": None})
        return {"top_candidates": [], "notes": [f"LLM output could not be parsed as JSON ({exc})."]}

    raw_candidates = data.get("top_candidates") if isinstance(data, dict) else None
    if raw_candidates is None and isinstance(data, dict) and "document_type" in data:
        raw_candidates = [data]  # tolerate the old single-answer shape
        notes.append("LLM returned a single answer instead of 3 candidates.")
    if not isinstance(raw_candidates, list):
        raw_candidates = []

    seen, candidates = set(), []
    for c in raw_candidates:
        if not isinstance(c, dict):
            continue
        doc_type = str(c.get("document_type") or "").strip().lower()
        if doc_type not in ALLOWED_TYPES:
            if doc_type and doc_type != "null":
                notes.append(f"Dropped unknown category '{doc_type}' returned by the LLM.")
            continue
        if doc_type in seen:
            continue
        seen.add(doc_type)
        candidates.append({
            "document_type": doc_type,
            "match_strength": _clamp(c.get("match_strength", c.get("confidence", 0))),
            "reasoning": str(c.get("reasoning") or ""),
            "evidence": _as_str_list(c.get("evidence")),
        })

    # Stable sort keeps the LLM's own order for ties.
    candidates.sort(key=lambda c: -c["match_strength"])
    top = _normalise(candidates[:TOP_K])

    if notes:
        # Anything dropped or defaulted is kept next to the raw reply, like every other repaired output.
        record_repair({
            "site": "llm classifier", "status": "degraded" if top else "failed", "issues": list(notes),
            "raw": raw_text[:4000], "used": json.dumps(top, default=str)[:4000] if top else None,
        })
    if not top:
        notes.append("LLM returned no valid candidates.")
    elif len(top) < TOP_K:
        notes.append(f"Only {len(top)} valid candidate(s) returned.")
    if top and sum(c["match_strength"] for c in top) == 0:
        notes.append("LLM gave zero match_strength to every candidate; confidence split evenly — treat as unclassified.")

    return {"top_candidates": top, "notes": notes}


async def classify_by_llm(extracted_text: str) -> dict:
    """
    Haiku node. Static
    system prompt first with cache_control so it's a candidate for Anthropic
    prompt caching across documents/runs; the per-document text is the only
    thing that varies. Gated through cache_gate so the first call in a batch
    isn't racing concurrent siblings for the same cache write — see
    cache_gate.py.
    """
    from ._anthropic import build_chat_anthropic
    from .cache_gate import cache_gate

    model = build_chat_anthropic(settings.LANGGRAPH_HAIKU_MODEL, 1024)
    messages = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            ],
        },
        {"role": "user", "content": extracted_text or ""},
    ]
    spend_model_call("llm classifier")
    response = await cache_gate.run("llm_classify", lambda: model.ainvoke(messages))
    return parse_llm_classification(message_text(response.content))


@tool
async def llm_classify_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Runs an independent semantic classification pass over the document's extracted text (no image, no vision) and returns up to 3 ranked candidates with your own reasoning. Call this for a second opinion when the rule-based signal is weak, absent, or ambiguous — skip it when the rule-based result is already decisive."""
    from . import text_cache, trace  # local import: avoids a module cycle at import time

    raw_text = text_cache.get(document_id) or ""
    result = await classify_by_llm(raw_text)
    top = (result.get("top_candidates") or [None])[0]
    trace.record(
        document_id, "llm_classify_tool",
        llm_top_type=top["document_type"] if top else None,
        llm_top_strength=top["match_strength"] if top else None,
    )
    return result
