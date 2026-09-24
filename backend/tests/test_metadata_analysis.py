from __future__ import annotations

from app.fraud_detection.metadata_analysis import (
    check_metadata_anomalies,
    extract_supported_metadata,
)


def test_extract_supported_metadata_pulls_the_six_supported_fields() -> None:
    raw = {
        "Author": "Jane Tan",
        "Creator": "Microsoft Word",
        "Producer": "Microsoft: Print To PDF",
        "CreateDate": "2024:01:15 10:23:00",
        "ModifyDate": "2024:01:15 10:25:00",
        "Software": "Adobe Acrobat 23.0",
        "ImageWidth": 1024,  # not one of the six — should be ignored
    }
    extracted = extract_supported_metadata(raw)
    assert extracted == {
        "author": "Jane Tan",
        "creator": "Microsoft Word",
        "producer": "Microsoft: Print To PDF",
        "creation_date": "2024:01:15 10:23:00",
        "modification_date": "2024:01:15 10:25:00",
        "software": "Adobe Acrobat 23.0",
    }


def test_extract_supported_metadata_falls_back_across_aliases() -> None:
    # "Artist" (EXIF's author-equivalent tag) rather than "Author" (PDF's).
    raw = {"Artist": "Jane Tan", "ModDate": "2024:01:15 10:25:00"}
    extracted = extract_supported_metadata(raw)
    assert extracted["author"] == "Jane Tan"
    assert extracted["modification_date"] == "2024:01:15 10:25:00"


def test_extract_supported_metadata_treats_blank_string_as_absent() -> None:
    raw = {"Author": "   "}
    assert extract_supported_metadata(raw)["author"] is None


# AC7 -----------------------------------------------------------------


def test_unable_to_assess_when_metadata_is_empty() -> None:
    result = check_metadata_anomalies("doc-1", {})
    assert result.unable_to_assess is True
    assert "No metadata was captured" in result.unable_to_assess_reason
    assert result.anomalies == []


def test_unable_to_assess_when_metadata_is_none() -> None:
    result = check_metadata_anomalies("doc-1", None)
    assert result.unable_to_assess is True


def test_unable_to_assess_when_only_unsupported_fields_present() -> None:
    # Real exiftool output, but none of it is one of the six supported
    # fields (AC2) — nothing for this check to actually assess.
    result = check_metadata_anomalies("doc-1", {"ImageWidth": 1024, "ColorSpace": "RGB"})
    assert result.unable_to_assess is True
    assert "None of the supported metadata fields" in result.unable_to_assess_reason


def test_assessed_when_at_least_one_supported_field_present() -> None:
    result = check_metadata_anomalies("doc-1", {"Author": "Jane Tan"})
    assert result.unable_to_assess is False
    assert result.unable_to_assess_reason is None


# AC3 -----------------------------------------------------------------


def test_flags_generic_placeholder_author() -> None:
    result = check_metadata_anomalies("doc-1", {"Author": "admin"})
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "author"
    assert result.anomalies[0].value == "admin"
    assert "placeholder" in result.anomalies[0].reason
    # Weak corroborating evidence only — plenty of genuine PDFs are
    # produced by a system account.
    assert result.anomalies[0].severity == "low"


def test_does_not_flag_a_real_looking_author() -> None:
    result = check_metadata_anomalies("doc-1", {"Author": "Jane Tan"})
    assert result.anomalies == []


def test_generic_pattern_check_is_case_and_whitespace_insensitive() -> None:
    result = check_metadata_anomalies("doc-1", {"Creator": "  Admin  "})
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "creator"


# AC4 -----------------------------------------------------------------


def test_flags_content_editor_on_any_document_type() -> None:
    # Photoshop repaints content — no form-filling workflow does that, so
    # it's flagged even on a document the applicant fills in themselves.
    result = check_metadata_anomalies(
        "doc-1", {"Software": "Adobe Photoshop 24.0"}, "fatca_crs_declaration"
    )
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "software"
    assert result.anomalies[0].severity == "high"
    assert "Photoshop" in result.anomalies[0].reason
    assert result.ai_generation_indicators == []


def test_flags_content_editor_when_no_document_type_known() -> None:
    result = check_metadata_anomalies("doc-1", {"Software": "Adobe Photoshop 24.0"})
    assert len(result.anomalies) == 1
    assert result.anomalies[0].severity == "high"


def test_flags_pdf_editor_on_institution_originated_document() -> None:
    # PDFs rarely populate exiftool's "Software" tag at all — the editing
    # tool realistically shows up in Producer/Creator instead (see the
    # module's _SOFTWARE_BEARING_FIELDS comment).
    result = check_metadata_anomalies(
        "doc-1", {"Producer": "Foxit PhantomPDF 12"}, "bank_statement"
    )
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "producer"
    assert result.anomalies[0].severity == "medium"
    assert "bank statement" in result.anomalies[0].reason


def test_does_not_flag_pdf_editor_on_a_form_the_applicant_fills_in() -> None:
    # The whole point of the document-type scoping: an applicant
    # completing a FATCA/CRS declaration in Acrobat is the expected
    # workflow, not evidence of tampering.
    result = check_metadata_anomalies(
        "doc-1", {"Producer": "Adobe Acrobat Pro DC 23"}, "fatca_crs_declaration"
    )
    assert result.anomalies == []


def test_does_not_flag_pdf_editor_when_document_type_is_unknown() -> None:
    result = check_metadata_anomalies("doc-1", {"Producer": "Adobe Acrobat Pro DC 23"})
    assert result.anomalies == []


