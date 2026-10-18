from __future__ import annotations

from datetime import datetime, timedelta

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


def test_does_not_flag_pdf_editor_on_a_scanned_identity_document() -> None:
    # Regression: nric/passport used to be in
    # INSTITUTION_ORIGINATED_DOCUMENT_TYPES on the (wrong) assumption that
    # they're issuer-generated PDFs like a bank statement. There is no such
    # thing — every one is a physical card an applicant photographed or
    # scanned, so a common scanning app (e.g. Adobe Scan, which writes
    # Acrobat-branded metadata) is the NORMAL producer here, not evidence
    # of tampering.
    for document_type in ("nric", "passport"):
        result = check_metadata_anomalies(
            "doc-1", {"Producer": "Adobe Acrobat Pro DC 23"}, document_type
        )
        assert result.anomalies == [], f"false positive for {document_type}"


def test_real_scanned_nric_metadata_is_clean() -> None:
    # A genuine scanned NRIC (user-supplied sample): PDFium as both
    # Creator and Producer, a single CreateDate with no ModifyDate at all.
    result = check_metadata_anomalies(
        "doc-1",
        {"Creator": "PDFium", "Producer": "PDFium", "CreateDate": "2023:03:09 17:09:29"},
        "nric",
    )
    assert result.anomalies == []
    assert result.unable_to_assess is False


def test_real_standard_chartered_statement_metadata_is_clean() -> None:
    # A genuine Standard Chartered eStatement (user-supplied): OpenPDF
    # (a real, legitimate open-source PDF library), no Creator at all, and
    # only a CreateDate with no ModifyDate — a different metadata shape
    # from OCBC's, confirming there's no single universal "bank statement"
    # signature and a baseline needs to be per-issuer, not per-type.
    result = check_metadata_anomalies(
        "doc-1",
        {"Producer": "OpenPDF 2.0.2", "CreateDate": "2025:10:01 17:55:29Z"},
        "bank_statement",
    )
    assert result.anomalies == []
    assert result.unable_to_assess is False


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


def test_creator_field_can_legitimately_hold_a_person_name() -> None:
    # Documents the confirmed exiftool collision itself: "creator" isn't
    # always a tool name and shouldn't be assumed to be one — a genuine
    # person's name there (with no other marker present) must stay clean.
    result = check_metadata_anomalies(
        "doc-1", {"Creator": "TAN YI XI JOVI _"}, "bank_statement"
    )
    assert result.anomalies == []


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


def test_no_parse_warning_key_present_is_not_flagged() -> None:
    result = check_metadata_anomalies("doc-1", {"Author": "Jane Tan"})
    assert result.anomalies == []


def test_real_ios_notes_passport_scan_metadata_is_clean() -> None:
    # A genuine passport scan made via iOS's Notes app scan-to-PDF feature
    # (user-supplied): Producer names the OS/Quartz PDF stack, Creator is
    # the app ("Notes") — exactly the kind of applicant-side capture
    # tooling that must NOT be scrutinised like an issuer document (see
    # the nric/passport exclusion above). CreateDate == ModifyDate to the
    # second, as a single scan-to-PDF pass should be.
    result = check_metadata_anomalies(
        "doc-1",
        {
            "Producer": "iOS Version 17.1.2 (Build 21B101) Quartz PDFContext",
            "Creator": "Notes",
            "CreateDate": "2025:10:12 08:19:18Z",
            "ModifyDate": "2025:10:12 08:19:18Z",
        },
        "passport",
    )
    assert result.anomalies == []
    assert result.unable_to_assess is False


def test_real_ocbc_bank_statement_metadata_is_clean() -> None:
    # Two genuine OCBC statements (user-supplied), 7 months apart, with
    # identical Producer/Creator — confirms the same-pass tolerance holds
    # for a real institutional generator (both gaps are single-digit
    # seconds, well under the 60s tolerance).
    for create, modify in [
        ("2026:02:03 11:43:08+08:00", "2026:02:03 11:43:11+08:00"),
        ("2026:09:02 22:36:34+08:00", "2026:09:02 22:36:38+08:00"),
    ]:
        result = check_metadata_anomalies(
            "doc-1",
            {
                "Producer": "Streamline PDFGen for OCBC Group",
                "Creator": "pdfgen",
                "CreateDate": create,
                "ModifyDate": modify,
            },
            "bank_statement",
        )
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


def test_does_not_flag_resave_on_a_document_the_applicant_edits() -> None:
    # Verified against the real GLDB FATCA form, whose dates are an hour
    # apart because someone authored it in Word — entirely normal.
    result = check_metadata_anomalies(
        "doc-1",
        {"CreateDate": "2025:11:05 08:47:33", "ModifyDate": "2025:11:05 09:45:07"},
        "fatca_crs_declaration",
    )
    assert result.anomalies == []


def test_does_not_flag_same_pass_generation_gap() -> None:
    # A single conversion pass produced a 1-second gap on a real file, and
    # ReportLab writes the two dates identically — neither is a re-save.
    result = check_metadata_anomalies(
        "doc-1",
        {"CreateDate": "2026:09:19 14:43:14", "ModifyDate": "2026:09:19 14:43:15"},
        "bank_statement",
    )
    assert result.anomalies == []


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


def test_does_not_flag_a_date_a_few_hours_ahead() -> None:
    # Clock skew and the timezone offsets _parse_exif_datetime ignores
    # shouldn't read as fabrication.
    soon = datetime.now() + timedelta(hours=6)
    result = check_metadata_anomalies(
        "doc-1", {"CreateDate": soon.strftime("%Y:%m:%d %H:%M:%S")}
    )
    assert result.anomalies == []


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


def test_does_not_flag_matching_instance_and_document_ids() -> None:
    result = check_metadata_anomalies(
        "doc-1",
        {"Author": "X", "DocumentID": "uuid:AAA", "InstanceID": "uuid:AAA"},
        "bank_statement",
    )
    assert result.anomalies == []


def test_does_not_flag_differing_ids_on_an_applicant_edited_document() -> None:
    # The real GLDB FATCA form has differing IDs legitimately.
    result = check_metadata_anomalies(
        "doc-1",
        {"Author": "X", "DocumentID": "uuid:AAA", "InstanceID": "uuid:BBB"},
        "fatca_crs_declaration",
    )
    assert result.anomalies == []


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
