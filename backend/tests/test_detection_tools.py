from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.fraud_detection.font_consistency import (
    AnomalyLocation,
    ExtractedField,
    FontAnomalyV2,
    FontConsistencyResult,
)
from app.fraud_detection.images.image_manipulation import (
    ImageManipulationIndicator,
    ImageManipulationResult,
    ImageTamperingIndicator,
    ImageTamperingResult,
    TamperingTolerances,
    Tolerances,
)
from app.fraud_detection.authenticity.metadata_analysis import MetadataAnalysisResult, MetadataAnomaly
from app.fraud_detection.spelling.spelling_anomaly import SpellingAnomaly, SpellingAnomalyResult
from app.langgraph.detection import tools
from app.langgraph.detection.field_checks import external, numerical
from app.langgraph.detection.field_checks.bindings import BindingArg


def _document(**attrs):
    return type("Document", (), attrs)()


@pytest.mark.asyncio
async def test_font_consistency_tool_findings_include_summary() -> None:
    """Regression: every tool's findings must carry 'summary' — specialists._collect_node
    and the Lead Investigator's review prompt both index it unconditionally, and a tool
    that forgot it used to crash detection mid-run with a KeyError."""
    result = FontConsistencyResult(
        document_id="doc-1",
        anomalies=[
            FontAnomalyV2(
                deviation_type="font",
                detected={"font": "Arial", "size": 11, "bold": False, "italic": False, "underline": False},
                expected={"font": "Times", "size": 11, "bold": False, "italic": False, "underline": False},
                occurrence_count=2,
                locations=[AnomalyLocation(page=0, bbox=(0, 0, 1, 1), text="x")],
                field_name="name",
            )
        ],
        all_detected_styles=[],
        unable_to_assess=False,
    )
    with (
        patch.object(tools, "_file_bytes", AsyncMock(return_value=b"x")),
        patch.object(tools, "_extracted_fields", AsyncMock(return_value=[])),
        patch.object(tools, "check_font_consistency", return_value=result),
    ):
        output = await tools.font_consistency_tool.ainvoke({"document_id": "doc-1"})

    assert output["findings"][0]["summary"]


@pytest.mark.asyncio
async def test_spelling_tool_findings_include_summary() -> None:
    result = SpellingAnomalyResult(
        document_id="doc-1",
        anomalies=[SpellingAnomaly(word="Helllo", surrounding_text="Helllo there", suggested_correction="hello")],
        unable_to_assess=False,
    )
    with (
        patch.object(tools, "_document", AsyncMock(return_value=_document(filename="f.pdf", document_type="nric"))),
        patch.object(tools, "_file_bytes", AsyncMock(return_value=b"x")),
        patch.object(tools, "_extracted_fields", AsyncMock(return_value=[])),
        patch.object(tools, "check_spelling_anomalies", AsyncMock(return_value=result)),
    ):
        output = await tools.spelling_tool.ainvoke({"document_id": "doc-1"})

    assert output["findings"][0]["summary"] == 'Possible misspelling "Helllo" (suggested: "hello").'


@pytest.mark.asyncio
async def test_spelling_tool_reports_a_repeated_word_as_one_finding_with_its_count() -> None:
    result = SpellingAnomalyResult(
        document_id="doc-1",
        anomalies=[SpellingAnomaly(
            word="Shenton", surrounding_text="2 Shenton Way #20-02", suggested_correction="shelton",
            occurrences=5, other_contexts=["Shenton Way branch"],
        )],
        unable_to_assess=False,
    )
    with (
        patch.object(tools, "_document", AsyncMock(return_value=_document(filename="f.pdf", document_type="nric"))),
        patch.object(tools, "_file_bytes", AsyncMock(return_value=b"x")),
        patch.object(tools, "_extracted_fields", AsyncMock(return_value=[])),
        patch.object(tools, "check_spelling_anomalies", AsyncMock(return_value=result)),
    ):
        output = await tools.spelling_tool.ainvoke({"document_id": "doc-1"})

    [finding] = output["findings"]
    assert finding["summary"] == 'Possible misspelling "Shenton" (suggested: "shelton"), appears 5 times.'
    assert finding["occurrences"] == 5
    assert finding["other_contexts"] == ["Shenton Way branch"]


