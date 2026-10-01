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
