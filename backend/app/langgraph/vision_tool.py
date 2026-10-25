"""
vision_llm — the classify agent's vision tool. It parses its answer the same way
as llm_classify, so vision returns the same {top_candidates (top 3), notes}
shape as rule_result/llm_result, a third source with its own
rank-1/agreement/close semantics (see classify_support's "Deciding the type +
confidence" table).

The file is read from storage directly. Parameter-bound via InjectedState so
storage_path/filename never appear in the schema the model sees.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from . import trace
from .llm_classify import parse_llm_classification
from .. import storage_service
from ..config import settings
from ..guardrails.envelope import IMAGE_RULE
from ..llm_io import message_text
from ..run_context import spend_model_call

MAX_CANDIDATES = 3
MAX_EVIDENCE_ITEMS = 6

# The vision classification prompt.
VISION_PROMPT = """You are a document classifier for a KYC/onboarding pipeline. Given the document, return the THREE most likely categories it could belong to, ranked from most to least likely.

Judge by both what the document says and what it looks like: layout, letterheads, logos, crests, photos, tables and security features.

A document that merely mentions or lists other document types (e.g. a cover letter or email saying documents are attached) is NOT itself one of those types. Classify what the document actually is, not what it refers to.

Categories:
- bank_statement (a statement for a bank ACCOUNT — current, savings or deposit — with transaction history, balances, credits and debits; NOT a brokerage, margin or securities statement)
- source_of_wealth (either a company's financial statements — Statement of Comprehensive Income, Statement of Financial Position and notes, a balance sheet or income statement — or an individual's own evidence of wealth, such as a brokerage or margin-account securities statement showing net asset value, portfolio value and holdings, dividend statements, or business valuation reports; NOT an IRAS Notice of Assessment, which is notice_of_assessment)
- certificate_of_incorporation
- payslip
- passport (a passport biodata page)
- nric (a Singapore National Registration Identity Card, front or back)
- fatca_crs_declaration
- certificate_of_incumbency (includes ACRA BizFile+ business profiles and similar registry extracts)
- company_constitution
- proof_of_address
- credit_report
- list_of_loans (loan or facility offer letters, overdraft/credit facility agreements, loan schedules, drawdown notices, or other documents describing an existing or offered lending facility)
- notice_of_assessment (an IRAS Income Tax Notice of Assessment: IRAS logo and letterhead, "Year of Assessment", a tax computation table from chargeable income through exempt amount and rebate to tax payable, signed by the Comptroller of Income Tax)

Distinguishing passport from nric (both are identity documents; never output "id_document"):
- nric: a pink/coloured plastic-card layout with "REPUBLIC OF SINGAPORE" and "IDENTITY CARD NO.", a portrait, and Name, Race, Date of birth, Sex and Country/Place of birth on the front. The back shows a residential address block, a fingerprint area and Date of issue, often with no name or photo. Front and back are often uploaded as separate images; a back with an address block and date of issue is still an nric.
- passport: a booklet biodata page with a "PASSPORT" heading, a crest, a Type / Country Code / Passport No row, Nationality, Date of expiry and Authority, a photo, and a two-line machine-readable zone at the bottom (capitals, digits and "<" filler). It has no residential address. A "National ID No" printed on it does not make it an nric.
- If it is clearly an identity document but you cannot tell which, list both passport and nric with modest match_strength rather than guessing high on one.

Distinguishing bank_statement from source_of_wealth:
- bank_statement: account-level transaction history for a current, savings or deposit account (balances, credits and debits).
- source_of_wealth: margin, brokerage, securities or investment statements (holdings, positions, portfolio value, net asset value), company financial statements, or other documents evidencing net worth.
- The issuer does not decide it: a bank- or broker-issued investment or securities statement is source_of_wealth, even though it is a periodic "Statement".
- Mixed content: classify by the primary content — deposit-account transactions are bank_statement; holdings and valuations are source_of_wealth.

