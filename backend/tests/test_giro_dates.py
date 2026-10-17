"""GIRO payments dated on days no bank processes GIRO, run by external validation."""

from __future__ import annotations

import asyncio

from app.fraud_detection.external_validation.runner import run_external_validation
from app.fraud_detection.font_consistency import ExtractedField


def _run(transactions: list[dict]):
    fields = [ExtractedField(field_name="transactions", field_type="array", field_value=transactions, confidence=0.99)]
    result = asyncio.run(run_external_validation("doc-1", "bank_statement", fields, []))
    return [f for f in result.findings if f.check_code == "BS.GIRO_BUSINESS_DAY"]


def test_flags_giro_on_a_sunday_and_a_public_holiday() -> None:
    findings = _run([
        {"date": "12 Jul 2026", "description": "Outward Remittance / GIRO / TAN HENG PACKAGING", "amount": -1},
        {"date": "10 Aug 2026", "description": "Giro Payment / GIRO / SP SERVICES LTD", "amount": -1},
    ])
    assert [f.field for f in findings] == ["transactions[0].date", "transactions[1].date"]
    assert "a Sunday" in findings[0].summary and "National Day (Observed)" in findings[1].summary


def test_ignores_saturday_giro_and_non_giro_payments_on_closed_days() -> None:
    assert _run([
        {"date": "01 Aug 2026", "description": "Giro Payment / GIRO / PREMISES RENTAL", "amount": -1},  # Saturday
        {"date": "12 Jul 2026", "description": "Inward Funds Transfer / FAST PAYMENT", "amount": 1},  # Sunday, FAST
        {"date": "12 Jul 2026", "description": "Card Transaction / GRAB* TRIP", "amount": -1},
    ]) == []


def test_prefers_the_value_date_when_extracted() -> None:
    findings = _run([{"date": "11 Jul 2026", "value_date": "12 Jul 2026", "description": "IBG CREDIT", "amount": 1}])
    assert len(findings) == 1 and findings[0].stated == "2026-07-12"
