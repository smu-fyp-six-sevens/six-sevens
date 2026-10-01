"""
Keeps the regression suite's shared fixture data in step with the backend
(SCRUM-206).

The Playwright suite's mocked backend (frontend/e2e/support/mockBackend.ts)
can't import Python, so it reads each document type's required extraction
fields from tests/fixtures/regression/required_fields.json to enforce the
same "can't approve while a required field is missing" rule as
routers/documents.py. If EXTRACTION_SCHEMAS changes, this test fails until
the JSON is regenerated:

    cd backend
    python -c "import json; from app.langgraph.extraction_prompts import EXTRACTION_SCHEMAS as S; \\
      print(json.dumps({t: s.get('required', []) for t, s in sorted(S.items())}, indent=2))" \\
      > tests/fixtures/regression/required_fields.json
"""
from __future__ import annotations

import json
from pathlib import Path

from app.langgraph.extraction_prompts import EXTRACTION_SCHEMAS

REQUIRED_FIELDS = Path(__file__).parent / "fixtures" / "regression" / "required_fields.json"


def test_mocked_backend_required_fields_match_extraction_schemas():
    expected = {t: s.get("required", []) for t, s in sorted(EXTRACTION_SCHEMAS.items())}
    assert json.loads(REQUIRED_FIELDS.read_text()) == expected, (
        "required_fields.json is out of date with EXTRACTION_SCHEMAS — regenerate it (see this file's docstring)"
    )
