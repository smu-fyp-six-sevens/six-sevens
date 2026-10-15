from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.case_match import (
    CheckOutcome,
    DocumentCaseMatch,
    DocumentMatchStatus,
    FieldComparison,
)
from app.langgraph.detection.graph import synthesize_node


def test_detection_report_includes_identity_mismatch_with_both_documents() -> None:
    case_match = DocumentCaseMatch(
        document_id="nric-1",
        filename="director-nric.pdf",
        document_type="nric",
        status=DocumentMatchStatus.MISMATCH,
        can_proceed_to_fraud_detection=True,
        case_mismatch=True,
        reason="Name differs",
        comparisons=[
            FieldComparison(
                check="individual_name",
                outcome=CheckOutcome.MISMATCH,
                document_field="individual_name",
                document_value="GOH KA YEO",
                other_document_id="statement-1",
                other_filename="bank-statement.pdf",
                case_value="GOH KA YEE",
                reason='Name "GOH KA YEO" differs from "GOH KA YEE"',
            )
        ],
    )

    async def run() -> dict:
        with (
            patch(
                "app.case_service.get_case_match_for_document",
                return_value=case_match,
            ),
            patch("app.langgraph.detection.graph.trace.record") as record,
        ):
            result = await synthesize_node({
                "document_id": "nric-1",
                "context": {"document_type": "nric"},
                "reports": [],
                "second_look_used": False,
            })
        record.assert_called_once()
        return result["result"]

    report = asyncio.run(run())

    assert report["verdict"] == "flagged"
    assert "Identity matching found 1" in report["summary"]
    finding = next(f for f in report["findings"] if f["tool"] == "case_identity_match")
    assert "director-nric.pdf" in finding["summary"]
    assert "bank-statement.pdf" in finding["summary"]
    assert finding["specialists"] == ["identity_matching"]
    assert any(item["specialist"] == "identity_matching" for item in report["consulted"])
