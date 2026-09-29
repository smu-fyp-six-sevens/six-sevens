# spelling_anomaly.py
from __future__ import annotations

import asyncio
import importlib.resources
import json
import logging
import mimetypes
import re

from pydantic import BaseModel
from symspellpy import SymSpell, Verbosity

from ..ingestion.ingestion_service import ingest_document
from .domain_terms import DOMAIN_TERMS
from .font_consistency import ExtractedField

logger = logging.getLogger("fraud_detection.spelling_anomaly")

MAX_EDIT_DISTANCE = 2
MIN_WORD_LENGTH = 2  # drops single-letter tokens (initials, OCR artifacts)

# Loaded once at module import — same "shared instance" pattern as
# ocr_extractor.py's module-level `reader = RapidOCR()` — SymSpell's
# dictionary load is the expensive part, not each individual lookup.
_sym_spell = SymSpell(max_dictionary_edit_distance=MAX_EDIT_DISTANCE, prefix_length=7)
_DICTIONARY_PATH = importlib.resources.files("symspellpy") / "frequency_dictionary_en_82_765.txt"
_sym_spell.load_dictionary(str(_DICTIONARY_PATH), term_index=0, count_index=1)

_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_TRAILING_HYPHENATED_WORD_RE = re.compile(r"[A-Za-z]{2,}-$")
_LEADING_WORD_RE = re.compile(r"^[a-z]+")


class SpellingAnomaly(BaseModel):
    word: str
    surrounding_text: str
    suggested_correction: str | None = None


class SpellingAnomalyResult(BaseModel):
    document_id: str
    anomalies: list[SpellingAnomaly]
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


class _Candidate(BaseModel):
    """
    A word spelling_anomaly's own SymSpell+dictionary pass couldn't
    confidently classify as correctly spelled — either a real typo, or a
    legitimate rare word (a name, an address, unusual-but-real corporate
    terminology) that just isn't in either dictionary. See
    _looks_like_probable_proper_noun for how these two get told apart
    without an LLM. page/section are internal bookkeeping only (dedup key
    while scanning) — not carried into the public SpellingAnomaly result.
    """

    page: int
    section: int
    word: str
    surrounding_text: str
    suggested_correction: str | None = None


def _empty_result(document_id: str, reason: str) -> SpellingAnomalyResult:
    return SpellingAnomalyResult(
        document_id=document_id,
        anomalies=[],
        unable_to_assess=True,
        unable_to_assess_reason=reason,
    )