def test_metadata_tool_findings_include_summary() -> None:
    result = MetadataAnalysisResult(
        document_id="doc-1",
        extracted_metadata={},
        anomalies=[MetadataAnomaly(field="author", value="admin", reason="placeholder", severity="low")],
        ai_generation_indicators=[],
        unable_to_assess=False,
    )
    with (
        patch.object(tools, "_document_with_metadata", AsyncMock(return_value=_document(metadata={}, document_type="nric"))),
        patch.object(tools, "check_metadata_anomalies", return_value=result),
    ):
        import asyncio

        output = asyncio.run(tools.metadata_tool.ainvoke({"document_id": "doc-1"}))

    assert output["findings"][0]["summary"]


@pytest.mark.asyncio
async def test_image_manipulation_tool_maps_indicators_to_findings() -> None:
    result = ImageManipulationResult(
        document_id="doc-1",
        regions=[],
        indicators=[
            ImageManipulationIndicator(
                indicator="Resolution Mismatch",
                page=0,
                bbox=(10.0, 20.0, 110.0, 70.0),
                detected="72 DPI",
                expected="300 DPI",
                deviation="4.2x lower",
                severity="high",
                summary="Region drawn at 72 DPI among 300 DPI images.",
                evidence={"xref": 12},
            )
        ],
        pages_checked=1,
        tolerances=Tolerances(),
        unable_to_assess=False,
    )
    with (
        patch.object(tools, "_file_bytes", AsyncMock(return_value=b"x")),
        patch.object(tools, "check_image_manipulation", return_value=result),
    ):
        output = await tools.image_manipulation_tool.ainvoke({"document_id": "doc-1"})

    finding = output["findings"][0]
    assert finding["id"] == "image_manipulation_tool-1"
    assert finding["summary"] == "[Resolution Mismatch] Region drawn at 72 DPI among 300 DPI images."
    assert finding["page"] == 1
    assert finding["severity_hint"] == "high"
    assert "evidence" not in finding


@pytest.mark.asyncio
async def test_image_manipulation_tool_reports_unable_to_assess() -> None:
    result = ImageManipulationResult(
        document_id="doc-1",
        regions=[],
        indicators=[],
        pages_checked=1,
        tolerances=Tolerances(),
        unable_to_assess=True,
        unable_to_assess_reason="Only one image region; nothing to compare against",
    )
    with (
        patch.object(tools, "_file_bytes", AsyncMock(return_value=b"x")),
        patch.object(tools, "check_image_manipulation", return_value=result),
    ):
        output = await tools.image_manipulation_tool.ainvoke({"document_id": "doc-1"})

    assert output["unable_to_assess"] is True
    assert output["reason"] == "Only one image region; nothing to compare against"
    assert output["findings"] == []


@pytest.mark.asyncio
async def test_image_tampering_tool_maps_indicators_to_findings() -> None:
    result = ImageTamperingResult(
        document_id="doc-1",
        regions=[],
        indicators=[
            ImageTamperingIndicator(
                indicator="Scaling Anomaly",
                page=0,
                bbox=(10.0, 20.0, 110.0, 70.0),
                detected="width-to-height 2.40",
                expected="1.00 (the original hidden underneath it)",
                deviation="2.4x wider than the original",
                severity="high",
                summary="An image was replaced by a copy stretched 2.4x out of proportion.",
                evidence={"xref": 12},
            )
        ],
        pages_checked=1,
        tolerances=TamperingTolerances(),
        unable_to_assess=False,
    )
    with (
        patch.object(tools, "_file_bytes", AsyncMock(return_value=b"x")),
        patch.object(tools, "check_image_tampering", return_value=result),
    ):
        output = await tools.image_tampering_tool.ainvoke({"document_id": "doc-1"})

    finding = output["findings"][0]
    assert finding["id"] == "image_tampering_tool-1"
    assert finding["indicator"] == "Scaling Anomaly"
    assert finding["summary"].startswith("[Scaling Anomaly] ")
    assert finding["page"] == 1
    assert finding["severity_hint"] == "high"
    assert "evidence" not in finding


async def _tampering_output(reason: str) -> dict:
    result = ImageTamperingResult(
        document_id="doc-1",
        regions=[],
        indicators=[],
        pages_checked=1,
        tolerances=TamperingTolerances(),
        unable_to_assess=True,
        unable_to_assess_reason=reason,
    )
    with (
        patch.object(tools, "_file_bytes", AsyncMock(return_value=b"x")),
        patch.object(tools, "check_image_tampering", return_value=result),
    ):
        return await tools.image_tampering_tool.ainvoke({"document_id": "doc-1"})


