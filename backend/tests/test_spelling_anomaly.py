from __future__ import annotations

from unittest.mock import patch

import pytest

from app.fraud_detection.font_consistency_v2 import ExtractedField
from app.fraud_detection.spelling_anomaly import (
    _dehyphenate_spans,
    _filter_candidates,
    _find_candidates,
    _known_terms_from_extracted_fields,
    _looks_like_probable_proper_noun,
    _tokenize,
    check_spelling_anomalies,
)


def test_tokenize_normalizes_punctuation_and_drops_short_tokens() -> None:
    words = _tokenize("Helllo, Mr. O'Brien! A T0401234A.")
    # Punctuation is stripped by the letters-only tokenizer; the digit-
    # containing NRIC-style token splits into single letters ("T", "A")
    # around the digits, which MIN_WORD_LENGTH then drops, same as the
    # standalone "A".
    assert words == ["Helllo", "Mr", "O'Brien"]


def test_dehyphenate_spans_joins_line_wrapped_word() -> None:
    result = _dehyphenate_spans(["this is a compre-", "hension test"])
    assert result == ["this is a comprehension", " test"]


def test_dehyphenate_spans_leaves_real_trailing_hyphen_before_capital() -> None:
    # A genuine end-of-sentence hyphen followed by a new capitalised word
    # shouldn't be joined — only a lowercase continuation looks like a
    # line-wrapped word fragment.
    result = _dehyphenate_spans(["Well-", "Known company"])
    assert result == ["Well-", "Known company"]


def test_find_candidates_skips_domain_terms_and_correct_words() -> None:
    pages = [
        {
            "page_number": 1,
            "elements": [
                {"text": "Please review the escrow amortization schedule."},
                {"text": "NRIC Mr Dr Pte Ltd"},
            ],
        }
    ]
    candidates = _find_candidates(pages, known_terms=set())
    assert candidates == []


def test_find_candidates_skips_known_terms() -> None:
    pages = [{"page_number": 1, "elements": [{"text": "Signed by Kowalski"}]}]
    # Without a known_terms hit, "Kowalski" is a candidate (see the
    # unrecognised-word test below) — providing it via known_terms (as if
    # it came from an already-extracted individual_name field) skips it.
    candidates = _find_candidates(pages, known_terms={"kowalski"})
    assert candidates == []


def test_find_candidates_flags_misspelling_with_suggestion() -> None:
    pages = [{"page_number": 1, "elements": [{"text": "Helllo there"}]}]
    candidates = _find_candidates(pages, known_terms=set())
    assert len(candidates) == 1
    assert candidates[0].word == "Helllo"
    assert candidates[0].suggested_correction == "hello"
    assert candidates[0].page == 1
    assert candidates[0].section == 0
    assert candidates[0].surrounding_text == "Helllo there"


def test_find_candidates_deduplicates_the_same_misspelling_repeated_in_one_span() -> None:
    pages = [{"page_number": 1, "elements": [{"text": "Helllo Helllo there"}]}]
    candidates = _find_candidates(pages, known_terms=set())
    # "Helllo" appears twice in the same (page, section) span — only the
    # first occurrence should surface, not one candidate per repetition.
    assert len([c for c in candidates if c.word == "Helllo"]) == 1


def test_find_candidates_flags_unrecognised_word_with_no_suggestion() -> None:
    pages = [{"page_number": 1, "elements": [{"text": "Mr Kowalski signed"}]}]
    candidates = _find_candidates(pages, known_terms=set())
    words = {c.word for c in candidates}
    assert "Kowalski" in words
    kowalski = next(c for c in candidates if c.word == "Kowalski")
    assert kowalski.suggested_correction is None


def test_known_terms_from_extracted_fields_tokenizes_string_values() -> None:
    fields = [
        ExtractedField(
            field_name="individual_name", field_type="string",
            field_value="Kowalski Tan", confidence=0.9,
        ),
        # Double-JSON-encoded string, as real Supabase rows sometimes are
        # (see font_consistency_v2._field_search_text).
        ExtractedField(
            field_name="company_name", field_type="string",
            field_value='"Acme Pte Ltd"', confidence=0.9,
        ),
        # Non-string values (e.g. a number, or a structured field like
        # transactions) have no single text to tokenize — skipped.
        ExtractedField(
            field_name="net_pay", field_type="number",
            field_value=5000, confidence=0.9,
        ),
    ]
    known = _known_terms_from_extracted_fields(fields)
    assert known == {"kowalski", "tan", "acme", "pte", "ltd"}


def test_known_terms_from_extracted_fields_handles_none() -> None:
    assert _known_terms_from_extracted_fields(None) == set()


