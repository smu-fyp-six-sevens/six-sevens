"""
rule_classify — ported verbatim from backend/app/dify/rule_classify_v3.yml's
Dify Code node (layers 1 + 2). No LLM call — pure regex scoring, so this is a
straight port with no redesign. Keep this file and that YAML's `code` block
in sync if either is edited; the YAML is the historical record of what
shipped in Dify, this module is what LangGraph actually runs.

v3 behaviour (top-3 candidates):
  1. Scores EVERY document type across filename + text layers instead of
     keeping only the single best. Output is `top_candidates`: up to 3
     ranked options, each with its own reasoning and evidence.
  2. Each candidate carries two numbers:
       - confidence     : share of the rule layer's belief across the
                           returned candidates. Sums to exactly 1.0. RELATIVE.
       - match_strength : absolute rule score 0-1 (the old `confidence`).
                           Needed because normalising hides weak matches — a
                           lone 0.4 filename hit would otherwise read as 1.0.
  3. Per type, filename + text scores combine as
     max(f, t) + min(f, t) * TEXT_AGREEMENT_BOOST (capped at 1.0) — the old
     "layers agree" boost, generalised to every type.
  4. Evidence shows the matched wording (e.g. text: "opening balance")
     instead of raw regex source.
  5. Residential-address check is returned in `notes` (document-level,
     independent of type).
  6. needs_manual_review uses the TOP candidate's match_strength vs
     confidence_threshold (normalised confidence would always pass).
  7. No rule fired -> top_candidates is [] and needs_manual_review is True.
"""

from __future__ import annotations

import re

MAX_TEXT_CHARS = 60000
TEXT_AGREEMENT_BOOST = 0.5

_FILENAME_RULES = {
    "notice_of_assessment": [
        (r"notice\s*of\s*assessment", 0.95),
        (r"\bnoa\b", 0.8),
        (r"\bassessment\b", 0.4),
        (r"\biras\b", 0.7),
        (r"income\s*tax", 0.7),
    ],
    "bank_statement": [
        (r"bank\s*statement", 0.9),
        (r"\bstatement\b", 0.4),
        (r"\b(dbs|ocbc|uob|hsbc|citibank|maybank|cimb)\b", 0.3),
    ],
    "certificate_of_incorporation": [
        (r"certificate\s*of\s*incorporation", 0.9),
        (r"\bincorporation\b", 0.5),
    ],
    "payslip": [
        (r"pay\s*slip", 0.9),
        (r"salary\s*slip", 0.9),
        (r"\bpayroll\b", 0.4),
    ],
    "nric": [
        (r"\bnric\b", 0.9),
        (r"identity\s*card", 0.9),
    ],
    "passport": [
        (r"\bpassport\b", 0.9),
    ],
    "fatca_crs_declaration": [
        (r"\bfatca\b", 0.9),
        (r"\bcrs\b", 0.6),
        (r"self[\s-]?declaration", 0.5),
    ],
    "certificate_of_incumbency": [
        (r"certificate\s*of\s*incumbency", 0.95),
        (r"\bincumbency\b", 0.7),
        (r"\bbizfile\b", 0.7),
        (r"business\s*profile", 0.7),
        (r"\bacra\b", 0.7),
    ],
    "company_constitution": [
        (r"\bconstitution\b", 0.8),
        (r"memorandum\s*and\s*articles", 0.8),
        (r"articles\s*of\s*association", 0.8),
        (r"\bm\s*&?\s*aa\b", 0.5),
    ],
    "proof_of_address": [
        (r"proof\s*of\s*address", 0.9),
        (r"utility\s*bill", 0.7),
        (r"residential\s*address", 0.6),
    ],
    "source_of_wealth": [
        (r"source\s*(of\s*)?wealth", 0.9),
        (r"\bsow\b", 0.5),
        (r"\bwealth\b", 0.5),
        (r"financial\s*statements?", 0.6),
        (r"balance\s*sheet", 0.6),
        (r"income\s*statement", 0.6),
    ],
    "credit_report": [
        (r"credit\s*report", 0.9),
        (r"credit\s*bureau", 0.7),
        (r"\bccris\b", 0.6),
    ],
    "list_of_loans": [
        (r"list\s*of\s*loans", 0.9),
        (r"loan\s*(list|schedule)", 0.8),
        (r"outstanding\s*loans", 0.7),
        (r"facility\s*(offer|letter|agreement)", 0.7),
        (r"overdraft\s*facility", 0.7),
        (r"credit\s*facility", 0.7),
        (r"\b(loan|overdraft)s?\b", 0.7),
    ],
}

