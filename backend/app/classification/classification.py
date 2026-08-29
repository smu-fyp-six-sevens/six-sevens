from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx
from pydantic import BaseModel

from ..config import settings
from ..models import DocumentType

logger = logging.getLogger("classifier_pipeline.classification")

# ---------------------------------------------------------------------------
# Three-layer classification:
#
#   1. classify_by_filename — cheap regex match against the filename.
#   2. classify_by_text     — regex match against extracted document text;
#                              body text is stronger evidence than a
#                              filename, so a hit here outweighs/boosts
#                              layer 1's guess.
#   3. classify_by_llm      — DeepSeek reads the extracted text and
#                              classifies by understanding content rather
#                              than keyword matching; the strongest
#                              evidence of the three, so it outweighs/
#                              boosts layers 1+2's combined guess. Unlike
#                              the other two layers this one does network
#                              I/O, so it's async and its failure doesn't
#                              take down the rest of classify_document().
#
# classify_document() runs all three and merges them. Layers 2+3 are
# skipped when no extracted text is supplied, falling back to the
# filename-only result.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern[str]
    weight: float


def _compile_rules(
    rules: dict[DocumentType, list[tuple[str, float]]],
) -> dict[DocumentType, list[_Pattern]]:
    return {
        doc_type: [_Pattern(re.compile(pattern, re.IGNORECASE), weight) for pattern, weight in patterns]
        for doc_type, patterns in rules.items()
    }


def _best_match(
    text: str, compiled_rules: dict[DocumentType, list[_Pattern]]
) -> tuple[DocumentType | None, float, list[str]]:
    """Score every document type against `text`, keeping the top scorer."""
    best_type: DocumentType | None = None
    best_score = 0.0
    best_matches: list[str] = []

    for doc_type, patterns in compiled_rules.items():
        matches = [p.regex.pattern for p in patterns if p.regex.search(text)]
        if not matches:
            continue
        score = min(1.0, sum(p.weight for p in patterns if p.regex.search(text)))
        if score > best_score:
            best_type, best_score, best_matches = doc_type, score, matches

    return best_type, best_score, best_matches


class ClassificationResult(BaseModel):
    document_type: DocumentType | None
    confidence: float
    matched_keywords: list[str] = []
    needs_manual_review: bool = True
    # Only ever populated by classify_by_llm — the regex layers have no
    # reasoning to report, just matched patterns.
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Layer 1: filename
# ---------------------------------------------------------------------------

# Each document type is scored against the filename using weighted
# keyword/phrase patterns. A filename can match several patterns for the
# same type; matched weights are summed and capped at 1.0 to produce the
# confidence score.
_FILENAME_RULES: dict[DocumentType, list[tuple[str, float]]] = {
    DocumentType.BANK_STATEMENT: [
        (r"bank\s*statement", 0.9),
        (r"\bstatement\b", 0.4),
        (r"\b(dbs|ocbc|uob|hsbc|citibank|maybank|cimb)\b", 0.3),
    ],
    DocumentType.CERTIFICATE_OF_INCORPORATION: [
        (r"certificate\s*of\s*incorporation", 0.9),
        (r"\bincorporation\b", 0.5),
    ],
    DocumentType.PAYSLIP: [
        (r"pay\s*slip", 0.9),
        (r"salary\s*slip", 0.9),
        (r"\bpayroll\b", 0.4),
    ],
    DocumentType.ID_DOCUMENT: [
        (r"\bnric\b", 0.9),
        (r"\bpassport\b", 0.9),
        (r"identity\s*card", 0.7),
        (r"\b(director|shareholder|ubo)\b", 0.2),
    ],
    DocumentType.FATCA_CRS_DECLARATION: [
        (r"\bfatca\b", 0.9),
        (r"\bcrs\b", 0.6),
        (r"self[\s-]?declaration", 0.5),
    ],
    DocumentType.CERTIFICATE_OF_INCUMBENCY: [
        (r"certificate\s*of\s*incumbency", 0.95),
        (r"\bincumbency\b", 0.6),
    ],
    DocumentType.COMPANY_CONSTITUTION: [
        (r"\bconstitution\b", 0.8),
        (r"memorandum\s*and\s*articles", 0.8),
        (r"articles\s*of\s*association", 0.8),
        (r"\bm\s*&?\s*aa\b", 0.5),
    ],
    DocumentType.PROOF_OF_ADDRESS: [
        (r"proof\s*of\s*address", 0.9),
        (r"utility\s*bill", 0.7),
        (r"residential\s*address", 0.6),
    ],
    DocumentType.SOURCE_OF_WEALTH: [
        (r"source\s*of\s*wealth", 0.9),
        (r"\bsow\b", 0.5),
    ],
    DocumentType.CREDIT_REPORT: [
        (r"credit\s*report", 0.9),
        (r"credit\s*bureau", 0.7),
        (r"\bccris\b", 0.6),
    ],
    DocumentType.LIST_OF_LOANS: [
        (r"list\s*of\s*loans", 0.9),
        (r"loan\s*(list|schedule)", 0.8),
        (r"outstanding\s*loans", 0.6),
        (r"\bloans?\b", 0.4),
    ],
}