def test_looks_like_probable_proper_noun_capitalised_with_no_suggestion() -> None:
    pages = [{"page_number": 1, "elements": [{"text": "Mr Kowalski signed"}]}]
    kowalski = next(c for c in _find_candidates(pages, known_terms=set()) if c.word == "Kowalski")
    assert _looks_like_probable_proper_noun(kowalski) is True


def test_looks_like_probable_proper_noun_false_when_suggestion_exists() -> None:
    pages = [{"page_number": 1, "elements": [{"text": "Helllo there"}]}]
    helllo = _find_candidates(pages, known_terms=set())[0]
    # Capitalised, but has a close SymSpell suggestion — a real typo, not
    # suppressed by the proper-noun heuristic.
    assert _looks_like_probable_proper_noun(helllo) is False


def test_looks_like_probable_proper_noun_false_when_lowercase() -> None:
    # A lowercase, totally-unrecognised token (no suggestion) doesn't match
    # the "proper noun" pattern — kept as an anomaly rather than suppressed.
    pages = [{"page_number": 1, "elements": [{"text": "zzqxw123abc appears here"}]}]
    candidates = [c for c in _find_candidates(pages, known_terms=set()) if not c.word[0].isupper()]
    assert candidates
    assert all(_looks_like_probable_proper_noun(c) is False for c in candidates)


def test_filter_candidates_drops_only_probable_proper_nouns() -> None:
    pages = [{"page_number": 1, "elements": [{"text": "Helllo Mr Kowalski"}]}]
    candidates = _find_candidates(pages, known_terms=set())
    filtered = _filter_candidates(candidates)
    words = {c.word for c in filtered}
    assert "Helllo" in words
    assert "Kowalski" not in words


def _stub_ingestion(mock_ingest, raw_text: str = "Helllo there") -> None:
    mock_ingest.return_value = {
        "raw_text": raw_text,
        "pages": [{"page_number": 1, "elements": [{"text": raw_text}]}],
    }


@pytest.mark.asyncio
async def test_check_spelling_anomalies_unable_to_assess_on_extraction_failure() -> None:
    with patch("app.fraud_detection.spelling_anomaly.ingest_document") as mock_ingest:
        mock_ingest.side_effect = RuntimeError("corrupt file")

        result = await check_spelling_anomalies("doc-1", b"bytes", "statement.pdf")

    assert result.unable_to_assess is True
    assert "corrupt file" in result.unable_to_assess_reason
    assert result.anomalies == []


@pytest.mark.asyncio
async def test_check_spelling_anomalies_unable_to_assess_on_empty_text() -> None:
    with patch("app.fraud_detection.spelling_anomaly.ingest_document") as mock_ingest:
        mock_ingest.return_value = {"raw_text": "   ", "pages": []}

        result = await check_spelling_anomalies("doc-1", b"bytes", "statement.pdf")

    assert result.unable_to_assess is True
    assert result.anomalies == []


@pytest.mark.asyncio
async def test_check_spelling_anomalies_no_candidates_returns_empty_not_unable() -> None:
    with patch("app.fraud_detection.spelling_anomaly.ingest_document") as mock_ingest:
        _stub_ingestion(mock_ingest, raw_text="Please review the escrow schedule.")

        result = await check_spelling_anomalies("doc-1", b"bytes", "statement.pdf")

    assert result.unable_to_assess is False
    assert result.anomalies == []


@pytest.mark.asyncio
async def test_check_spelling_anomalies_flags_typo_and_suppresses_proper_noun() -> None:
    with patch("app.fraud_detection.spelling_anomaly.ingest_document") as mock_ingest:
        _stub_ingestion(mock_ingest, raw_text="Helllo Mr Kowalski")

        result = await check_spelling_anomalies("doc-1", b"bytes", "statement.pdf")

    words = {a.word for a in result.anomalies}
    assert words == {"Helllo"}


@pytest.mark.asyncio
async def test_check_spelling_anomalies_skips_word_matching_extracted_field() -> None:
    with patch("app.fraud_detection.spelling_anomaly.ingest_document") as mock_ingest:
        # "Kowalski" would normally be a (suppressed) proper-noun candidate
        # anyway — use a lowercase, no-suggestion word instead so the only
        # thing keeping it out of the result is the known-terms match, not
        # the proper-noun heuristic.
        _stub_ingestion(mock_ingest, raw_text="the zzqxwabc entity signed")
        fields = [
            ExtractedField(
                field_name="company_name", field_type="string",
                field_value="zzqxwabc", confidence=0.9,
            )
        ]

        result = await check_spelling_anomalies("doc-1", b"bytes", "statement.pdf", fields)

    assert result.anomalies == []
