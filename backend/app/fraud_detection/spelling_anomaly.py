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

# Letters touching a digit are part of a reference/account code
# ("EBACT51059664352", "4n3NIEdM"), not a word — the lookarounds stop the
# letters being pulled out of the middle of one.
_WORD_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]+(?:'[A-Za-z]+)?(?![A-Za-z0-9])")
_POSSESSIVE_RE = re.compile(r"'[sS]$")
_TRAILING_HYPHENATED_WORD_RE = re.compile(r"[A-Za-z]{2,}-$")
_TRAILING_WORD_RE = re.compile(r"[A-Za-z]{2,}$")
_LEADING_WORD_RE = re.compile(r"^[a-z]+")
# A short, fully-uppercase token (HH, GG, SGX, HDB...) reads as an acronym
# or a legend/grade code in a formal document, not prose a typo could occur
# in — even when it happens to be edit-distance-close to some unrelated
# short dictionary word (e.g. "HH" -> "he"). A lowercase plural "s" on one
# ("ATMs") is still an acronym.
_ACRONYM_RE = re.compile(r"^(?:[A-Z]{1,4}|[A-Z]{2,4}s)$")

# Text that isn't prose and gets blanked out before tokenizing: web
# addresses (split into "sg", "http", "acratrustbar"... otherwise), emails,
# passport MRZ lines ("PASGP<<MUHAMMAD<FAIZ<<<"), roman-numeral clause
# enumerators ("(iv)", "ix)"), and optional-plural suffixes
# ("beneficiary(ies)", "class(es)").
_URL_RE = re.compile(
    r"(?:https?://|www\.)\S*"
    r"|\S+@[\w-]+(?:\.[\w-]+)+"
    r"|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|gov|edu|sg|io|co|my)\b(?:/\S*)?",
    re.IGNORECASE,
)
_MRZ_RE = re.compile(r"\S*<\S*")
_ROMAN = r"(?=[ivxlcdm])m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
_ROMAN_ENUMERATOR_RE = re.compile(
    rf"\(\s*{_ROMAN}\s*\)|(?:^|(?<=\s)){_ROMAN}[.)](?=\s|$)", re.IGNORECASE
)
_PLURAL_SUFFIX_RE = re.compile(r"(?<=[A-Za-z])\((?:s|es|ies)\)", re.IGNORECASE)
# A company name ("Ivory Pharma Distribution Pte. Ltd.", "EVERBRIGHT
# CONSTRUCTION PTE LTD"): a run of capitalised words ending in a legal-entity
# suffix. Not spell-checked — coined/brand words are normal in company names,
# and a misspelt one is caught by cross-document name comparison instead.
_COMPANY_NAME_RE = re.compile(
    r"\b(?:[A-Z][\w&'.,-]*\s+){1,8}?"
    r"(?=[A-Z])(?i:pte\.?\s*ltd|private\s+limited|limited|ltd|llp|llc|inc|corporation|corp"
    r"|sdn\.?\s*bhd|bhd|plc|gmbh|co\.)\.?(?![A-Za-z])"
)
# A URL cut by a line wrap: span ends inside a URL on a "-" or "/" and the
# next span picks it up with no leading whitespace.
_URL_CONTINUES_RE = re.compile(r"[-/]$")
_LEADING_NON_SPACE_RE = re.compile(r"^\S+")


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


def _is_recognized_word(word: str, known_terms: set[str]) -> bool:
    normalized = word.lower()
    if normalized in DOMAIN_TERMS or normalized in known_terms:
        return True
    exact = _sym_spell.lookup(normalized, Verbosity.TOP, max_edit_distance=0)
    return bool(exact)


def _dehyphenate_spans(texts: list[str], known_terms: set[str] | None = None) -> list[str]:
    """
    Joins a word split across two spans back into one, two different ways:

    1. A span ending in "<word>-" immediately followed by a lowercase-
       starting next span is treated as a word split across a line break (a
       real hyphenated compound word ending a sentence is normally followed
       by a capital letter or punctuation, not a lowercase continuation) —
       the hyphen is dropped and the fragments joined.
    2. A narrow table column (a "Bank" cell, say) can force a mid-word wrap
       with no hyphen character at all — e.g. "Standar" / "d Charte" / "red"
       for "Standard Chartered". There's no hyphen to key off here, so this
       is only safe to join when the trailing fragment ISN'T already a
       recognised word on its own but the joined result IS one: two
       genuinely separate short words concatenated essentially never also
       happen to form a third recognised word, so this doesn't misfire on
       ordinary short-word-next-to-short-word text.

    Heuristic, not a full typesetting-aware solve; the reassembled word is
    attributed to the first span's (page, section).
    """
    known_terms = known_terms or set()
    texts = list(texts)
    for i in range(len(texts) - 1):
        stripped = texts[i].rstrip()
        ws_tail = texts[i][len(stripped):]

        trailing_match = _TRAILING_HYPHENATED_WORD_RE.search(stripped)
        if trailing_match:
            leading_match = _LEADING_WORD_RE.match(texts[i + 1])
            if not leading_match:
                continue
            fragment = trailing_match.group(0)[:-1]  # drop the trailing hyphen
            texts[i] = stripped[: trailing_match.start()] + fragment + leading_match.group(0) + ws_tail
            texts[i + 1] = texts[i + 1][leading_match.end():]
            continue

        bare_match = _TRAILING_WORD_RE.search(stripped)
        if not bare_match:
            continue
        leading_match = _LEADING_WORD_RE.match(texts[i + 1])
        if not leading_match:
            continue
        fragment = bare_match.group(0)
        if _is_recognized_word(fragment, known_terms):
            continue  # already a real word on its own — not a mid-word cut
        joined = fragment + leading_match.group(0)
        if not _is_recognized_word(joined, known_terms):
            continue  # joining doesn't produce a recognised word either
        texts[i] = stripped[: bare_match.start()] + joined + ws_tail
        texts[i + 1] = texts[i + 1][leading_match.end():]
    return texts


def _strip_non_prose(texts: list[str]) -> list[str]:
    """
    Blanks out the non-prose patterns above, span by span. Runs before
    _dehyphenate_spans: a URL wrapped across spans ("...basic-information-
    for-" / "account-holders...") would otherwise be rejoined into a fake
    word ("foraccount"). The continuation at the start of the next span is
    dropped along with the URL itself. Same number of spans out as in, so
    section indices still line up.
    """
    cleaned: list[str] = []
    url_continues = False
    for text in texts:
        if url_continues:
            text = _LEADING_NON_SPACE_RE.sub(" ", text, count=1)
        stripped = text.rstrip()
        last_url = None
        for last_url in _URL_RE.finditer(stripped):
            pass
        url_continues = (
            last_url is not None
            and last_url.end() == len(stripped)
            and bool(_URL_CONTINUES_RE.search(stripped))
        )
        for pattern in (_URL_RE, _MRZ_RE, _ROMAN_ENUMERATOR_RE, _PLURAL_SUFFIX_RE, _COMPANY_NAME_RE):
            text = pattern.sub(" ", text)
        cleaned.append(text)
    return cleaned


def _tokenize(text: str) -> list[str]:
    """
    Letters-only words of MIN_WORD_LENGTH or more, with a possessive "'s"
    dropped ("Bank's" -> "Bank") — SymSpell's dictionary has no possessive
    forms, so every "X's" would otherwise be a candidate.
    """
    words = (_POSSESSIVE_RE.sub("", word) for word in _WORD_RE.findall(text))
    return [word for word in words if len(word) >= MIN_WORD_LENGTH]


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
        texts = _dehyphenate_spans(
            _strip_non_prose([element.get("text", "") for element in elements]), known_terms
        )
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

    A short, fully-uppercase token is excluded outright, suggestion or not:
    formal documents are full of acronyms and legend/grade codes (HDB, SGX,
    or a credit bureau's HH/GG/FF/... risk-grade scale) that SymSpell's
    short-string matching will always find *some* unrelated "closest"
    dictionary word for (e.g. "HH" -> "he"), which is noise rather than a
    typo signal — nobody mistypes a 2-4 letter acronym into another real
    word.
    """
    if _ACRONYM_RE.match(candidate.word):
        return True
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
