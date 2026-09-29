"""
Shared classify-subgraph logic. `validate_agent_output` is a straight port
of the Classifier chatflow's "Validate Output" code node (see
backend/app/dify/Classifier (Chatflow) (2).yml) — that contract (allowed
types, id_document self-fulfilment, never-raises parsing) doesn't depend on
how the classification was arrived at, so it's unchanged.

The agent's system prompt (`AGENT_STATIC_PROMPT`/`agent_prompt_parts`) is
NOT the Dify port anymore. Dify's Agent 3 was handed two already-computed
results (rule_result, llm_result) and could only optionally call vision_llm;
this graph only pre-computes rule_result deterministically (see
LANGGRAPH_MIGRATION_PLAN.md — "only rule-based runs deterministically") and
gives the agent two real tools, `llm_classify_tool` and `vision_llm`, to
call zero, one, or both of as it judges necessary — including calling
neither when rule_result already includes a decisive `certain_type` (see
rule_classify.py), which is surfaced to the agent as a hint, not a
hardcoded bypass: the agent still has to say so in its own output.
`address_check` and `has_usable_text` are still computed the same way, from
rule_result alone.
"""

from __future__ import annotations

import json

# Minimum extracted-text length worth classifying from text alone.
MIN_USABLE_CHARS = 50

_ABSENT_RESULT = {"top_candidates": [], "notes": "tool output unparseable - treat as absent"}

_ADDRESS_VISIBLE = "appears to show a residential address"
_ADDRESS_NOT_RESIDENTIAL = "not residential"


def has_usable_text(extracted_text) -> bool:
    if extracted_text is None:
        return False
    if isinstance(extracted_text, list):
        raw = "\n".join(str(t) for t in extracted_text if t)
    else:
        raw = str(extracted_text)
    return len(raw.strip()) >= MIN_USABLE_CHARS


def _notes_text(rule: dict) -> str:
    notes = rule.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    return " ".join(str(n) for n in notes).lower()


def _is_nric_front(rule: dict) -> bool:
    """The NRIC front carries "IDENTITY CARD NO." and never an address."""
    for candidate in rule.get("top_candidates") or []:
        if candidate.get("document_type") == "nric":
            return any("identity card no" in str(e).lower() for e in candidate.get("evidence") or [])
    return False


def address_check(rule_result: dict | None, text_available: bool) -> str:
    """
    visible     — rule layer found a residential address in the text
    not_visible — rule layer flagged it as a business/office address, or
                  this is an NRIC front (no address expected)
    unknown     — text layer can't settle it (no text, or no signal either way)
    """
    if not text_available:
        return "unknown"
    rule = rule_result or {}
    notes = _notes_text(rule)
    if _ADDRESS_VISIBLE in notes:
        return "visible"
    if _ADDRESS_NOT_RESIDENTIAL in notes or _is_nric_front(rule):
        return "not_visible"
    return "unknown"


_INTRO = "You classify documents for a KYC/corporate onboarding pipeline.\n\n"