_COMPILED_FILENAME_RULES = _compile_rules(_FILENAME_RULES)


def _normalise_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ")


def classify_by_filename(
    filename: str, *, confidence_threshold: float | None = None
) -> ClassificationResult:
    """
    Layer 1: rule-based, filename-only classifier.

    Scores every document type by summing the weights of its matched
    patterns (capped at 1.0) and keeps the highest-scoring type as the
    best guess, regardless of whether it clears the confidence threshold
    — a low-confidence guess is still useful context for manual review.
    `needs_manual_review` is what gates whether the document is treated
    as recognised: true if nothing matched, or if the best score falls
    below the threshold.
    """
    threshold = (
        confidence_threshold
        if confidence_threshold is not None
        else settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
    )
    normalised = _normalise_filename(filename)
    best_type, best_score, best_matches = _best_match(normalised, _COMPILED_FILENAME_RULES)

    return ClassificationResult(
        document_type=best_type,
        confidence=best_score,
        matched_keywords=best_matches,
        needs_manual_review=best_type is None or best_score < threshold,
    )


# ---------------------------------------------------------------------------
# Layer 2: extracted text identifiers
# ---------------------------------------------------------------------------

# Phrases that tend to appear verbatim in a document's own body/header —
# generally weighted higher than the equivalent filename pattern, since
# finding "bank statement" inside the document itself is much stronger
# evidence than finding it in the filename.
_TEXT_RULES: dict[DocumentType, list[tuple[str, float]]] = {
    DocumentType.BANK_STATEMENT: [
        (r"bank\s*statement", 0.95),
        (r"opening\s*balance", 0.35),
        (r"closing\s*balance", 0.35),
        (r"\btransaction\s*history\b", 0.25),
    ],
    DocumentType.CERTIFICATE_OF_INCORPORATION: [
        (r"certificate\s*of\s*incorporation", 0.95),
        (r"company\s*registration\s*number", 0.3),
        (r"incorporated\s*(under|in)\b", 0.3),
    ],
    DocumentType.PAYSLIP: [
        (r"pay\s*slip", 0.9),
        (r"salary\s*slip", 0.9),
        (r"\bnet\s*pay\b", 0.3),
        (r"\bgross\s*pay\b", 0.3),
        (r"\bbasic\s*salary\b", 0.3),
    ],
    DocumentType.ID_DOCUMENT: [
        (r"\bnric\b", 0.9),
        (r"\bpassport\s*no\.?\b", 0.9),
        (r"identity\s*card", 0.7),
        (r"\bdate\s*of\s*birth\b", 0.2),
        (r"\bnationality\b", 0.2),
    ],
    DocumentType.FATCA_CRS_DECLARATION: [
        (r"\bfatca\b", 0.9),
        (r"\bcrs\b", 0.6),
        (r"self[\s-]?certification", 0.5),
        (r"tax\s*residency", 0.4),
    ],
    DocumentType.CERTIFICATE_OF_INCUMBENCY: [
        (r"certificate\s*of\s*incumbency", 0.95),
        (r"\bincumbency\b", 0.6),
        (r"duly\s*incorporated\s*and\s*validly\s*existing", 0.4),
    ],
    DocumentType.COMPANY_CONSTITUTION: [
        (r"memorandum\s*and\s*articles", 0.85),
        (r"articles\s*of\s*association", 0.85),
        (r"\bconstitution\b", 0.5),
    ],
    DocumentType.PROOF_OF_ADDRESS: [
        (r"proof\s*of\s*address", 0.9),
        (r"utility\s*bill", 0.7),
        (r"billing\s*address", 0.4),
        (r"residential\s*address", 0.5),
    ],
    DocumentType.SOURCE_OF_WEALTH: [
        (r"source\s*of\s*wealth", 0.9),
        (r"\bsow\b", 0.4),
        (r"wealth\s*(declaration|summary)", 0.5),
    ],
    DocumentType.CREDIT_REPORT: [
        (r"credit\s*report", 0.9),
        (r"credit\s*bureau", 0.7),
        (r"\bccris\b", 0.6),
        (r"credit\s*score", 0.4),
    ],
    DocumentType.LIST_OF_LOANS: [
        (r"list\s*of\s*loans", 0.9),
        (r"outstanding\s*loans", 0.6),
        (r"loan\s*(schedule|summary)", 0.6),
        (r"\bloan\s*balance\b", 0.3),
    ],
}