def _content_type_from_filename(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _dehyphenate_spans(texts: list[str]) -> list[str]:
    """
    A span ending in "<word>-" immediately followed by a lowercase-starting
    next span is treated as a word split across a line break (a real
    hyphenated compound word ending a sentence is normally followed by a
    capital letter or punctuation, not a lowercase continuation) — the
    hyphen is dropped and the fragments joined so the pair tokenizes as one
    whole word. Heuristic, not a full typesetting-aware solve; the
    reassembled word is attributed to the first span's (page, section).
    """
    texts = list(texts)
    for i in range(len(texts) - 1):
        stripped = texts[i].rstrip()
        trailing_match = _TRAILING_HYPHENATED_WORD_RE.search(stripped)
        if not trailing_match:
            continue
        leading_match = _LEADING_WORD_RE.match(texts[i + 1])
        if not leading_match:
            continue
        fragment = trailing_match.group(0)[:-1]  # drop the trailing hyphen
        texts[i] = stripped[: trailing_match.start()] + fragment + leading_match.group(0)
        texts[i + 1] = texts[i + 1][leading_match.end():]
    return texts


def _tokenize(text: str) -> list[str]:
    return [word for word in _WORD_RE.findall(text) if len(word) >= MIN_WORD_LENGTH]


def _decode_field_text(value: object) -> str | None:
    """
    field_value comes back from the JSON-typed extracted_fields column as
    whatever native type the stored JSON value is, and some string values
    are themselves JSON-encoded a second time (e.g. '"ACME Pte Ltd"' instead
    of 'ACME Pte Ltd') — same quirk font_consistency._field_search_text
    documents. One decode attempt, falling back to the value itself.
    """
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            decoded = value
    else:
        decoded = value
    return decoded if isinstance(decoded, str) else None


def _known_terms_from_extracted_fields(fields: list[ExtractedField] | None) -> set[str]:
    """
    Words drawn from the document's own already-extracted field values
    (individual_name, company_name, individual_address, etc. — from the
    Extraction LLM call every document already goes through, no extra call
    needed here). A candidate matching one of these is treated as a known
    legitimate term rather than an anomaly: in a KYC document, most of what
    SymSpell doesn't recognise is a proper noun the Extraction stage
    already identified and named, not a typo — this is the primary signal
    that replaces sending ambiguous words to an LLM for judgement.
    """
    known: set[str] = set()
    for field in fields or []:
        text = _decode_field_text(field.field_value)
        if text is None:
            continue
        known.update(word.lower() for word in _tokenize(text))
    return known


def _find_candidates(pages: list[dict], known_terms: set[str]) -> list[_Candidate]:
    flat_spans: list[tuple[int, int, str]] = []
    for page in pages:
        page_number = page.get("page_number", 0)
        elements = page.get("elements") or [{"text": page.get("text", "")}]
        texts = _dehyphenate_spans([element.get("text", "") for element in elements])
        for section, text in enumerate(texts):
            flat_spans.append((page_number, section, text))

    candidates: list[_Candidate] = []
    seen_in_span: set[tuple[int, int, str]] = set()
    for page_number, section, text in flat_spans:
        for word in _tokenize(text):
            normalized = word.lower()
            key = (page_number, section, normalized)
            if key in seen_in_span:
                continue

            if normalized in DOMAIN_TERMS or normalized in known_terms:
                continue

            suggestions = _sym_spell.lookup(
                normalized, Verbosity.CLOSEST, max_edit_distance=MAX_EDIT_DISTANCE
            )
            if suggestions and suggestions[0].term == normalized and suggestions[0].distance == 0:
                continue  # correctly spelled

            seen_in_span.add(key)
            candidates.append(
                _Candidate(
                    page=page_number,
                    section=section,
                    word=word,
                    surrounding_text=text,
                    suggested_correction=suggestions[0].term if suggestions else None,
                )
            )
    return candidates


def _looks_like_probable_proper_noun(candidate: _Candidate) -> bool:
    """
    A capitalised word with no SymSpell suggestion at all (nothing within
    edit distance 2 of any dictionary word) is characteristically a proper
    noun or other genuinely novel term rather than a typo — a typo of a
    real word is, by definition, usually close to that word, so something
    this "far" from the dictionary with no lowercase-common-word pattern is
    more likely a name/place SymSpell just doesn't know than a misspelling.

    This is the local, non-LLM stand-in for the typo-vs-legitimate-rare-
    word judgement call an agent would otherwise make — it trades some
    recall (a capitalised, badly-misspelled real word could slip through)
    for a large cut in false positives on ordinary KYC documents full of
    names, streets, and company names that _known_terms_from_extracted_fields
    doesn't happen to cover (e.g. a witness or co-signatory who isn't
    captured in any extracted field).
    """
    return candidate.word[:1].isupper() and candidate.suggested_correction is None


def _filter_candidates(candidates: list[_Candidate]) -> list[_Candidate]:
    return [c for c in candidates if not _looks_like_probable_proper_noun(c)]


async def check_spelling_anomalies(
    document_id: str,
    file_bytes: bytes,
    filename: str,
    extracted_fields: list[ExtractedField] | None = None,
) -> SpellingAnomalyResult:
    """
    AC1/AC6: extracts the document's text (reusing ingestion_service —
    the same native-PDF/OCR pipeline the main classification/extraction
    flow uses, rather than a second, separate extraction pass) and checks
    it for spelling anomalies, entirely locally (SymSpell + a domain-term
    allowlist + the document's own extracted fields + a proper-noun
    heuristic — see _looks_like_probable_proper_noun for why this doesn't
    need an LLM call). Unable to Assess (AC6) if no text comes back at all,
    e.g. an image-only PDF that OCR also fails to read.
    """
    content_type = _content_type_from_filename(filename)

    try:
        ingestion_result = await asyncio.to_thread(ingest_document, file_bytes, filename, content_type)
    except Exception as exc:
        reason = f"Text extraction failed: {type(exc).__name__}: {exc}"
        logger.warning("Document %s: %s", document_id, reason, exc_info=True)
        return _empty_result(document_id, reason)

    if not ingestion_result.get("raw_text", "").strip():
        return _empty_result(document_id, "No extractable text found in document")

    known_terms = _known_terms_from_extracted_fields(extracted_fields)
    candidates = await asyncio.to_thread(
        _find_candidates, ingestion_result.get("pages", []), known_terms
    )
    confirmed = _filter_candidates(candidates)

    return SpellingAnomalyResult(
        document_id=document_id,
        anomalies=[
            SpellingAnomaly(
                word=c.word,
                surrounding_text=c.surrounding_text,
                suggested_correction=c.suggested_correction,
            )
            for c in confirmed
        ],
        unable_to_assess=False,
    )
