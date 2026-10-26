from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.fraud_detection.authenticity.metadata_analysis import (
    SUPPORTED_METADATA_FIELDS,
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
    # The AC2 six, resolved out of exiftool's much larger tag dump. The
    # XMP provenance keys are also present (absent here) — asserted
    # separately so this stays a test of the six.
    assert {field: extracted[field] for field in SUPPORTED_METADATA_FIELDS} == {
        "author": "Jane Tan",
        "creator": "Microsoft Word",
        "producer": "Microsoft: Print To PDF",
        "creation_date": "2024:01:15 10:23:00",
        "modification_date": "2024:01:15 10:25:00",
        "software": "Adobe Acrobat 23.0",
    }
    assert extracted["xmp_instance_id"] is None


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
    # exiftool always reports basic file details, so empty means it didn't run.
    assert "exiftool wasn't available" in result.unable_to_assess_reason
    assert "didn't run" in result.unable_to_assess_reason
    assert result.anomalies == []


def test_unable_to_assess_reports_the_recorded_extraction_failure() -> None:
    result = check_metadata_anomalies(
        "doc-1", {"MetadataExtractionError": "exiftool is not installed on the server that processed this document"}
    )
    assert result.unable_to_assess is True
    assert "couldn't be read when it was uploaded: exiftool is not installed" in result.unable_to_assess_reason


def test_unable_to_assess_when_metadata_is_none() -> None:
    result = check_metadata_anomalies("doc-1", None)
    assert result.unable_to_assess is True


def test_unable_to_assess_when_only_unsupported_fields_present() -> None:
    # Real exiftool output, but none of it is one of the six supported
    # fields (AC2) — nothing for this check to actually assess.
    result = check_metadata_anomalies("doc-1", {"ImageWidth": 1024, "ColorSpace": "RGB"})
    assert result.unable_to_assess is True
    assert "None of the supported metadata fields" in result.unable_to_assess_reason


def test_unable_to_assess_reason_explains_bare_image_metadata() -> None:
    result = check_metadata_anomalies(
        "doc-1", {"FileType": "PNG", "ImageWidth": 1412, "FileModifyDate": "2026:10:02 04:59:12+00:00"}
    )
    assert result.unable_to_assess is True
    assert "This PNG image carries no authorship or editing metadata" in result.unable_to_assess_reason
    assert "not a sign of tampering" in result.unable_to_assess_reason


def test_assessed_when_at_least_one_supported_field_present() -> None:
    result = check_metadata_anomalies("doc-1", {"Author": "Jane Tan"})
    assert result.unable_to_assess is False
    assert result.unable_to_assess_reason is None


# AC3 -----------------------------------------------------------------


# Metadata that must produce no anomaly, and must still count as assessed. Each id says what it guards.
_ACROBAT = {"Producer": "Adobe Acrobat Pro DC 23"}
_NOT_FLAGGED = [
    pytest.param({"Author": "Jane Tan"}, None, id="a real-looking author"),
    pytest.param(_ACROBAT, "fatca_crs_declaration", id="a pdf editor on a form the applicant fills in"),
    pytest.param(_ACROBAT, None, id="a pdf editor when the document type is unknown"),
    # nric and passport are photographed or scanned by the applicant, so a scanning app's Acrobat-branded
    # metadata is the normal producer, not tampering (they were once wrongly institution-originated).
    pytest.param(_ACROBAT, "nric", id="a pdf editor on a scanned nric"),
    pytest.param(_ACROBAT, "passport", id="a pdf editor on a scanned passport"),
    # Distiller generates PDFs from PostScript; widening the Acrobat marker must not sweep it up.
    pytest.param({"Producer": "Acrobat Distiller 11.0 (Windows)"}, "bank_statement", id="acrobat distiller"),
    # Documents a known gap: a deny-list can't catch a tool it doesn't know, however fabricated the document.
    pytest.param({"Producer": "DocuSuite Enterprise 4.2"}, "bank_statement", id="an unknown producer"),
    pytest.param({"Producer": "Microsoft: Print To PDF"}, "bank_statement", id="an ordinary producer on a statement"),
    # exiftool's "creator" is not always a tool name; a person's name there must stay clean.
    pytest.param({"Creator": "TAN YI XI JOVI _"}, "bank_statement", id="a person's name as creator"),
    pytest.param(
        {"CreateDate": "2024:01:15 10:23:00", "ModifyDate": "2024:01:15 10:25:00"}, None, id="modified after created"
    ),
    pytest.param({"CreateDate": "2024:01:15 10:23:00"}, None, id="only one date present"),
    pytest.param({"CreateDate": "not a date", "ModifyDate": "also not a date"}, None, id="unparseable dates"),
    # Matches the exiftool shape but names an impossible calendar date, which datetime() rejects.
    pytest.param(
        {"CreateDate": "2024:13:99 10:23:00", "ModifyDate": "2024:01:15 10:25:00"}, None, id="an impossible date"
    ),
    # The real GLDB FATCA form was authored in Word, so its dates are an hour apart.
    pytest.param(
        {"CreateDate": "2025:11:05 08:47:33", "ModifyDate": "2025:11:05 09:45:07"},
        "fatca_crs_declaration", id="a re-save on a form the applicant edits",
    ),
    # One conversion pass gave a one-second gap on a real file; ReportLab writes the two dates identically.
    pytest.param(
        {"CreateDate": "2026:09:19 14:43:14", "ModifyDate": "2026:09:19 14:43:15"},
        "bank_statement", id="a same-pass generation gap",
    ),
    # Clock skew and ignored timezone offsets shouldn't read as fabrication.
    pytest.param(
        {"CreateDate": (datetime.now() + timedelta(hours=6)).strftime("%Y:%m:%d %H:%M:%S")}, None,
        id="a date a few hours ahead",
    ),
    pytest.param(
        {"Author": "X", "DocumentID": "uuid:AAA", "InstanceID": "uuid:AAA"}, "bank_statement",
        id="matching instance and document ids",
    ),
    # The real GLDB FATCA form has differing ids legitimately.
    pytest.param(
        {"Author": "X", "DocumentID": "uuid:AAA", "InstanceID": "uuid:BBB"}, "fatca_crs_declaration",
        id="differing ids on a form the applicant edits",
    ),
    # Genuine samples supplied by users:
    pytest.param(
        {"Creator": "PDFium", "Producer": "PDFium", "CreateDate": "2023:03:09 17:09:29"}, "nric",
        id="a real scanned nric",
    ),
    pytest.param(
        {"Producer": "OpenPDF 2.0.2", "CreateDate": "2025:10:01 17:55:29Z"}, "bank_statement",
        id="a real standard chartered statement (no creator, no modify date)",
    ),
    pytest.param(
        {
            "Producer": "iOS Version 17.1.2 (Build 21B101) Quartz PDFContext", "Creator": "Notes",
            "CreateDate": "2025:10:12 08:19:18Z", "ModifyDate": "2025:10:12 08:19:18Z",
        },
        "passport", id="a real passport scanned with the iOS Notes app",
    ),
    # Two genuine OCBC statements with single-digit-second gaps, well under the 60 s tolerance.
    pytest.param(
        {
            "Producer": "Streamline PDFGen for OCBC Group", "Creator": "pdfgen",
            "CreateDate": "2026:02:03 11:43:08+08:00", "ModifyDate": "2026:02:03 11:43:11+08:00",
        },
        "bank_statement", id="a real ocbc statement (3 s gap)",
    ),
    pytest.param(
        {
            "Producer": "Streamline PDFGen for OCBC Group", "Creator": "pdfgen",
            "CreateDate": "2026:09:02 22:36:34+08:00", "ModifyDate": "2026:09:02 22:36:38+08:00",
        },
        "bank_statement", id="a real ocbc statement (4 s gap)",
    ),
]


@pytest.mark.parametrize(("metadata", "document_type"), _NOT_FLAGGED)
def test_ordinary_metadata_is_not_flagged(metadata, document_type) -> None:
    result = check_metadata_anomalies("doc-1", metadata, document_type)

    assert result.anomalies == []
    assert result.unable_to_assess is False


def test_flags_generic_placeholder_author() -> None:
    result = check_metadata_anomalies("doc-1", {"Author": "admin"})
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "author"
    assert result.anomalies[0].value == "admin"
    assert "placeholder" in result.anomalies[0].reason
    # Weak corroborating evidence only — plenty of genuine PDFs are
    # produced by a system account.
    assert result.anomalies[0].severity == "low"


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


# Parse warnings (a genuinely re-saved/tampered file) ------------------


def test_flags_canva_edited_document() -> None:
    # User-supplied: a real document edited in Canva. Shape confirmed by
    # deliberately constructing a PDF with both a classic /Creator="Canva"
    # and an XMP dc:creator=<person name>, then running the real exiftool
    # -json (flat, no -G) our extractor uses — the person's name wins the
    # "Creator" name collision, so "Canva" only survives via CreatorTool
    # (our "software" field). This is that exact real shape.
    result = check_metadata_anomalies(
        "doc-1",
        {
            "Creator": "TAN YI XI JOVI _",
            "CreatorTool": "Canva",
            "Producer": "Canva",
            "Author": "TAN YI XI JOVI _",
            "CreateDate": "2026:09:26 06:25:53.314Z",
            "ModifyDate": "2026:09:26 06:25:53.314Z",
        },
        "bank_statement",
    )
    fields_flagged = {a.field for a in result.anomalies}
    assert "software" in fields_flagged or "producer" in fields_flagged
    for a in result.anomalies:
        assert a.severity == "high"


def test_flags_canva_regardless_of_document_type() -> None:
    # No type in the real 13-type KYC taxonomy is a "design something"
    # category, so — like Photoshop/Illustrator — this isn't excused by
    # the document being one the applicant is expected to complete.
    result = check_metadata_anomalies(
        "doc-1", {"Producer": "Canva"}, "fatca_crs_declaration"
    )
    assert len(result.anomalies) == 1
    assert result.anomalies[0].severity == "high"


def test_real_annotated_and_resaved_bank_statement_is_flagged() -> None:
    # User-supplied: took the genuine OCBC statement above, drew on it, and
    # saved — the resaving tool stripped every one of the original
    # Producer/Creator/date fields entirely AND left the file structurally
    # damaged. Before this fix that came back as Unable to Assess (nothing
    # to see) rather than flagged; this is the actual bug those two exact
    # warning strings exposed.
    result = check_metadata_anomalies(
        "doc-1",
        {
            "Warning (1)": "Error inflating stream",
            "Warning (2)": "Invalid xref stream in main dictionary",
            "PDFVersion": "1.4",
            "Linearized": "false",
        },
        "bank_statement",
    )
    assert result.unable_to_assess is False
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "parse_warnings"
    assert result.anomalies[0].severity == "medium"
    assert "Error inflating stream" in result.anomalies[0].value
    assert "Invalid xref stream in main dictionary" in result.anomalies[0].value


def test_single_warning_key_is_also_caught() -> None:
    # Confirmed empirically: exiftool -json emits a single "Warning" key
    # (no "(1)" suffix) when there's exactly one, e.g. against a
    # deliberately corrupted real PDF: {"Warning": "Invalid xref table"}.
    result = check_metadata_anomalies("doc-1", {"Warning": "Invalid xref table"})
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "parse_warnings"


def test_parse_warning_fires_regardless_of_document_type() -> None:
    # Unlike the PDF-editor tier, structural damage isn't excused by the
    # document being one the applicant is expected to edit.
    result = check_metadata_anomalies(
        "doc-1", {"Warning": "Invalid xref table"}, "fatca_crs_declaration"
    )
    assert len(result.anomalies) == 1


def test_flags_plain_adobe_acrobat_not_just_the_pro_edition() -> None:
    # Regression: the marker list only held "acrobat pro", so a real
    # install writing plain "Adobe Acrobat" — the most common of the three
    # spellings — sailed through untouched.
    result = check_metadata_anomalies("doc-1", {"Creator": "Adobe Acrobat"}, "bank_statement")
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "creator"
    assert result.anomalies[0].severity == "medium"


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


# Re-saved after creation ----------------------------------------------


def test_flags_institution_document_resaved_long_after_creation() -> None:
    result = check_metadata_anomalies(
        "doc-1",
        {"CreateDate": "2025:11:05 08:47:33", "ModifyDate": "2025:11:05 09:45:07"},
        "bank_statement",
    )
    assert len(result.anomalies) == 1
    assert result.anomalies[0].severity == "medium"
    assert "Re-saved" in result.anomalies[0].reason


def test_resave_gap_is_described_in_the_largest_sensible_unit() -> None:
    hours = check_metadata_anomalies(
        "doc-1",
        {"CreateDate": "2025:11:05 08:00:00", "ModifyDate": "2025:11:05 14:00:00"},
        "bank_statement",
    )
    assert "6 hour(s)" in hours.anomalies[0].reason

    days = check_metadata_anomalies(
        "doc-1",
        {"CreateDate": "2025:11:05 08:00:00", "ModifyDate": "2025:11:12 08:00:00"},
        "bank_statement",
    )
    # A statement re-saved a week after issue is the shape that matters
    # most here — the gap is the story, so it reads in days, not minutes.
    assert "7 day(s)" in days.anomalies[0].reason


# Impossible dates ------------------------------------------------------


def test_flags_creation_date_before_the_pdf_format_existed() -> None:
    result = check_metadata_anomalies("doc-1", {"CreateDate": "1987:01:01 00:00:00"})
    assert len(result.anomalies) == 1
    assert result.anomalies[0].severity == "high"
    assert "predates the PDF format" in result.anomalies[0].reason


def test_flags_future_date() -> None:
    future = datetime.now() + timedelta(days=400)
    result = check_metadata_anomalies(
        "doc-1", {"CreateDate": future.strftime("%Y:%m:%d %H:%M:%S")}
    )
    assert len(result.anomalies) == 1
    assert "in the future" in result.anomalies[0].reason


# XMP edit history ------------------------------------------------------


def test_flags_instance_id_differing_from_document_id_on_institution_document() -> None:
    result = check_metadata_anomalies(
        "doc-1",
        {"Author": "X", "DocumentID": "uuid:AAA", "InstanceID": "uuid:BBB"},
        "bank_statement",
    )
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "xmp_instance_id"
    assert result.anomalies[0].severity == "medium"


def test_provenance_ids_alone_are_still_unable_to_assess() -> None:
    # No authorship or dates to judge, so the AC2 six decide this — the
    # provenance fields are an enrichment, not grounds for assessability.
    result = check_metadata_anomalies(
        "doc-1", {"DocumentID": "uuid:AAA", "InstanceID": "uuid:BBB"}, "bank_statement"
    )
    assert result.unable_to_assess is True


def test_catches_editing_tool_recorded_only_in_the_edit_history() -> None:
    # The headline Producer looks clean; XMP still remembers what touched
    # the file earlier.
    result = check_metadata_anomalies(
        "doc-1",
        {
            "Producer": "ReportLab PDF Library",
            "HistorySoftwareAgent": ["Adobe Photoshop 24.0", "Acrobat Distiller"],
        },
        "bank_statement",
    )
    assert len(result.anomalies) == 1
    assert result.anomalies[0].field == "edit_history_tools"
    assert result.anomalies[0].severity == "high"
    # The list is joined into readable text rather than str()'d as a list.
    assert "['" not in (result.anomalies[0].value or "")


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


def test_subject_and_title_are_read_but_are_not_among_the_six_supported_fields() -> None:
    extracted = extract_supported_metadata({"Subject": "modified copy", "Title": "Constitution"})

    assert extracted["subject"] == "modified copy" and extracted["title"] == "Constitution"
    assert "subject" not in SUPPORTED_METADATA_FIELDS and "title" not in SUPPORTED_METADATA_FIELDS


def test_a_document_with_only_a_subject_or_title_is_still_unable_to_assess() -> None:
    result = check_metadata_anomalies("doc-1", {"Subject": "modified copy", "Title": "x", "FileType": "PDF"}, "company_constitution")

    assert result.unable_to_assess is True
