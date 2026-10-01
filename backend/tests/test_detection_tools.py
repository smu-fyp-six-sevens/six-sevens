from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.fraud_detection.font_consistency import AnomalyLocation, FontAnomalyV2, FontConsistencyResult
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


def test_with_ids_rejects_finding_missing_summary() -> None:
    with pytest.raises(AssertionError):
        tools._with_ids("fake_tool", [{"id": "x"}])
