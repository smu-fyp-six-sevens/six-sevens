"""
The regression fixtures the Playwright suite's mocked backend reads (tests/fixtures/regression/*.json).

A test only ever compares: `check_fixture` fails when the stored file differs from what the backend produces now.
To change a fixture, run `python scripts/regenerate_regression_fixtures.py`, which sets REGENERATE_REGRESSION_FIXTURES
for those tests. With it set, `check_fixture` writes the file and skips the test, so a run that rewrites a fixture can
never pass as a run that matched it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

REGENERATE = "REGENERATE_REGRESSION_FIXTURES"


def check_fixture(path: Path, data: Any, *, sort_keys: bool = True) -> None:
    """Fails unless the stored fixture equals `data`. When regenerating, writes it and skips instead."""
    if os.environ.get(REGENERATE):
        path.write_text(json.dumps(data, indent=2, sort_keys=sort_keys) + "\n")
        pytest.skip(f"{path.name} rewritten; run the tests again without {REGENERATE} to compare")
    assert json.loads(path.read_text()) == json.loads(json.dumps(data)), (
        f"{path.name} is out of date with the backend: run `python scripts/regenerate_regression_fixtures.py`"
    )
