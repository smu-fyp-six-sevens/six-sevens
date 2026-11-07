"""
Regression tests (SCRUM-296) for AI-generated content — "Test Cases -
Sprint 4.xlsx", Fraud Detection sheet, Detect AI-Generated Content
(SCRUM-67): FD-027, FD-028.

Same approach as Sprint 3's fraud-detection regressions (and the same
harness, from test_regression_detection.py): the real detection graph runs
end to end with the real tools on the document's bytes, and only Claude's
replies are scripted — the Layout & Rendering and Digital Authenticity
specialists run their checks and rate whatever those checks find.

The live run used a passport scan reworked with AI (FD-027) and a clean
NRIC (FD-028); neither is in the repository. Here each is an uploaded image
generated in the test: a photo-like scan carrying a generator's upsampling
checkerboard and no sensor noise, and the same kind of scan with ordinary
sensor noise and no checkerboard (the fixtures test_ai_generation.py
calibrates the check against).
"""
from __future__ import annotations

import pytest

from app.langgraph.detection.graph import run_detection
from tests.support import detection_harness
from tests.support.ai_images import png, scene, with_checkerboard
from tests.support.detection_harness import DIGITAL, LAYOUT, Specialist, consulted, findings_from

# The Sprint 3 harness's fixtures: scripted Claude, the in-memory store, and the cleanup after each run.
ai = detection_harness.ai
store = detection_harness.store
_clear_detection_memory = detection_harness._clear_detection_memory


def ai_generated_passport_scan() -> bytes:
    # 768 x 700: not one of the preset sizes a generator outputs, so only the pixels give it away.
    return png(with_checkerboard(scene(noise=0.0, falloff=2.0))[:, :700])


def clean_passport_scan() -> bytes:
    return png(scene()[:, :700])


def _layout_and_digital_checks(ai) -> None:
    ai.specialists[LAYOUT] = Specialist(tools=["image_tampering_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool", "ai_generation_tool"])


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-67", "FD-027")
async def test_an_ai_generated_scan_is_flagged_for_ai_generation(ai, store):
    document_id = store.add("C011-P1_Passport_scan.png", "passport", ai_generated_passport_scan())
    _layout_and_digital_checks(ai)

    report = await run_detection(document_id)

    [finding] = findings_from(report, "ai_generation_tool")
    assert finding["indicator"] == "AI Generation"
    assert finding["severity"] in ("medium", "high")
    assert "Spectral Peaks" in finding["summary"]
    assert finding["specialists"] == [DIGITAL]
    assert report["verdict"] == "flagged"
    # Both the layout and the digital authenticity checks ran.
    assert "image_tampering_tool" in consulted(report, LAYOUT)["tools_called"]
    assert {"metadata_tool", "ai_generation_tool"} <= set(consulted(report, DIGITAL)["tools_called"])


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-67", "FD-028")
async def test_a_clean_scan_is_not_flagged_for_ai_generation(ai, store):
    document_id = store.add("C004-P1_NRIC_scan.png", "nric", clean_passport_scan())
    _layout_and_digital_checks(ai)

    report = await run_detection(document_id)

    assert findings_from(report, "ai_generation_tool") == []
    assert "ai_generation_tool" in consulted(report, DIGITAL)["tools_called"]
    assert "image_tampering_tool" in consulted(report, LAYOUT)["tools_called"]
    assert not any(u["tool"] == "ai_generation_tool" for u in report["unable_to_assess"])
