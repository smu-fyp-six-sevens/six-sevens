from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.fraud_detection.font_consistency import AnomalyLocation, FontAnomalyV2, FontConsistencyResult
from app.fraud_detection.image_manipulation import (
    ImageManipulationIndicator,
    ImageManipulationResult,
    ImageTamperingIndicator,
    ImageTamperingResult,
    TamperingTolerances,
    Tolerances,
)
from app.fraud_detection.metadata_analysis import MetadataAnalysisResult, MetadataAnomaly
from app.fraud_detection.numerical_validation import (
    BenfordAnalysis,
    NumericalFinding,
    NumericalValidationResult,
)
from app.fraud_detection.spelling_anomaly import SpellingAnomaly, SpellingAnomalyResult
from app.langgraph.detection import tools


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

    assert output["findings"][0]["summary"]


def test_metadata_tool_findings_include_summary() -> None:
    result = MetadataAnalysisResult(
        document_id="doc-1",
        extracted_metadata={},
        anomalies=[MetadataAnomaly(field="author", value="admin", reason="placeholder", severity="low")],
        ai_generation_indicators=[],
        unable_to_assess=False,
    )
    with (
        patch.object(tools, "_document", AsyncMock(return_value=_document(metadata={}, document_type="nric"))),
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


@pytest.mark.asyncio
async def test_image_tampering_tool_reports_unable_to_assess() -> None:
    result = ImageTamperingResult(
        document_id="doc-1",
        regions=[],
        indicators=[],
        pages_checked=1,
        tolerances=TamperingTolerances(),
        unable_to_assess=True,
        unable_to_assess_reason="No embedded images found across 1 page(s)",
    )
    with (
        patch.object(tools, "_file_bytes", AsyncMock(return_value=b"x")),
        patch.object(tools, "check_image_tampering", return_value=result),
    ):
        output = await tools.image_tampering_tool.ainvoke({"document_id": "doc-1"})

    assert output["unable_to_assess"] is True
    assert output["findings"] == []


def test_layout_rendering_owns_both_image_tools() -> None:
    from app.langgraph.detection.specialists import SPECIALISTS

    names = {t.name for t in SPECIALISTS["layout_rendering"].tools}
    assert {"image_manipulation_tool", "image_tampering_tool"} <= names


def test_with_ids_rejects_finding_missing_summary() -> None:
    with pytest.raises(AssertionError):
        tools._with_ids("fake_tool", [{"id": "x"}])


@pytest.mark.asyncio
async def test_numerical_validation_tool_surfaces_non_bank_statement_findings() -> None:
    # Regression: the tool used to filter for check_code == "BS.RUNNING_BALANCE"
    # only, silently dropping every other document type's findings (FS.*, CR.*, etc).
    result = NumericalValidationResult(
        document_id="doc-1",
        document_type="source_of_wealth",
        values_analysed=4,
        findings=[
            NumericalFinding(
                check_code="FS.IS_CAST", severity="impossible", summary="Income statement doesn't cast.",
            )
        ],
        skipped_checks=[],
        benford=BenfordAnalysis(sample_size=0, digits=[], unable_to_assess=True),
        unable_to_assess=False,
    )
    with (
        patch.object(tools, "_document", AsyncMock(return_value=_document(document_type="source_of_wealth"))),
        patch.object(tools, "_extracted_fields", AsyncMock(return_value=[])),
        patch.object(tools, "check_numerical_validation", return_value=result),
    ):
        output = await tools.numerical_validation_tool.ainvoke({"document_id": "doc-1"})

    assert output["findings"][0]["check_code"] == "FS.IS_CAST"


# --- ai_generation_tool -------------------------------------------------------


def _ai_result(*images, unable: str | None = None, file_provenance=None):
    from app.fraud_detection.ai_generation import AIGenerationConfig, AIGenerationResult

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
    from app.fraud_detection.ai_generation import AIImageAssessment, AIIndicator

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
    from app.fraud_detection.ai_generation import AIIndicator

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