_TEXT_RULES = {
    "notice_of_assessment": [
        (r"notice\s*of\s*assessment", 0.95),
        (r"year\s*of\s*assessment", 0.7),
        (r"comptroller\s*of\s*income\s*tax", 0.8),
        (r"inland\s*revenue\s*authority", 0.7),
        (r"\biras\.gov\.sg\b", 0.4),
        (r"\bmytax\b", 0.3),
        (r"estimated\s*chargeable\s*income", 0.7),
        (r"\btax\s*assessed\b", 0.7),
        (r"\btax\s*(payable|rebate)\b", 0.3),
        (r"\bexempt\s*amount\b", 0.3),
        (r"tax\s*reference\s*number", 0.3),
    ],
    "bank_statement": [
        (r"bank\s*statement", 0.95),
        (r"opening\s*balance", 0.7),
        (r"closing\s*balance", 0.7),
        (r"\btransaction\s*history\b", 0.5),
    ],
    "certificate_of_incorporation": [
        (r"certificate\s*of\s*incorporation", 0.95),
        # ACRA's own title is "Certificate Confirming Incorporation of
        # Company" — "Confirming" sits between the two words, so the rule
        # above never fires on the actual Singapore document.
        (r"certificate\s*confirming\s*incorporation", 0.95),
        (r"company\s*registration\s*number", 0.3),
        (r"incorporated\s*(under|in)\b", 0.5),
    ],
    "payslip": [
        (r"pay\s*slip", 0.9),
        (r"salary\s*slip", 0.9),
        (r"\bnet\s*pay\b", 0.7),
        (r"\bgross\s*pay\b", 0.7),
        (r"\bbasic\s*salary\b", 0.7),
    ],
    "nric": [
        (r"\bnric\b", 0.4),
        (r"identity\s*card\s*no", 0.9),
        (r"identity\s*card", 0.7),
        (r"\brace\b", 0.3),
        (r"\b(finger|thumb)\s*print", 0.2),
        (r"\bdate\s*of\s*birth\b", 0.2),
        # --- back face ---
        (r"\bnric\s*no\b", 0.1),
        (r"\bdate\s*of\s*issue\b", 0.1),
        # both labels on the same page, either order: the back-face signature
        (
            r"(?s)\bnric\s*no\b.{0,1500}?\bdate\s*of\s*issue\b"
            r"|\bdate\s*of\s*issue\b.{0,1500}?\bnric\s*no\b",
            0.3,
        ),
    ],
    "passport": [
        (r"\bpassport\s*no\.?\b", 0.9),
        (r"\bP<[A-Z]{3}", 0.9),
        (r"\bpassport\b", 0.8),
        (r"country\s*code", 0.5),
        (r"date\s*of\s*expiry", 0.3),
        (r"\bnationality\b", 0.2),
        (r"\bdate\s*of\s*birth\b", 0.2),
    ],
    "fatca_crs_declaration": [
        (r"\bfatca\b", 0.9),
        (r"\bcrs\b", 0.6),
        (r"self[\s-]?certification", 0.7),
        (r"tax\s*residency", 0.8),
    ],
    "certificate_of_incumbency": [
        (r"certificate\s*of\s*incumbency", 0.95),
        # Identifies ACRA as the ISSUER, not the document type — it appears
        # on every ACRA-issued PDF, incorporation certificates included. At
        # 0.9 it combined with `\buen\b` (0.2) to score a capped 1.0 for
        # incumbency on any ACRA document, outranking the real type. Kept
        # only as weak corroboration.
        (r"accounting\s*and\s*corporate\s*regulatory\s*authority", 0.2),
        (r"business\s*profile", 0.8),
        (r"\bbizfile\b", 0.7),
        (r"\bincumbency\b", 0.6),
        (r"duly\s*incorporated\s*and\s*validly\s*existing", 0.4),
        (r"\buen\b", 0.2),
    ],
    "company_constitution": [
        (r"memorandum\s*and\s*articles", 0.85),
        (r"articles\s*of\s*association", 0.85),
        (r"\bconstitution\b", 0.85),
    ],
    "proof_of_address": [
        (r"proof\s*of\s*address", 0.9),
        (r"utility\s*bill", 0.7),
        (r"billing\s*address", 0.5),
        (r"residential\s*address", 0.5),
    ],
    "source_of_wealth": [
        (r"source\s*of\s*wealth", 0.9),
        (r"\bsow\b", 0.4),
        (r"wealth\s*(declaration|summary)", 0.5),
        (r"statements?\s*of\s*comprehensive\s*income", 0.7),
        (r"statements?\s*of\s*financial\s*position", 0.7),
        (r"notes\s*to\s*the\s*financial\s*statements", 0.6),
        (r"\bbalance\s*sheet\b", 0.5),
        (r"\bincome\s*statement\b", 0.5),
        (r"\bmargin\s*account\b", 0.6),
        (r"\bnet\s*asset\s*value\b", 0.5),
        (r"\bportfolio\s*value\b", 0.4),
    ],
    "credit_report": [
        (r"credit\s*report", 0.9),
        (r"credit\s*bureau", 0.9),
        (r"\bccris\b", 0.6),
        (r"credit\s*score", 0.6),
    ],
    "list_of_loans": [
        (r"list\s*of\s*loans", 0.9),
        (r"loan\s*(list|schedule)", 0.8),
        (r"outstanding\s*loans", 0.6),
        (r"facility\s*(offer|letter|agreement)", 0.6),
        (r"overdraft\s*facility", 0.6),
        (r"credit\s*facility", 0.5),
        (r"\b(loan|overdraft)s?\b", 0.4),
    ],
}

