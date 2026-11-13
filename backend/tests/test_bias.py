from __future__ import annotations

import pytest

from app.case_pipeline.cross_document.bias import is_protected_field


@pytest.mark.parametrize("name", [
    "dateOfBirth", "date_of_birth", "DOB", "title", "pass_type", "residency_status", "Nationality", "marital-status",
])
def test_a_protected_field_is_caught_in_any_spelling(name: str) -> None:
    assert is_protected_field(name)


@pytest.mark.parametrize("name", ["job_title", "jobTitle", "employer_name", "net_pay", "page"])
def test_an_ordinary_field_is_not_protected(name: str) -> None:
    assert not is_protected_field(name)
