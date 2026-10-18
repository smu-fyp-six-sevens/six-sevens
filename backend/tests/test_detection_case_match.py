from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.langgraph.detection.graph import synthesize_node


def test_detection_no_longer_adds_cross_document_identity_findings() -> None:
    """Pairing people across documents is the cross-document agent's job (compare_linked_people)."""

    async def run() -> dict:
        with (
            patch("app.case_service.get_case_match_for_document") as case_match,
            patch("app.langgraph.detection.graph.trace.record"),
        ):
            result = await synthesize_node({
                "document_id": "nric-1",
                "context": {"document_type": "nric"},
                "reports": [],
                "second_look_used": False,
            })
        case_match.assert_not_called()
        return result["result"]

    report = asyncio.run(run())

    assert all(f["tool"] != "case_identity_match" for f in report["findings"])
    assert not any(item["specialist"] == "identity_matching" for item in report["consulted"])
