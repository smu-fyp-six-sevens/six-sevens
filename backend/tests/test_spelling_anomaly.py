from __future__ import annotations

from unittest.mock import patch

import pytest

from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.spelling.spelling_anomaly import (
    _dehyphenate_spans,
    _filter_candidates,
    _find_candidates,
    _known_terms_from_extracted_fields,
    _looks_like_probable_proper_noun,
    _strip_non_prose,
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


def test_tokenize_drops_possessive_s() -> None:
    assert _tokenize("the Bank's and Borrower's assets") == ["the", "Bank", "and", "Borrower", "assets"]


def test_tokenize_ignores_letters_touching_digits() -> None:
    # Reference/account codes: the letters aren't pulled out as words.
    assert _tokenize("EBACT51059664352 0016RF7097939 verify/4n3NIEdM ok") == ["verify", "ok"]


def test_strip_non_prose_removes_urls_emails_and_mrz() -> None:
    result = _strip_non_prose([
        "See https://www.iras.gov.sg/taxes or mytax.iras.gov.sg now",
        "Email ops@bank.com.sg today",
        "PASGP<<MUHAMMAD<FAIZ<BIN<AHMAD<<<<<<",
    ])
    assert _tokenize(" ".join(result)) == ["See", "or", "now", "Email", "today"]


def test_strip_non_prose_drops_url_continued_on_the_next_span() -> None:
    # A wrapped URL would otherwise be rejoined by _dehyphenate_spans into a
    # fake word ("for-" + "account" -> "foraccount").
    result = _strip_non_prose([
        "https://www.iras.gov.sg/basic-information-for-",
        "account-holders-of-financial-institutions and more",
        "plain text-",
        "continues here",
    ])
    assert _tokenize(" ".join(_dehyphenate_spans(result))) == ["and", "more", "plain", "textcontinues", "here"]


def test_strip_non_prose_removes_roman_enumerators_and_plural_suffixes() -> None:
    result = _strip_non_prose(["(iv) the beneficiary(ies) or class(es); ix) any (vi) trust"])
    assert _tokenize(result[0]) == ["the", "beneficiary", "or", "class", "any", "trust"]


def test_strip_non_prose_removes_company_names_ending_in_a_legal_suffix() -> None:
    result = _strip_non_prose([
        "Employer: Ivory Pharma Distribution Pte. Ltd., Singapore",
        "EVERBRIGHT CONSTRUCTION PTE LTD",
        "Kallang Corporate Management Pte Ltd and Merlion Holdings LLP",
        "Helllo Everbright workers",  # no legal suffix -> still checked
    ])
    assert _tokenize(" ".join(result)) == ["Employer", "Singapore", "and", "Helllo", "Everbright", "workers"]


def test_tokenize_skips_urls_domains_and_emails() -> None:
    text = (
        "Website: www.iras.gov.sg myTax Portal: mytax.iras.gov.sg "
        "see https://www.dbs.com.sg/personal/support/bank-general.html or email enquiry@cbs.com.sg"
    )
    assert _tokenize(text) == ["Website", "myTax", "Portal", "see", "or", "email"]
    # A URL wrapped onto its own line continues as a bare path.
    assert _tokenize("taxes/corporate-income-tax/basics") == []


def test_tokenize_still_keeps_real_typos_next_to_web_like_text() -> None:
    words = _tokenize("Payment recieved on the acount. Ended here.Then cotninued, and/or a statment")
    for typo in ("recieved", "acount", "cotninued", "statment"):
        assert typo in words
    assert "and" in words and "or" in words


def test_dehyphenate_spans_joins_line_wrapped_word() -> None:
    result = _dehyphenate_spans(["this is a compre-", "hension test"])
    assert result == ["this is a comprehension", " test"]


def test_dehyphenate_spans_leaves_real_trailing_hyphen_before_capital() -> None:
    # A genuine end-of-sentence hyphen followed by a new capitalised word
    # shouldn't be joined — only a lowercase continuation looks like a
    # line-wrapped word fragment.
    result = _dehyphenate_spans(["Well-", "Known company"])
    assert result == ["Well-", "Known company"]


def test_dehyphenate_spans_joins_a_narrow_table_column_wrap_with_no_hyphen() -> None:
    # A table cell too narrow for "Standard Chartered" wraps it across three
    # spans with no hyphen character at all ("Standar" / "d Charte" / "red").
    result = _dehyphenate_spans(["Standar", "d Charte", "red"])
    assert result == ["Standard", " Chartered", ""]


def test_dehyphenate_spans_does_not_join_two_genuinely_separate_short_words() -> None:
    # "as" and "sign" are both real words on their own — joining them would
    # only misfire if the *fragment alone* already failed as a real word,
    # which isn't the case here, so this must be left untouched.
    result = _dehyphenate_spans(["as", "sign a form"])
    assert result == ["as", "sign a form"]


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


def test_domain_terms_are_all_lowercase() -> None:
    # Regression guard: spelling_anomaly.py only lowercases the word being
    # checked, not DOMAIN_TERMS itself — a mixed-case entry added straight
    # to the raw set (e.g. "DBS") would silently never match anything and
    # just sit there doing nothing. DOMAIN_TERMS must stay fully lowercase
    # for the domain-terms.py module's own "checked case-insensitively"
    # claim to actually hold.
    from app.fraud_detection.spelling.domain_terms import DOMAIN_TERMS

    assert all(term == term.lower() for term in DOMAIN_TERMS)


def test_find_candidates_skips_mixed_case_domain_term() -> None:
    # "DBS" is stored mixed-case in domain_terms.py's raw seed list (for
    # readability) — confirms it still matches regardless, via both casings
    # as they'd actually appear in a real document.
    pages = [{"page_number": 1, "elements": [{"text": "DBS Bank Ltd"}, {"text": "www.dbs.com"}]}]
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
        # (see font_consistency._field_search_text).
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
    pages = [{"page_number": 1, "elements": [{"text": "zzqxw appears here"}]}]
    candidates = [c for c in _find_candidates(pages, known_terms=set()) if not c.word[0].isupper()]
    assert candidates
    assert all(_looks_like_probable_proper_noun(c) is False for c in candidates)


def test_looks_like_probable_proper_noun_true_for_short_acronym_with_suggestion() -> None:
    # "HH" is a credit-bureau risk-grade legend code, not prose — even
    # though SymSpell finds an unrelated close match ("he"), a short
    # all-caps token is excluded outright.
    pages = [{"page_number": 1, "elements": [{"text": "Grade HH applies"}]}]
    hh = next(c for c in _find_candidates(pages, known_terms=set()) if c.word == "HH")
    assert _looks_like_probable_proper_noun(hh) is True


def test_looks_like_probable_proper_noun_true_for_plural_acronym() -> None:
    pages = [{"page_number": 1, "elements": [{"text": "list all UENs"}]}]
    uens = next(c for c in _find_candidates(pages, known_terms=set()) if c.word == "UENs")
    assert _looks_like_probable_proper_noun(uens) is True


def test_find_candidates_skips_mixed_case_bank_shorthand() -> None:
    pages = [{"page_number": 1, "elements": [
        {"text": "Txn Charges Billing"}, {"text": "Funds Trf - FAST"}, {"text": "Inward Cr - FAST"},
    ]}]
    assert _find_candidates(pages, known_terms=set()) == []


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
async def test_a_company_name_printed_on_the_document_is_not_reported_as_a_misspelling() -> None:
    # The name is in the document's own extracted fields, so it is a known term; a misspelt company name
    # is a case-level finding (comparing the case's documents), not a spelling one.
    fields = [ExtractedField(field_name="company_name", field_type="string",
                             field_value="HARBOR LIGHT SHIPING PTE LTD", confidence=0.9)]
    with patch("app.fraud_detection.spelling.spelling_anomaly.ingest_document") as mock_ingest:
        _stub_ingestion(mock_ingest, "HARBOR LIGHT SHIPING PTE LTD")

        result = await check_spelling_anomalies("doc-1", b"bytes", "cert.pdf", fields)

    assert result.unable_to_assess is False
    assert result.anomalies == []


@pytest.mark.asyncio
async def test_check_spelling_anomalies_unable_to_assess_on_extraction_failure() -> None:
    with patch("app.fraud_detection.spelling.spelling_anomaly.ingest_document") as mock_ingest:
        mock_ingest.side_effect = RuntimeError("corrupt file")

        result = await check_spelling_anomalies("doc-1", b"bytes", "statement.pdf")

    assert result.unable_to_assess is True
    assert "corrupt file" in result.unable_to_assess_reason
    assert result.anomalies == []


@pytest.mark.asyncio
async def test_check_spelling_anomalies_unable_to_assess_on_empty_text() -> None:
    with patch("app.fraud_detection.spelling.spelling_anomaly.ingest_document") as mock_ingest:
        mock_ingest.return_value = {"raw_text": "   ", "pages": []}

        result = await check_spelling_anomalies("doc-1", b"bytes", "statement.pdf")

    assert result.unable_to_assess is True
    assert result.anomalies == []


@pytest.mark.asyncio
async def test_check_spelling_anomalies_no_candidates_returns_empty_not_unable() -> None:
    with patch("app.fraud_detection.spelling.spelling_anomaly.ingest_document") as mock_ingest:
        _stub_ingestion(mock_ingest, raw_text="Please review the escrow schedule.")

        result = await check_spelling_anomalies("doc-1", b"bytes", "statement.pdf")

    assert result.unable_to_assess is False
    assert result.anomalies == []


@pytest.mark.asyncio
async def test_check_spelling_anomalies_flags_typo_and_suppresses_proper_noun() -> None:
    with patch("app.fraud_detection.spelling.spelling_anomaly.ingest_document") as mock_ingest:
        _stub_ingestion(mock_ingest, raw_text="Helllo Mr Kowalski")

        result = await check_spelling_anomalies("doc-1", b"bytes", "statement.pdf")

    words = {a.word for a in result.anomalies}
    assert words == {"Helllo"}


@pytest.mark.asyncio
async def test_check_spelling_anomalies_skips_word_matching_extracted_field() -> None:
    with patch("app.fraud_detection.spelling.spelling_anomaly.ingest_document") as mock_ingest:
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


@pytest.mark.asyncio
async def test_check_spelling_anomalies_reports_a_repeated_word_once_with_its_count() -> None:
    # The same unrecognised word in four places (a letterhead line repeated on each page, and in
    # lower case once) is one anomaly with occurrences=4, not four.
    pages = [
        {"page_number": n, "elements": [{"text": "Shentn Holdings letterhead"}, {"text": "the shentn branch"}]}
        for n in (1, 2)
    ]
    with patch("app.fraud_detection.spelling.spelling_anomaly.ingest_document") as mock_ingest:
        mock_ingest.return_value = {"raw_text": "x", "pages": pages}

        result = await check_spelling_anomalies("doc-1", b"bytes", "statement.pdf")

    assert [(a.word, a.occurrences) for a in result.anomalies] == [("Shentn", 4)]
    anomaly = result.anomalies[0]
    assert anomaly.surrounding_text == "Shentn Holdings letterhead"
    # The other distinct places it appears, not the same line again.
    assert anomaly.other_contexts == ["the shentn branch"]



def test_find_candidates_flags_street_names_in_an_address_line_for_the_agent_to_judge() -> None:
    # An address street name missing from the dictionary is flagged like any other word; the specialist
    # decides whether it's a real place. (Skipping address lines would also hide a misspelt one.)
    pages = [{"page_number": 1, "elements": [{"text": "2 Shenton Way #20-02 SGX Centre 1 Singapore 068804"}]}]
    assert [c.word for c in _filter_candidates(_find_candidates(pages, set()))] == ["Shenton"]