_COMPILED_TEXT_RULES = _compile_rules(_TEXT_RULES)

# How much of the text-regex layer's own confidence gets added on top of a
# filename guess when both layers agree on the same document type.
_TEXT_AGREEMENT_BOOST = 0.5


def classify_by_text(
    extracted_text: str, *, confidence_threshold: float | None = None
) -> ClassificationResult:
    """
    Layer 2: rule-based classifier over extracted document text.

    Same scoring approach as classify_by_filename, applied to the
    document's own extracted text instead of its filename. Callers
    typically don't use this in isolation — see classify_document(),
    which merges this layer's result with the filename layer's.
    """
    threshold = (
        confidence_threshold
        if confidence_threshold is not None
        else settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
    )
    best_type, best_score, best_matches = _best_match(extracted_text, _COMPILED_TEXT_RULES)

    return ClassificationResult(
        document_type=best_type,
        confidence=best_score,
        matched_keywords=best_matches,
        needs_manual_review=best_type is None or best_score < threshold,
    )


def _merge_layer(
    base: ClassificationResult, other: ClassificationResult, agreement_boost: float
) -> ClassificationResult:
    """
    Fold `other` (stronger evidence) into `base` (weaker/prior evidence).
    Agreement on the same type boosts confidence significantly; a
    confident match from `other` for a *different* type wins outright,
    since `other` is assumed more reliable than `base`. `needs_manual_review`
    is left for the caller to recompute against its threshold.
    """
    if other.document_type is None:
        return base

    if other.document_type == base.document_type:
        boosted_confidence = min(1.0, base.confidence + other.confidence * agreement_boost)
        merged_keywords = list(dict.fromkeys(base.matched_keywords + other.matched_keywords))
        return ClassificationResult(
            document_type=other.document_type,
            confidence=boosted_confidence,
            matched_keywords=merged_keywords,
        )

    if other.confidence >= base.confidence:
        return other
    return base


# ---------------------------------------------------------------------------
# Layer 3: LLM classification (DeepSeek)
# ---------------------------------------------------------------------------

# How much of the LLM layer's own confidence gets added on top of the
# filename+text-regex guess when they agree on the same document type.
# Weighted higher than the text-regex boost — the LLM reads for meaning
# rather than matching fixed phrases, so its agreement is stronger evidence.
_LLM_AGREEMENT_BOOST = 0.6