# Singapore-specific residential-address heuristics — deliberately separate
# from _TEXT_RULES, since "does this show a residential address" is
# orthogonal to document_type (an id_document or bank_statement can carry
# this signal without it being their primary category). Each tuple is
# (pattern, weight, human-readable label) — the label is what actually
# reaches the classifier prompt, appended as a plain sentence in `notes`.
_ADDRESS_POSITIVE_RULES = [
    (r"#\s*\d{1,3}\s*-\s*\d{1,5}", 0.6, "unit number format (e.g. #12-345)"),
    (r"\bblk\.?\s*\d+\b", 0.4, "HDB block number"),
    (r"\bblock\s*\d+\b", 0.4, "HDB block number"),
    (r"\bsingapore\s*\d{6}\b", 0.5, "Singapore postal code"),
    (r"\bresidential\s*address\b", 0.7, "explicit 'residential address' label"),
    (r"\bhome\s*address\b", 0.6, "explicit 'home address' label"),
    (
        r"\b\d{1,4}\s+\w+\s+"
        r"(road|street|avenue|ave|drive|lane|close|crescent|walk|way|terrace|grove)\b",
        0.3,
        "street address format",
    ),
]

# Disqualifying signals — an address block near these is a business/office
# address, not the person's residence, even if it otherwise looks addressy.
# Weighted high enough to override the generic unit-number/postal-code
# matches on their own (those alone are only 0.6+0.5=1.1, capped to 1.0) —
# a registered-office disclaimer should decisively cancel that out, not
# just dent it, since a corporate statement's office address will always
# incidentally match the generic SG-address-format patterns.
_ADDRESS_NEGATIVE_RULES = [
    (r"registered\s*office", 1.0, "'registered office' wording"),
    (r"\bbusiness\s*address\b", 0.9, "'business address' wording"),
    (r"\bcompany'?s?\s*(registered\s*)?address\b", 0.9, "company address wording"),
    (r"\bcorporate\s*address\b", 0.9, "'corporate address' wording"),
    (r"\boffice\s*address\b", 0.9, "'office address' wording"),
]


def _compile(rules):
    return {
        doc_type: [(re.compile(pattern, re.IGNORECASE), weight) for pattern, weight in patterns]
        for doc_type, patterns in rules.items()
    }


def _compile_flat(rules):
    return [(re.compile(pattern, re.IGNORECASE), weight, label) for pattern, weight, label in rules]


_COMPILED_FILENAME_RULES = _compile(_FILENAME_RULES)
_COMPILED_TEXT_RULES = _compile(_TEXT_RULES)
_COMPILED_ADDRESS_POSITIVE = _compile_flat(_ADDRESS_POSITIVE_RULES)
_COMPILED_ADDRESS_NEGATIVE = _compile_flat(_ADDRESS_NEGATIVE_RULES)