@pytest.mark.asyncio
async def test_image_tampering_tool_with_no_images_has_nothing_to_check() -> None:
    # A document with no embedded images passes: there is nothing to tamper with.
    output = await _tampering_output("No embedded images found across 1 page(s)")
    assert output["unable_to_assess"] is False
    assert output["nothing_to_check"] is True
    assert output["reason"].startswith("No embedded images found")
    assert output["findings"] == []


@pytest.mark.asyncio
async def test_image_tampering_tool_that_cannot_open_the_file_is_unable_to_assess() -> None:
    output = await _tampering_output("Failed to open document as a PDF: ValueError: bad")
    assert output["unable_to_assess"] is True
    assert output["nothing_to_check"] is False


@pytest.mark.parametrize(
    "reason, nothing",
    [
        ("No embedded images found across 2 page(s), so there are no image regions to compare.", True),
        ("Only 1 distinct image asset(s) large enough to compare (of 4 placement(s) found)", True),
        ("This PNG image carries no authorship or editing metadata (no author, ...)", True),
        ("None of the supported metadata fields (author, creator, ...) were present", True),
        ("No dates could be reliably extracted from this document", True),
        ("Not enough comparable text to assess layout. A one-page document ...", True),
        ("Nothing to check: This image is a flat graphic (logos, stamps, designs or rendered pages)", True),
        ("Nothing to check: this document contains no photographs, scans or other images", True),
        ("Could not run: the file could not be opened as an image.", False),
        ("No metadata was recorded for this document, which means exiftool wasn't available", False),
        ("No extractable text found in document", False),
        ("Failed to decode the image: OSError: truncated", False),
        ("Tool error: boom", False),
    ],
)
def test_nothing_to_check_covers_only_documents_a_check_does_not_apply_to(reason, nothing) -> None:
    assert tools._is_nothing_to_check(reason) is nothing


def test_layout_rendering_owns_both_image_tools() -> None:
    from app.langgraph.detection.specialists import SPECIALISTS

    names = {t.name for t in SPECIALISTS["layout_rendering"].tools}
    assert {"image_manipulation_tool", "image_tampering_tool"} <= names


def test_with_ids_keeps_a_finding_missing_its_summary() -> None:
    # Dropping it would let a flagged document read as clean.
    [finding] = tools._with_ids("fake_tool", [{"detail": "x"}])
    assert finding["id"] == "fake_tool-1"
    assert finding["detail"] == "x"
    assert finding["summary"]


def _balance_fields() -> list[ExtractedField]:
    return [
        ExtractedField(field_name="total_assets", field_type="number", field_value="1000", confidence=0.95),
        ExtractedField(field_name="total_liabilities", field_type="number", field_value="400", confidence=0.95),
        ExtractedField(field_name="total_equity", field_type="number", field_value="500", confidence=0.95),
    ]


@pytest.mark.asyncio
async def test_numerical_checks_tool_surfaces_non_bank_statement_findings() -> None:
    # Regression: the tool used to filter for check_code == "BS.RUNNING_BALANCE"
    # only, silently dropping every other document type's findings (FS.*, CR.*, etc).
    with patch.object(numerical, "document_fields", AsyncMock(return_value=_balance_fields())):
        output = await numerical.numerical_checks_tool.coroutine(
            document_id="doc-1", document_type="source_of_wealth",
        )

    assert output["findings"][0]["check_code"] == "FS.BALANCES"


@pytest.mark.asyncio
async def test_numerical_checks_tool_uses_the_specialists_bindings_without_a_binder_call() -> None:
    fields = [
        ExtractedField(field_name="closing", field_type="number", field_value="1000", confidence=0.95),
        ExtractedField(field_name="debts", field_type="number", field_value="400", confidence=0.95),
        ExtractedField(field_name="owners", field_type="number", field_value="500", confidence=0.95),
    ]
    bindings = [
        BindingArg(role="total_assets", field="closing"),
        BindingArg(role="total_liabilities", field="debts"),
        BindingArg(role="total_equity", field="owners"),
    ]
    with patch.object(numerical, "document_fields", AsyncMock(return_value=fields)),             patch("app.langgraph.detection.field_checks.binder.build_chat_anthropic") as model:
        output = await numerical.numerical_checks_tool.coroutine(
            document_id="doc-1", document_type="source_of_wealth", bindings=bindings,
        )

    model.assert_not_called()  # the specialist filled the bindings, so no second model is asked
    assert output["findings"][0]["check_code"] == "FS.BALANCES"
    assert {"role": "total_assets", "field": "closing"} in output["bindings_applied"]


