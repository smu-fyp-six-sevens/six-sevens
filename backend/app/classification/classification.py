from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from ..config import settings
from ..models import DocumentType

# ---------------------------------------------------------------------------
# Two-layer classification so far:
#
#   1. classify_by_filename — cheap regex match against the filename.
#   2. classify_by_text     — regex match against extracted document text;
#                              body text is stronger evidence than a
#                              filename, so a hit here outweighs/boosts
#                              layer 1's guess.
#
# classify_document() runs both and merges them. Layer 2 is skipped when no
# extracted text is supplied, falling back to the filename-only result.
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

# How much of the text layer's own confidence gets added on top of a
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


def _merge_filename_and_text(
    filename_result: ClassificationResult, text_result: ClassificationResult
) -> ClassificationResult:
    """
    Combine layers 1 and 2. Extracted text outranks a filename guess:
    agreement on the same type boosts confidence significantly; a
    confident text match for a *different* type than the filename wins
    outright, since actual document content is more reliable than a
    filename. `needs_manual_review` is left for the caller to recompute
    against its threshold.
    """
    if text_result.document_type is None:
        return filename_result

    if text_result.document_type == filename_result.document_type:
        boosted_confidence = min(
            1.0, filename_result.confidence + text_result.confidence * _TEXT_AGREEMENT_BOOST
        )
        merged_keywords = list(
            dict.fromkeys(filename_result.matched_keywords + text_result.matched_keywords)
        )
        return ClassificationResult(
            document_type=text_result.document_type,
            confidence=boosted_confidence,
            matched_keywords=merged_keywords,
        )

    if text_result.confidence >= filename_result.confidence:
        return text_result
    return filename_result


def classify_document(
    filename: str,
    extracted_text: str | None = None,
    *,
    confidence_threshold: float | None = None,
) -> ClassificationResult:
    """
    Runs the filename and text-identifier layers and merges them.

    extracted_text is optional — when it isn't supplied (or carries no
    recognisable identifiers), this falls back to the filename-only
    result. When it is supplied, a matching text identifier boosts
    confidence in the filename's guess, or overrides it outright if the
    text points to a different, more strongly-evidenced type.
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
    combined = _merge_filename_and_text(filename_result, text_result)

    return ClassificationResult(
        document_type=combined.document_type,
        confidence=combined.confidence,
        matched_keywords=combined.matched_keywords,
        needs_manual_review=combined.document_type is None or combined.confidence < threshold,
    )
