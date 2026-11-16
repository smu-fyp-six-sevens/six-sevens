from __future__ import annotations

from unittest.mock import patch

import pytest

from app import extracted_fields_repository
from app.fraud_detection.font_consistency import ExtractedField
from app.models import DocumentStatus
from tests.support.fake_supabase import FakeSupabase

_ANALYZED = [DocumentStatus.ANALYZED]
_RUNNING = [DocumentStatus.PROCESSING, DocumentStatus.EXTRACTING]


def _saved(mock_service) -> tuple[str, list[dict]]:
    """(the document id, the rows) the save handed to the replace_extracted_fields function."""
    name, params = mock_service.return_value.rpc.call_args.args
    assert name == "replace_extracted_fields"
    return params["p_document_id"], params["p_rows"]


def test_replace_extracted_fields_saves_one_row_per_field_in_one_call() -> None:
    with patch("app.extracted_fields_repository.service_supabase_client") as mock_service:
        extracted_fields_repository.replace_extracted_fields(
            "doc-1",
            {"individual_name": "Jane Tan", "net_pay": 5000, "loan_purpose": "renovation"},
            default_confidence=0.9,
            expected_statuses=_RUNNING,
        )

        document_id, inserted_rows = _saved(mock_service)
        assert document_id == "doc-1"
        assert len(inserted_rows) == 3

        by_name = {row["field_name"]: row for row in inserted_rows}
        assert by_name["individual_name"]["field_type"] == "string"
        assert by_name["net_pay"]["field_type"] == "number"

        for row in inserted_rows:
            assert row["confidence"] == 0.9


def test_replace_extracted_fields_uses_per_field_confidence_with_fallback() -> None:
    with patch("app.extracted_fields_repository.service_supabase_client") as mock_service:
        extracted_fields_repository.replace_extracted_fields(
            "doc-1",
            {"bank_name": "OCBC", "account_number": "1234", "loan_purpose": "renovation"},
            field_confidences={"bank_name": 0.98, "account_number": 0.4},
            default_confidence=0.7,
            expected_statuses=_RUNNING,
        )

        _, inserted_rows = _saved(mock_service)
        by_name = {row["field_name"]: row for row in inserted_rows}
        # Fields the LLM gave a confidence for keep their own number...
        assert by_name["bank_name"]["confidence"] == 0.98
        assert by_name["account_number"]["confidence"] == 0.4
        # ...while one missing from field_confidences falls back to the
        # document-level default rather than being left blank.
        assert by_name["loan_purpose"]["confidence"] == 0.7


def test_replace_extracted_fields_with_no_fields_clears_the_set() -> None:
    with patch("app.extracted_fields_repository.service_supabase_client") as mock_service:
        extracted_fields_repository.replace_extracted_fields(
            "doc-1", {}, default_confidence=0.0, expected_statuses=_RUNNING
        )

        assert _saved(mock_service) == ("doc-1", [])


def test_replace_extracted_fields_infers_types_for_lists_dicts_and_null() -> None:
    with patch("app.extracted_fields_repository.service_supabase_client") as mock_service:
        extracted_fields_repository.replace_extracted_fields(
            "doc-1",
            {
                "transactions": [{"date": "2026-01-01", "amount": 100}],
                "metadata_blob": {"nested": True},
                "missing_field": None,
                "is_verified": True,
            },
            default_confidence=0.5,
            expected_statuses=_RUNNING,
        )

        _, inserted_rows = _saved(mock_service)
        by_name = {row["field_name"]: row for row in inserted_rows}
        assert by_name["transactions"]["field_type"] == "array"
        assert by_name["metadata_blob"]["field_type"] == "object"
        assert by_name["missing_field"]["field_type"] == "null"
        assert by_name["is_verified"]["field_type"] == "boolean"


def test_replace_extracted_fields_sends_the_expected_statuses_and_reports_a_refusal() -> None:
    with patch("app.extracted_fields_repository.service_supabase_client") as mock_service:
        mock_service.return_value.rpc.return_value.execute.return_value.data = False
        written = extracted_fields_repository.replace_extracted_fields(
            "doc-1", {"net_pay": 1}, default_confidence=0.9, expected_statuses=_RUNNING,
        )

    assert written is False
    assert mock_service.return_value.rpc.call_args.args[1]["p_expected_statuses"] == ["processing", "extracting"]


@pytest.fixture
def db(monkeypatch) -> FakeSupabase:
    fake = FakeSupabase()
    monkeypatch.setattr(extracted_fields_repository, "supabase_client", fake)
    return fake