def test_field_checks_hide_the_document_type_from_the_agent() -> None:
    # What the model is sent. `.args` also lists the injected arguments, which the model never sees.
    for tool in (numerical.numerical_checks_tool, external.external_validation_tool):
        assert list(tool.tool_call_schema.model_json_schema()["properties"]) == ["bindings"]


@pytest.mark.asyncio
async def test_external_validation_tool_runs_the_static_reference_checks_for_the_type() -> None:
    fields = [
        ExtractedField(field_name="gst_amount", field_type="number", field_value="1.00", confidence=0.95),
        ExtractedField(field_name="charges_before_gst", field_type="number", field_value="100.00", confidence=0.95),
        ExtractedField(field_name="bill_date", field_type="string", field_value="2026-03-01", confidence=0.95),
    ]
    with patch.object(external, "document_fields", AsyncMock(return_value=fields)):
        output = await external.external_validation_tool.coroutine(
            document_id="doc-1", document_type="proof_of_address",
        )

    assert [f["check_code"] for f in output["findings"]] == ["POA.GST"]
    assert output["findings"][0]["source"] == "IRAS GST rates"


# --- ai_generation_tool -------------------------------------------------------


def _ai_result(*images, unable: str | None = None, file_provenance=None):
    from app.fraud_detection.authenticity.ai_generation import AIGenerationConfig, AIGenerationResult

    return AIGenerationResult(
        document_id="doc-1",
        score=max([i.score for i in images], default=0.0),
        likelihood="low",
        images=list(images),
        file_provenance=file_provenance,
        config=AIGenerationConfig(),
        unable_to_assess=unable is not None,
        unable_to_assess_reason=unable,
    )


def _ai_image(score: float, likelihood: str):
    from app.fraud_detection.authenticity.ai_generation import AIImageAssessment, AIIndicator

    return AIImageAssessment(
        source="embedded", page=1, xref=7, bbox=(10.0, 20.0, 110.0, 120.0),
        pixel_width=800, pixel_height=600, format="jpeg", photographic=True,
        score=score, likelihood=likelihood, coverage=0.9,
        indicators=[
            AIIndicator(indicator="Spectral Peaks", assessed=True, value=40.0,
                        detail="Strongest periodic peak: 40.0x at a 2px period (fixed pattern).",
                        strength=1.0, weight=0.35, contribution=0.35),
            AIIndicator(indicator="Noise Level", assessed=True, value=0.3,
                        detail="Median noise 0.30 grey levels.", strength=1.0, weight=0.25, contribution=0.25),
            AIIndicator(indicator="Provenance", assessed=True, value=0.0, detail="None declared."),
        ],
    )


@pytest.mark.asyncio
async def test_ai_generation_tool_reports_only_medium_and_high_images() -> None:
    result = _ai_result(_ai_image(0.6, "medium"), _ai_image(0.1, "low"))
    with (
        patch.object(tools, "_file_bytes", AsyncMock(return_value=b"x")),
        patch.object(tools, "check_ai_generation", return_value=result),
    ):
        output = await tools.ai_generation_tool.ainvoke({"document_id": "doc-1"})

    assert len(output["findings"]) == 1
    finding = output["findings"][0]
    assert finding["id"] == "ai_generation_tool-1"
    assert finding["page"] == 2
    assert finding["severity_hint"] == "medium"
    assert finding["summary"].startswith("[AI Generation] Image on page 2 scores 0.60")
    assert [c["indicator"] for c in finding["contributors"]] == ["Spectral Peaks", "Noise Level"]


@pytest.mark.asyncio
async def test_ai_generation_tool_reports_declared_file_provenance() -> None:
    from app.fraud_detection.authenticity.ai_generation import AIIndicator

    provenance = AIIndicator(
        indicator="Provenance", assessed=True, value=0.95, strength=0.95,
        detail="declares digital source type 'trainedalgorithmicmedia'",
    )
    result = _ai_result(file_provenance=provenance)
    with (
        patch.object(tools, "_file_bytes", AsyncMock(return_value=b"x")),
        patch.object(tools, "check_ai_generation", return_value=result),
    ):
        output = await tools.ai_generation_tool.ainvoke({"document_id": "doc-1"})

    assert output["findings"][0]["severity_hint"] == "high"
    assert "trainedalgorithmicmedia" in output["findings"][0]["summary"]


