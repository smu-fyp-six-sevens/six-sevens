from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from ..config import settings
from ..models import DocumentType


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern[str]
    weight: float


# Each document type is scored against the filename using weighted
# keyword/phrase patterns. A filename can match several patterns for the
# same type; matched weights are summed and capped at 1.0 to produce the
# confidence score. This is intentionally filename-only (for now)
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

_COMPILED_RULES: dict[DocumentType, list[_Pattern]] = {
    doc_type: [_Pattern(re.compile(pattern, re.IGNORECASE), weight) for pattern, weight in rules]
    for doc_type, rules in _FILENAME_RULES.items()
}


class ClassificationResult(BaseModel):
    document_type: DocumentType | None
    confidence: float
    matched_keywords: list[str]
    needs_manual_review: bool


def _normalise(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ")


def classify_by_filename(
    filename: str, *, confidence_threshold: float | None = None
) -> ClassificationResult:
    """
    Rule-based, filename-only classifier.

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
    normalised = _normalise(filename)

    best_type: DocumentType | None = None
    best_score = 0.0
    best_matches: list[str] = []

    for doc_type, patterns in _COMPILED_RULES.items():
        matches = [p.regex.pattern for p in patterns if p.regex.search(normalised)]
        if not matches:
            continue
        score = min(1.0, sum(p.weight for p in patterns if p.regex.search(normalised)))
        if score > best_score:
            best_type, best_score, best_matches = doc_type, score, matches

    return ClassificationResult(
        document_type=best_type,
        confidence=best_score,
        matched_keywords=best_matches,
        needs_manual_review=best_type is None or best_score < threshold,
    )
