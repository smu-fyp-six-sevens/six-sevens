"""
Keeps the regression suite's shared fixture data in step with the backend
(SCRUM-206).

The Playwright suite's mocked backend (frontend/e2e/support/mockBackend.ts)
can't import Python, so it reads each document type's required extraction
fields from tests/fixtures/regression/required_fields.json to enforce the
same "can't approve while a required field is missing" rule as
routers/documents.py. If EXTRACTION_SCHEMAS changes, this test fails until the JSON is regenerated with
scripts/regenerate_regression_fixtures.py.
"""
from __future__ import annotations

from pathlib import Path

from app.langgraph.extraction_prompts import EXTRACTION_SCHEMAS
from tests.support.regression_fixtures import check_fixture

REQUIRED_FIELDS = Path(__file__).parent / "fixtures" / "regression" / "required_fields.json"


def test_mocked_backend_required_fields_match_extraction_schemas():
    expected = {
        t: {
            "required": s.get("required", []),
            "allow_empty": s.get("allow_empty", []),
            "required_when_individual": s.get("required_when_individual", []),
        }
        for t, s in sorted(EXTRACTION_SCHEMAS.items())
    }
    check_fixture(REQUIRED_FIELDS, expected, sort_keys=False)