# --- "Certain" tier (langgraph only — no Dify equivalent) ------------------
#
# Deliberately NOT "most likely" signals (that's what _TEXT_RULES above is
# for) — each pattern here was checked against every generator under
# backend/doc_gen_scripts/ (the actual synthetic corpus this classifier is
# evaluated against) and only kept if it appears in exactly one document
# type's *rendered output* there (source comments/docstrings mentioning a
# word don't count — they never reach the PDF text a real document produces).
# See LANGGRAPH_MIGRATION_PLAN.md section 2 for why this exists: a document
# that trips one of these needs neither llm_classify nor the Sonnet agent —
# rule_classify alone is enough, with a plain-language justification of
# which decisive phrase matched (see classify_by_rules' "certain_type").
#
# Keep this tier small and load-bearing only for phrases that are exact
# document titles/mastheads or fixed regulator wording — never a single
# generic word, and never something a legal boilerplate clause in an
# unrelated document could incidentally quote (see company_constitution's
# note below for why "constitution" made the cut despite being one word).
_CERTAIN_TEXT_RULES = {
    # Machine-readable-zone line — a fixed-format artifact of passport
    # biodata pages, not prose, so no other document type can produce it.
    "passport": [r"\bP<[A-Z]{3}[A-Z0-9<]{20,}"],
    # NRIC's own masthead ("REPUBLIC OF SINGAPORE") also appears on a
    # passport and on a constitution's cover page, so it's not certain
    # alone — but neither of those documents is a form with "IDENTITY CARD
    # NO." on it, so requiring both nearby each other is decisive.
    "nric": [r"(?s)republic\s+of\s+singapore.{0,300}identity\s+card\s+no"],
    # Title only — the "Asst Registrar of Companies" signature line is printed
    # on the incumbency/business profile too (with or without the period,
    # depending on the source), so it can't tell the two apart.
    "certificate_of_incorporation": [r"certificate\s*confirming\s*incorporation", r"certificate\s*of\s*incorporation"],
    "certificate_of_incumbency": [r"business\s*profile\s*\(company\)\s*of"],
    # A one-word signal, exceptionally — verified against every other
    # generator's *rendered* text (not source comments) with none matching.
    "company_constitution": [r"\bconstitution\b"],
    "source_of_wealth": [
        r"statement\s*of\s*comprehensive\s*income",
        r"statement\s*of\s*financial\s*position",
        r"notes\s*to\s*the\s*financial\s*statements",
    ],
    "credit_report": [r"enhanced\s*consumer\s*credit\s*report"],
    "fatca_crs_declaration": [r"\bfatca\b"],
    "list_of_loans": [r"letter\s*of\s*offer.{0,80}facility"],
    "payslip": [r"payroll\s*statement"],
    "bank_statement": [r"statement\s*of\s*account"],
    "notice_of_assessment": [r"comptroller\s*of\s*income\s*tax"],
}
_COMPILED_CERTAIN_TEXT_RULES = {
    doc_type: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for doc_type, patterns in _CERTAIN_TEXT_RULES.items()
}


def _certain_signal(text):
    """
    Returns (document_type, [matched phrases]) if exactly one document type
    tripped a certain-tier pattern, else None. More than one type matching
    is treated as ambiguous on purpose (e.g. a document that quotes another
    type's boilerplate) rather than guessing between them.
    """
    hits: dict[str, list[str]] = {}
    for doc_type, patterns in _COMPILED_CERTAIN_TEXT_RULES.items():
        matched = [" ".join(m.group(0).split()) for p in patterns if (m := p.search(text))]
        if matched:
            hits[doc_type] = matched
    if len(hits) == 1:
        ((doc_type, matched),) = hits.items()
        return doc_type, matched
    return None


def _residential_address_evidence(text):
    """
    Returns 0-1 plain-language sentences describing whether `text` appears
    to show a residential address — returned in `notes` (document-level,
    independent of document_type). classify_support.address_check reads
    these back to decide the fast-path/vision-escalation question.
    """
    positive_hits = [(w, label) for regex, w, label in _COMPILED_ADDRESS_POSITIVE if regex.search(text)]
    negative_hits = [(w, label) for regex, w, label in _COMPILED_ADDRESS_NEGATIVE if regex.search(text)]

    positive_score = min(1.0, sum(w for w, _ in positive_hits))
    negative_score = min(1.0, sum(w for w, _ in negative_hits))
    net_score = max(0.0, positive_score - negative_score)

    if not positive_hits and not negative_hits:
        return []

    # Deliberately conservative: some positive signal AND net score clears
    # a bar, so a stray "Road" match alone doesn't count, and a
    # registered-office disclaimer can cancel out a real-looking match.
    has_residential_address = positive_score > 0 and net_score >= 0.4
    matched_labels = ", ".join(label for _, label in positive_hits) or "none"

    if has_residential_address:
        return [f"Proof of address check: appears to show a residential address ({matched_labels})"]
    if negative_hits:
        return ["Proof of address check: address present but appears to be a business/office address, not residential"]
    return ["Proof of address check: no residential address detected"]