# Only this template is ever .format()-ed; keep braces elsewhere literal.
_SHARED_BODY = """## Inputs
You are given, already computed:
- rule_result: regex/keyword scoring against filename + extracted text. top_candidates (up to 3, ranked) + notes.
  Provided below, after this instruction block. This is the ONLY source computed for you — nothing else has run yet.
- text_available: whether a text layer existed for rule_result to work from.
- address_check: visible | not_visible | unknown — already computed from rule_result's address note.

confidence is RELATIVE (sums to 1, never copy into your own output). match_strength is ABSOLUTE (0-1) — use this for your own confidence. rule_result's "pick" = its rank-1. "No match" = empty candidates or rank-1 match_strength < 0.3. "Close" = rank-1/rank-2 confidence differ by < 0.15.

## Your tools
You have two tools, offered to you one at a time, in a fixed order — you decide whether to actually use each one, but you cannot reach for vision before you've had your first turn, and it will never be offered alongside the text tool:
- llm_classify_tool: offered first, on your very first turn only. An independent semantic read of the extracted text (no image). Cheap. Worth calling when rule_result is weak, absent, ambiguous ("close"), or you simply want a second opinion before committing to a low-evidence guess. Skip it when rule_result already gives you a confident, unambiguous answer — especially when rule_result includes a `certain_type` (a decisive, type-unique phrase was found; you may finalise on that alone, but you're not required to). You only get this one chance to call it.
- vision_llm: your last resort, offered only from your second turn onward (whether or not you called llm_classify_tool) and only until you've used it once. Reads the document's actual image/page — by far the most expensive option, since it sends the whole document, not just text. Worth calling when: text-based signals (rule_result, and llm_classify_tool if you called it) disagree on the type; both are weak, absent, or close; the type you're leaning toward is nric or bank_statement and address_check is "unknown"; or you otherwise remain unsure after checking the text-based signals. Don't call it just to double-check an already-confident agreement.

If you're confident enough after rule_result alone (or after also calling llm_classify_tool), answer immediately — you are never required to call vision just because it becomes available.

## Categories
bank_statement, certificate_of_incorporation, payslip, passport, nric, fatca_crs_declaration, certificate_of_incumbency, company_constitution, proof_of_address, source_of_wealth, credit_report, list_of_loans, notice_of_assessment, other

## bank_statement vs source_of_wealth
- bank_statement: a statement for a bank ACCOUNT (current/savings/deposit) — account-level transaction history with balances, credits and debits.
- source_of_wealth: margin, securities, brokerage or investment statements (holdings, positions, portfolio valuation), or any financial document evidencing net worth or its history.
- The issuer does not decide it: a bank-issued securities/investment/wealth statement is source_of_wealth, not bank_statement.
- Mixed content -> classify by the primary content: deposit-account transactions -> bank_statement; holdings/positions/net-worth valuation -> source_of_wealth.

## passport vs nric
Both are identity documents; never output "id_document".
- nric: Singapore Identity Card. Front: "REPUBLIC OF SINGAPORE", "IDENTITY CARD NO.", portrait, Race, Sex, Country/Place of birth. Back: residential address block, fingerprint area, Date of issue, usually no name or photo.
- passport: biodata page with "PASSPORT" heading, Type / Country Code / Passport No row, Nationality, Date of expiry, Authority, and a two-line machine-readable zone. No residential address.
- Identity document but genuinely can't tell which: pick the likelier one, cap confidence at 0.6, set needs_manual_review, and name the runner-up in evidence.

## Deciding the type + confidence
Agreement = rank-1 picks matching, among every source you actually consulted (rule_result always; llm_classify_tool/vision_llm only if you called them). A rank-2/3 match is partial support, not agreement.

- All sources you consulted agree (or you only ever needed rule_result and it was decisive, e.g. `certain_type`) -> that type, confidence = average of their match_strength, capped at 0.97 for a `certain_type` match or 0.95 otherwise.
- You consulted more than one source and they disagree -> prefer the type with more sources behind it; if it's still one-vs-one, pick the higher match_strength, cap confidence at 0.6, and set needs_manual_review true.
- Every source you consulted came back no-match/absent -> "other", confidence low — never high-confidence an absence.
- Only one source produced a usable result and you stopped there (e.g. you judged rule_result alone was enough) -> if it was a `certain_type` rule match, cap at 0.97; otherwise say so explicitly in reasoning and cap at 0.85 (uncorroborated) — lower still (cap 0.6, needs_manual_review) if that lone source's pick was itself "close".

## Proof of address
Depends on THIS document actually showing a legible residential address — never inferred from type alone.
- nric: only satisfies it if a residential address is visibly populated.
- bank_statement: only if the address is residential, not a registered office/business address.
- source_of_wealth: never adds proof_of_address, even if an address is shown.
- No confident confirmation -> leave it out (under-claim, don't over-claim).

address_check is given in the inputs below:
- visible -> the address is confirmed. Include proof_of_address (nric/bank_statement only). No need to call vision_llm just to re-confirm it.
- not_visible -> no residential address on this document. Do not include proof_of_address. No need to call vision_llm for this.
- unknown -> not settled by the text layer alone — this is one of the reasons to consider calling vision_llm and letting what it sees decide.

## fulfilled_requirements
Default: exactly one entry — the document_type itself, except passport and nric, which both use "id_document". Address check is the only thing that ever adds a second entry (proof_of_address), and only for nric/bank_statement.

## Evidence
Explains why it's THIS TYPE, never document content/values. Name what's PRESENT, never its VALUE. Max 4 entries, one line each.

## Output
JSON only, nothing else:
{{"document_type": "...", "confidence": 0.0, "evidence": ["..."], "reasoning": "...", "needs_manual_review": true, "fulfilled_requirements": ["..."]}}"""