_DEEPSEEK_SYSTEM_PROMPT = (
    "You are a document classifier for a KYC/onboarding pipeline. Given the "
    "extracted text of a document, decide which single category it belongs "
    "to. Judge by meaning, not just keyword matches — the text may come "
    "from noisy OCR or use different phrasing than the category name. "
    "A document that merely mentions or lists other document types (e.g. a "
    "cover letter or email saying documents are attached) is NOT itself one "
    "of those types — classify what the text actually is, not what it "
    "refers to.\n\n"
    "Categories:\n" + "\n".join(f"- {doc_type.value}" for doc_type in DocumentType) + "\n\n"
    "Respond with ONLY a JSON object (no other text) of this shape:\n"
    '{"document_type": "<one of the category values above, or null if none '
    'clearly match>", "confidence": <float 0.0-1.0>, "evidence": ["short '
    'phrase or reason", ...], "reasoning": "<one short sentence explaining '
    'the decision>"}'
)


class DeepSeekClassificationError(Exception):
    """Raised when the DeepSeek classification call fails or returns unusable output."""


async def classify_by_llm(
    extracted_text: str, *, confidence_threshold: float | None = None
) -> ClassificationResult:
    """
    Layer 3: LLM-based classifier over extracted document text.

    Sends the text to DeepSeek and asks it to classify by understanding
    content, catching cases layers 1+2's regex matching misses (OCR noise,
    paraphrasing, non-English documents, etc). Same ClassificationResult
    contract as the other layers so classify_document() can merge it the
    same way. Raises DeepSeekClassificationError on any request failure or
    unparseable response — callers should treat that as "layer unavailable"
    rather than "no match", since it says nothing about the document itself.
    """
    threshold = (
        confidence_threshold
        if confidence_threshold is not None
        else settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
    )

    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": _DEEPSEEK_SYSTEM_PROMPT},
            {"role": "user", "content": extracted_text},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            raise DeepSeekClassificationError(f"DeepSeek request failed: {exc}") from exc

    try:
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        raw_type = parsed.get("document_type")
        document_type = DocumentType(raw_type) if raw_type else None
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        evidence = [str(item) for item in parsed.get("evidence", [])]
        reasoning = str(parsed.get("reasoning", ""))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DeepSeekClassificationError(f"DeepSeek returned unusable output: {exc}") from exc

    if document_type is None:
        confidence = 0.0
        evidence = []

    return ClassificationResult(
        document_type=document_type,
        confidence=confidence,
        matched_keywords=evidence,
        needs_manual_review=document_type is None or confidence < threshold,
        reasoning=reasoning,
    )


async def classify_document(
    filename: str,
    extracted_text: str | None = None,
    *,
    confidence_threshold: float | None = None,
) -> ClassificationResult:
    """
    Runs all three layers and merges them.

    extracted_text is optional — when it isn't supplied, this falls back to
    the filename-only result (layers 2+3 both need it). When it is
    supplied, the text-regex layer boosts/overrides the filename guess,
    then the LLM layer boosts/overrides that combined guess the same way —
    each layer trusted more than the one before it. If the LLM call fails,
    classification falls back to the filename+text-regex result rather than
    failing the whole document.
    """
    threshold = (
        confidence_threshold
        if confidence_threshold is not None
        else settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
    )

    filename_result = classify_by_filename(filename, confidence_threshold=threshold)
    if not extracted_text:
        return filename_result

    text_result = classify_by_text(extracted_text, confidence_threshold=threshold)
    combined = _merge_layer(filename_result, text_result, _TEXT_AGREEMENT_BOOST)

    try:
        llm_result = await classify_by_llm(extracted_text, confidence_threshold=threshold)
    except DeepSeekClassificationError:
        logger.warning("LLM classification layer unavailable; using filename+text result only", exc_info=True)
    else:
        combined = _merge_layer(combined, llm_result, _LLM_AGREEMENT_BOOST)

    return ClassificationResult(
        document_type=combined.document_type,
        confidence=combined.confidence,
        matched_keywords=combined.matched_keywords,
        needs_manual_review=combined.document_type is None or combined.confidence < threshold,
    )