@pytest.mark.asyncio
async def test_ai_generation_tool_reports_unable_to_assess() -> None:
    result = _ai_result(unable="No raster images of at least 128px to analyse")
    with (
        patch.object(tools, "_file_bytes", AsyncMock(return_value=b"x")),
        patch.object(tools, "check_ai_generation", return_value=result),
    ):
        output = await tools.ai_generation_tool.ainvoke({"document_id": "doc-1"})

    assert output["unable_to_assess"] is True
    assert output["findings"] == []


def test_digital_authenticity_owns_the_ai_generation_tool() -> None:
    from app.langgraph.detection.specialists import SPECIALISTS

    names = {t.name for t in SPECIALISTS["digital_authenticity"].tools}
    assert {"metadata_tool", "ai_generation_tool"} <= names


def _side_field(value: str) -> ExtractedField:
    return ExtractedField(field_name="document_side", field_type="string", field_value=value, confidence=0.95)


@pytest.mark.parametrize("raw, expected", [('"front"', "front"), ("back", "back"), ("single", None), (None, None)])
def test_document_side_reads_the_extracted_side(raw, expected) -> None:
    from app.langgraph.detection.specialists import document_side

    assert document_side([_side_field(raw)] if raw else []) == expected


async def test_registry_check_on_nric_front_is_nothing_to_check() -> None:
    from app.fraud_detection.external_validation.registry_lookups.runner import verify_against_registries

    result = await verify_against_registries("doc-1", [_side_field("front")], "nric")

    assert result.unable_to_assess_reason.startswith("Nothing to check:")
    assert tools._is_nothing_to_check(result.unable_to_assess_reason)


async def test_registry_check_on_nric_back_without_address_stays_unable() -> None:
    from app.fraud_detection.external_validation.registry_lookups.runner import verify_against_registries

    result = await verify_against_registries("doc-1", [_side_field("back")], "nric")

    assert result.unable_to_assess
    assert not tools._is_nothing_to_check(result.unable_to_assess_reason)


async def test_metadata_tool_reports_what_it_saw_when_there_are_no_anomalies() -> None:
    raw = {
        "Author": "A. Tan", "Producer": "ReportLab PDF Library", "FileType": "PDF",
        "CreateDate": "2026:08:31 10:00:00", "ModifyDate": "2026:08:31 10:00:00",
    }
    with patch.object(tools, "_document_with_metadata", AsyncMock(return_value=_document(metadata=raw, document_type="proof_of_address"))):
        output = await tools.metadata_tool.ainvoke({"document_id": "doc-1"})

    assert output["findings"] == []
    observed = output["observed_metadata"]
    assert observed["present"]["author"] == "A. Tan"
    assert "creator" in observed["absent"]
    assert "created equals modified" in observed["note"]


def test_a_binding_without_a_field_is_skipped_and_reported_instead_of_rejecting_the_whole_call() -> None:
    from app.langgraph.detection.field_checks.bindings import binding_problems, to_bindings

    args = [
        BindingArg(role="total_assets", field="closing"),
        BindingArg.model_validate({"row": 14, "role": "expense_line"}),
        BindingArg(field="orphan"),
    ]

    assert [b.role for b in to_bindings(args)] == ["total_assets"]
    assert binding_problems(args) == [
        "binding 1 was skipped: it has no field (row 14)",
        "binding 2 was skipped: it has no role",
    ]
    assert binding_problems(None) == []


async def test_metadata_tool_shows_the_subject_and_title_for_the_specialist_to_judge() -> None:
    raw = {
        "Author": "A. Tan", "Producer": "ReportLab PDF Library", "FileType": "PDF", "Subject": "modified copy",
        "Title": "edited_final_v2", "CreateDate": "2026:08:31 10:00:00", "ModifyDate": "2026:08:31 10:00:00",
    }
    with patch.object(tools, "_document_with_metadata", AsyncMock(return_value=_document(metadata=raw, document_type="company_constitution"))):
        output = await tools.metadata_tool.ainvoke({"document_id": "doc-1"})

    observed = output["observed_metadata"]
    assert observed["present"]["subject"] == "modified copy" and observed["present"]["title"] == "edited_final_v2"
    assert "subject" not in observed["absent"] and "title" not in observed["absent"]
    assert "subject modified copy" in observed["note"]
    assert output["findings"] == []  # read out for the specialist; no rule inspects them
