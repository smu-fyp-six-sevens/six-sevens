from __future__ import annotations

from unittest.mock import patch

from app import extracted_fields_repository


def test_replace_extracted_fields_deletes_then_inserts_one_row_per_field() -> None:
    with patch("app.extracted_fields_repository.supabase_client") as mock_client:
        extracted_fields_repository.replace_extracted_fields(
            "doc-1",
            {"individual_name": "Jane Tan", "net_pay": 5000, "loan_purpose": "renovation"},
            confidence=0.9,
            required_field_names={"individual_name", "net_pay"},
        )

        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with(
            "document_id", "doc-1"
        )

        inserted_rows = mock_client.table.return_value.insert.call_args[0][0]
        assert len(inserted_rows) == 3

        by_name = {row["field_name"]: row for row in inserted_rows}
        assert by_name["individual_name"]["field_category"] == "required"
        assert by_name["individual_name"]["field_type"] == "string"
        assert by_name["net_pay"]["field_category"] == "required"
        assert by_name["net_pay"]["field_type"] == "number"
        # Not in required_field_names — an LLM-added extra field.
        assert by_name["loan_purpose"]["field_category"] == "additional"

        for row in inserted_rows:
            assert row["document_id"] == "doc-1"
            assert row["confidence"] == 0.9


def test_replace_extracted_fields_skips_insert_when_no_fields() -> None:
    with patch("app.extracted_fields_repository.supabase_client") as mock_client:
        extracted_fields_repository.replace_extracted_fields(
            "doc-1", {}, confidence=0.0, required_field_names=set()
        )

        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with(
            "document_id", "doc-1"
        )
        mock_client.table.return_value.insert.assert_not_called()


def test_replace_extracted_fields_infers_types_for_lists_dicts_and_null() -> None:
    with patch("app.extracted_fields_repository.supabase_client") as mock_client:
        extracted_fields_repository.replace_extracted_fields(
            "doc-1",
            {
                "transactions": [{"date": "2026-01-01", "amount": 100}],
                "metadata_blob": {"nested": True},
                "missing_field": None,
                "is_verified": True,
            },
            confidence=0.5,
            required_field_names={"transactions"},
        )

        inserted_rows = mock_client.table.return_value.insert.call_args[0][0]
        by_name = {row["field_name"]: row for row in inserted_rows}
        assert by_name["transactions"]["field_type"] == "array"
        assert by_name["metadata_blob"]["field_type"] == "object"
        assert by_name["missing_field"]["field_type"] == "null"
        assert by_name["is_verified"]["field_type"] == "boolean"