def _without_id(field: ExtractedField) -> tuple:
    return (field.field_name, field.field_type, field.field_value, field.confidence)


def _field_row(document_id: str, name: str, value, field_type: str, confidence: float, **extra) -> dict:
    return {
        "document_id": document_id, "field_name": name, "field_type": field_type, "field_value": value,
        "confidence": confidence, **extra,
    }


def test_get_extracted_fields_returns_only_that_documents_fields(db) -> None:
    db.add("extracted_fields", _field_row("doc-1", "individual_name", "Jane Tan", "string", 0.95))
    db.add("extracted_fields", _field_row("doc-1", "net_pay", 5000, "number", 0.9))
    db.add("extracted_fields", _field_row("doc-2", "bank_name", "OCBC", "string", 0.8))

    fields = extracted_fields_repository.get_extracted_fields("doc-1")

    assert all(field.id for field in fields)  # each field carries its row id, which the officer's edits address
    assert sorted(_without_id(f) for f in fields) == [
        ("individual_name", "string", "Jane Tan", 0.95),
        ("net_pay", "number", 5000, 0.9),
    ]


def test_get_extracted_fields_returns_empty_list_when_none_stored(db) -> None:
    assert extracted_fields_repository.get_extracted_fields("doc-1") == []


def test_get_extracted_fields_for_documents_returns_empty_dict_without_querying(db) -> None:
    assert extracted_fields_repository.get_extracted_fields_for_documents([]) == {}
    assert db.tables == {}


def test_get_extracted_fields_for_documents_groups_rows_by_document_id(db) -> None:
    db.add("extracted_fields", _field_row("doc-1", "individual_name", "Jane Tan", "string", 0.95))
    db.add("extracted_fields", _field_row("doc-1", "net_pay", 5000, "number", 0.9))
    db.add("extracted_fields", _field_row("doc-2", "bank_name", "OCBC", "string", 0.8))
    db.add("extracted_fields", _field_row("doc-3", "left_out", "x", "string", 0.8))

    by_document = extracted_fields_repository.get_extracted_fields_for_documents(["doc-1", "doc-2"])

    assert set(by_document) == {"doc-1", "doc-2"}
    assert len(by_document["doc-1"]) == 2
    assert [_without_id(f) for f in by_document["doc-2"]] == [("bank_name", "string", "OCBC", 0.8)]


def test_add_extracted_field_stores_a_human_verified_row() -> None:
    with patch("app.extracted_fields_repository.service_supabase_client") as mock_service:
        mock_service.return_value.rpc.return_value.execute.return_value.data = True
        added = extracted_fields_repository.add_extracted_field("doc-1", "net_pay", 5000, expected_statuses=_ANALYZED)

    assert added is True
    name, params = mock_service.return_value.rpc.call_args.args
    assert name == "add_extracted_field"
    assert params == {
        "p_document_id": "doc-1", "p_field_name": "net_pay", "p_field_value": 5000, "p_field_type": "number",
        "p_expected_statuses": ["analyzed"],
    }


def test_delete_extracted_field_scopes_delete_to_its_own_document() -> None:
    # The function deletes by id and document together, so a guessed id can't remove another document's row.
    with patch("app.extracted_fields_repository.service_supabase_client") as mock_service:
        mock_service.return_value.rpc.return_value.execute.return_value.data = False
        deleted = extracted_fields_repository.delete_extracted_field("doc-1", "field-1", expected_statuses=_ANALYZED)

    assert deleted is False
    assert mock_service.return_value.rpc.call_args.args == (
        "delete_extracted_field",
        {"p_document_id": "doc-1", "p_field_id": "field-1", "p_expected_statuses": ["analyzed"]},
    )


def test_normalise_field_name_lowercases_and_joins_with_underscores() -> None:
    normalise = extracted_fields_repository.normalise_field_name
    assert normalise("Total Equity (SGD)") == "total_equity_sgd"
    assert normalise("  Share   Capital ") == "share_capital"
    assert normalise("Revenue - 2026") == "revenue_2026"
    assert normalise("!!!") == ""


def test_infer_field_type_guesses_from_text_values() -> None:
    infer = extracted_fields_repository._infer_field_type
    assert infer("2,729,000") == "number"
    assert infer("(1,000.00)") == "number"
    assert infer("-5") == "number"
    assert infer("true") == "boolean"
    assert infer("2026-10-06") == "date"
    assert infer("06/10/2026") == "date"
    assert infer("Acme Pte Ltd") == "string"
    assert infer("NaN") == "string"
    assert infer(42) == "number"