def _normalise_filename(filename):
    stem = filename.rsplit(".", 1)[0]
    stem = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem)
    return stem.replace("_", " ").replace("-", " ")


TOP_K = 3


def _score_all(text, compiled_rules):
    """{doc_type: (score, [matched wording, ...])} for every type with >=1 hit."""
    scores = {}
    for doc_type, patterns in compiled_rules.items():
        hits = []
        for regex, weight in patterns:
            m = regex.search(text)
            if m:
                hits.append((" ".join(m.group(0).split()).lower(), weight))
        if hits:
            score = min(1.0, sum(weight for _, weight in hits))
            scores[doc_type] = (score, list(dict.fromkeys(w for w, _ in hits)))
    return scores


def _combine(f_score, t_score):
    if f_score and t_score:
        return min(1.0, max(f_score, t_score) + min(f_score, t_score) * TEXT_AGREEMENT_BOOST)
    return f_score or t_score


def _reasoning(f_score, t_score, strength, has_text):
    if f_score and t_score:
        return (
            f"Filename and text rules both point to this type "
            f"(filename {f_score:.2f}, text {t_score:.2f}); combined strength {strength:.2f}."
        )
    if f_score:
        tail = "no text rules fired for it" if has_text else "no extracted text was supplied"
        return f"Filename rules only (strength {f_score:.2f}); {tail}."
    return f"Text rules only (strength {t_score:.2f}); filename gave no signal for this type."


def _normalise(candidates):
    """Adds rank + confidence (relative share, sums to exactly 1.0)."""
    if not candidates:
        return candidates
    total = sum(c["match_strength"] for c in candidates)
    if total > 0:
        shares = [c["match_strength"] / total for c in candidates]
    else:
        shares = [1.0 / len(candidates)] * len(candidates)
    rounded = [round(s, 4) for s in shares]
    rounded[0] = round(rounded[0] + (1.0 - sum(rounded)), 4)  # absorb rounding drift
    out = []
    for i, (c, conf) in enumerate(zip(candidates, rounded)):
        out.append({
            "rank": i + 1,
            "document_type": c["document_type"],
            "confidence": conf,
            "match_strength": round(c["match_strength"], 4),
            "reasoning": c["reasoning"],
            "evidence": c["evidence"],
        })
    return out


def classify_by_rules(filename: str, extracted_text: str = "", confidence_threshold: float = 0.6) -> dict:
    """Port of rule_classify_v3's `main`. Pure function, no I/O."""
    threshold = float(confidence_threshold)
    text = (extracted_text or "")[:MAX_TEXT_CHARS]

    f_scores = _score_all(_normalise_filename(filename), _COMPILED_FILENAME_RULES)
    t_scores = _score_all(text, _COMPILED_TEXT_RULES) if text else {}

    candidates = []
    for doc_type in set(f_scores) | set(t_scores):
        f_score, f_hits = f_scores.get(doc_type, (0.0, []))
        t_score, t_hits = t_scores.get(doc_type, (0.0, []))
        strength = _combine(f_score, t_score)
        candidates.append({
            "document_type": doc_type,
            "match_strength": strength,
            "reasoning": _reasoning(f_score, t_score, strength, bool(text)),
            "evidence": [f'filename: "{h}"' for h in f_hits] + [f'text: "{h}"' for h in t_hits],
        })

    # prefer more distinct hits
    candidates.sort(key=lambda c: (-c["match_strength"], -len(c["evidence"]), c["document_type"]))
    top = _normalise(candidates[:TOP_K])

    notes = []
    if not top:
        notes.append("No filename or text rule matched any document type.")
    if text:
        notes += _residential_address_evidence(text)

    certain_type: str | None = None
    certain_evidence: list[str] = []
    if text:
        certain = _certain_signal(text)
        if certain:
            certain_type, certain_evidence = certain

    return {
        "top_candidates": top,
        "needs_manual_review": (not top) or top[0]["match_strength"] < threshold,
        "notes": notes,
        # Set only when a langgraph-only "certain" pattern fired (see
        # _CERTAIN_TEXT_RULES) — None for every Dify-equivalent path, so
        # this key is additive and doesn't change rule_classify_v3 parity.
        "certain_type": certain_type,
        "certain_evidence": certain_evidence,
    }