def test_flags_plain_adobe_acrobat_not_just_the_pro_edition() -> None:
    # Regression: the marker list only held "acrobat pro", so a real
    # install writing plain "Adobe Acrobat" — the most common of the three
    # spellings — sailed through untouched.
    result = check_metadata_anomalies("doc-1", {"Creator": "Adobe Acrobat"}, "bank_statement")
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "creator"
    assert result.anomalies[0].severity == "medium"


def test_does_not_flag_acrobat_distiller() -> None:
    # Distiller is a PostScript-to-PDF generator, not an editor — plenty of
    # institutions produce their genuine statements with it, so widening
    # the Acrobat marker must not have swept it up.
    result = check_metadata_anomalies(
        "doc-1", {"Producer": "Acrobat Distiller 11.0 (Windows)"}, "bank_statement"
    )
    assert result.anomalies == []


def test_flags_test_artefact_author_not_only_exact_placeholders() -> None:
    # Regression: the placeholder pattern was anchored to the whole value,
    # so "FraudTests" (contains "test", isn't equal to it) wasn't caught.
    result = check_metadata_anomalies("doc-1", {"Author": "FraudTests"})
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "author"
    assert result.anomalies[0].severity == "low"
    assert "artefact" in result.anomalies[0].reason


def test_flags_template_artefact_in_producer() -> None:
    result = check_metadata_anomalies("doc-1", {"Producer": "invoice_template_final"})
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "producer"


def test_unknown_producer_is_not_flagged_by_the_deny_list() -> None:
    # Documents the known gap, so it fails loudly if someone later adds a
    # producer allow-list and forgets this test: a deny-list can only catch
    # tools it already knows, so an invented one like "DocuSuite Enterprise"
    # passes cleanly no matter how fabricated the document is.
    result = check_metadata_anomalies(
        "doc-1", {"Producer": "DocuSuite Enterprise 4.2"}, "bank_statement"
    )
    assert result.anomalies == []


def test_does_not_flag_ordinary_producer_on_institution_document() -> None:
    result = check_metadata_anomalies(
        "doc-1", {"Producer": "Microsoft: Print To PDF"}, "bank_statement"
    )
    assert result.anomalies == []


# AC8 -----------------------------------------------------------------


def test_flags_ai_generation_tool_as_anomaly_and_indicator() -> None:
    result = check_metadata_anomalies("doc-1", {"Creator": "ChatGPT"})
    assert len(result.anomalies) == 1
    assert "ChatGPT" in result.anomalies[0].reason
    assert result.anomalies[0].severity == "high"
    assert result.ai_generation_indicators == result.anomalies


def test_flags_ai_generation_tool_regardless_of_document_type() -> None:
    # Like the content editors, AI-generation markers aren't excused by a
    # document being one the applicant fills in themselves.
    result = check_metadata_anomalies("doc-1", {"Creator": "ChatGPT"}, "fatca_crs_declaration")
    assert len(result.ai_generation_indicators) == 1


def test_ai_marker_takes_priority_over_generic_software_rule_on_same_field() -> None:
    # A field shouldn't be flagged twice for one value — the more specific
    # AI-generation finding wins over the generic editing-tool rule.
    result = check_metadata_anomalies("doc-1", {"Software": "Adobe Firefly (Photoshop plugin)"})
    assert len(result.anomalies) == 1
    assert result.anomalies[0] in result.ai_generation_indicators


# AC5 -----------------------------------------------------------------


def test_flags_modification_before_creation() -> None:
    result = check_metadata_anomalies(
        "doc-1",
        {"CreateDate": "2024:01:15 10:25:00", "ModifyDate": "2024:01:15 10:23:00"},
    )
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "modification_date"
    assert "before" in result.anomalies[0].reason
    assert result.anomalies[0].severity == "high"


def test_does_not_flag_modification_after_creation() -> None:
    result = check_metadata_anomalies(
        "doc-1",
        {"CreateDate": "2024:01:15 10:23:00", "ModifyDate": "2024:01:15 10:25:00"},
    )
    assert result.anomalies == []


def test_does_not_flag_date_relationship_when_only_one_date_present() -> None:
    result = check_metadata_anomalies("doc-1", {"CreateDate": "2024:01:15 10:23:00"})
    assert result.anomalies == []


def test_unparseable_dates_do_not_crash_or_flag() -> None:
    result = check_metadata_anomalies(
        "doc-1",
        {"CreateDate": "not a date", "ModifyDate": "also not a date"},
    )
    assert result.unable_to_assess is False
    assert result.anomalies == []


def test_semantically_invalid_date_does_not_crash_or_flag() -> None:
    # Matches the exiftool date shape syntactically (regex-wise) but names
    # an impossible calendar date — datetime() itself rejects it.
    result = check_metadata_anomalies(
        "doc-1",
        {"CreateDate": "2024:13:99 10:23:00", "ModifyDate": "2024:01:15 10:25:00"},
    )
    assert result.unable_to_assess is False
    assert result.anomalies == []


# Combined --------------------------------------------------------------


def test_multiple_anomalies_all_reported_together() -> None:
    result = check_metadata_anomalies(
        "doc-1",
        {
            "Author": "admin",
            "Producer": "Adobe Photoshop 24.0",
            "CreateDate": "2024:01:15 10:25:00",
            "ModifyDate": "2024:01:15 10:23:00",
        },
    )
    fields = {a.field for a in result.anomalies}
    assert fields == {"author", "producer", "modification_date"}
    assert result.document_id == "doc-1"