For each of the 3 candidates provide:
- document_type: one of the category values above. All 3 must be different.
- match_strength: a number from 0.0 to 1.0 for how well the document fits THIS category on its own, judged independently of the other candidates. Runner-up candidates will usually be low. If nothing fits well, all three may be low. Do not force the three values to add up to 1.
- evidence: short phrases or visual cues from the document that support (or weigh against) this category.
- reasoning: one short sentence on why this category is plausible and, for ranks 2 and 3, what makes it less likely than the one above.

Respond with ONLY a JSON object, no other text, of this shape:
{"top_candidates": [{"document_type": "<category>", "match_strength": <float 0.0-1.0>, "evidence": ["short phrase or visual cue", ...], "reasoning": "<one short sentence>"}, <candidate 2>, <candidate 3>]}"""


class VisionCandidate(BaseModel):
    document_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    match_strength: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    evidence: list[str] = Field(default_factory=list)


class VisionClassificationResult(BaseModel):
    """Small, validated shape — never raw provider JSON."""

    top_candidates: list[VisionCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _build_image_block(file_bytes: bytes, filename: str) -> dict:
    content_type = _content_type(filename)
    encoded = base64.b64encode(file_bytes).decode("ascii")
    block_type = "document" if content_type == "application/pdf" else "image"
    return {
        "type": block_type,
        "source": {"type": "base64", "media_type": content_type, "data": encoded},
    }


def _capped(result: dict) -> VisionClassificationResult:
    candidates = (result.get("top_candidates") or [])[:MAX_CANDIDATES]
    for c in candidates:
        c["evidence"] = (c.get("evidence") or [])[:MAX_EVIDENCE_ITEMS]
    notes = (result.get("notes") or [])[:MAX_EVIDENCE_ITEMS]
    return VisionClassificationResult(top_candidates=candidates, notes=notes)


async def _classify_with_vision(storage_path: str, filename: str) -> VisionClassificationResult:
    from ._anthropic import build_chat_anthropic
    from .cache_gate import cache_gate

    try:
        file_bytes = await asyncio.to_thread(storage_service.download_file, storage_path)
    except Exception as exc:
        return VisionClassificationResult(notes=[f"Could not read the document: {exc}"])

    model = build_chat_anthropic(settings.LANGGRAPH_SONNET_MODEL, 1024)
    # Static prompt FIRST, document second. A cache_control breakpoint caches
    # everything up to and including its own block, so with the document
    # first the cached prefix contained this document — unique per call,
    # never re-readable, and still charged the 1.25x write premium. Ordering
    # it this way makes the byte-stable prompt the cached prefix and leaves
    # the document as the varying suffix, matching extract.py and agent_node.
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": f"{VISION_PROMPT}\n\n{IMAGE_RULE}", "cache_control": {"type": "ephemeral"}},
            _build_image_block(file_bytes, filename),
        ],
    }
    try:
        spend_model_call("vision classifier")
        # Gated like llm_classify/extract: the first vision call in a batch
        # pays for the cache write, later ones wait for it instead of racing it.
        response = await cache_gate.run("vision_llm", lambda: model.ainvoke([message]))
    except Exception as exc:
        return VisionClassificationResult(notes=[f"Vision call failed: {exc}"])

    raw = message_text(response.content)
    # Same parse as llm_classify: same ALLOWED_TYPES, top-3/normalise/never-raises behaviour.
    return _capped(parse_llm_classification(raw))


@tool
async def vision_llm(
    storage_path: Annotated[str, InjectedState("storage_path")],
    filename: Annotated[str, InjectedState("filename")],
    document_id: Annotated[str, InjectedState("document_id")],
) -> dict:
    """Reads the document's image/page and classifies it directly — expensive, call only when the rule and LLM classifiers disagree, are unsure, or an address check is unresolved."""
    result = await _classify_with_vision(storage_path, filename)
    top = result.top_candidates[0] if result.top_candidates else None
    trace.record(
        document_id, "vision_llm",
        vision_top_type=top.document_type if top else None,
        vision_top_strength=top.match_strength if top else None,
    )
    return result.model_dump()