_INPUTS_BLOCK = """

## Classification inputs for this document

text_available: {text_available}

address_check: {address_check}

rule_result:
{rule_result}"""


#: Byte-stable across every document — the only part worth a cache_control
#: breakpoint (see agent_prompt_parts below and LANGGRAPH_MIGRATION_PLAN.md
#: section 5, "Prompt caching").
AGENT_STATIC_PROMPT = _INTRO + _SHARED_BODY


def agent_prompt_parts(rule_result: dict | None, extracted_text) -> tuple[str, str]:
    """
    Same content as build_agent_prompt, split into (static, per-document)
    so callers can put cache_control on just the static half — concatenating
    the per-document inputs_block into that block would make it a different
    byte string on every document and defeat caching entirely.
    """
    text_available = has_usable_text(extracted_text)
    inputs_block = _INPUTS_BLOCK.format(
        text_available=str(text_available).lower(),
        address_check=address_check(rule_result, text_available),
        rule_result=json.dumps(rule_result or dict(_ABSENT_RESULT), indent=2),
    )
    return AGENT_STATIC_PROMPT, inputs_block


def build_agent_prompt(rule_result: dict | None, extracted_text) -> str:
    """Builds the agent's full system prompt as one string (mainly for tests)."""
    static, dynamic = agent_prompt_parts(rule_result, extracted_text)
    return static + dynamic


VALID_TYPES = {
    "bank_statement", "certificate_of_incorporation", "payslip",
    "passport", "nric", "fatca_crs_declaration", "certificate_of_incumbency",
    "company_constitution", "proof_of_address", "source_of_wealth",
    "credit_report", "list_of_loans", "notice_of_assessment", "unrecognised",
}
TYPE_ALIASES = {"other": "unrecognised"}
REQUIREMENT_FOR_TYPE = {"passport": "id_document", "nric": "id_document"}
VALID_REQUIREMENTS = (VALID_TYPES - {"unrecognised", "passport", "nric"}) | {"id_document"}


def validate_agent_output(agent_output: str) -> dict:
    """Port of the "Validate Output" code node — never raises."""
    doc_type, confidence, evidence, reasoning = "", 0.0, [], ""
    fulfilled_requirements: list[str] = []
    flagged = True

    try:
        raw = agent_output or ""
        parsed = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
        candidate = (parsed.get("document_type") or "").strip().lower()
        candidate = TYPE_ALIASES.get(candidate, candidate)
        doc_type = candidate if candidate in VALID_TYPES else ""
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        evidence = [str(item) for item in parsed.get("evidence", [])]
        reasoning = str(parsed.get("reasoning", ""))
        flagged = bool(parsed.get("needs_manual_review", True))

        raw_requirements = parsed.get("fulfilled_requirements", [])
        if isinstance(raw_requirements, list):
            fulfilled_requirements = [
                str(item) for item in raw_requirements if str(item) in VALID_REQUIREMENTS
            ]

        own_requirement = REQUIREMENT_FOR_TYPE.get(doc_type, doc_type)
        if not fulfilled_requirements and own_requirement in VALID_REQUIREMENTS:
            fulfilled_requirements = [own_requirement]

        if candidate and not doc_type:
            reasoning = f"Agent returned unknown type '{candidate}'. {reasoning}"
            flagged = True
    except (ValueError, TypeError, AttributeError):
        reasoning = "Could not parse agent output as JSON."

    return {
        "document_type": doc_type or None,
        "confidence": round(confidence, 4),
        "evidence": evidence,
        "fulfilled_requirements": fulfilled_requirements,
        "needs_manual_review": flagged or not doc_type,
        "reasoning": reasoning,
    }
