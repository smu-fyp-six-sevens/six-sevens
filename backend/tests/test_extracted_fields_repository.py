from __future__ import annotations

from unittest.mock import MagicMock, patch

from app import extracted_fields_repository
from app.fraud_detection.font_consistency_v2 import ExtractedField


def test_replace_extracted_fields_deletes_then_inserts_one_row_per_field() -> None:
    with patch("app.extracted_fields_repository.supabase_client") as mock_client:
        extracted_fields_repository.replace_extracted_fields(
            "doc-1",
            {"individual_name": "Jane Tan", "net_pay": 5000, "loan_purpose": "renovation"},
            default_confidence=0.9,
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


def test_replace_extracted_fields_uses_per_field_confidence_with_fallback() -> None:
    with patch("app.extracted_fields_repository.supabase_client") as mock_client:
        extracted_fields_repository.replace_extracted_fields(
            "doc-1",
            {"bank_name": "OCBC", "account_number": "1234", "loan_purpose": "renovation"},
            field_confidences={"bank_name": 0.98, "account_number": 0.4},
            default_confidence=0.7,
            required_field_names={"bank_name", "account_number"},
        )

        inserted_rows = mock_client.table.return_value.insert.call_args[0][0]
        by_name = {row["field_name"]: row for row in inserted_rows}
        # Fields the LLM gave a confidence for keep their own number...
        assert by_name["bank_name"]["confidence"] == 0.98
        assert by_name["account_number"]["confidence"] == 0.4
        # ...while one missing from field_confidences falls back to the
        # document-level default rather than being left blank.
        assert by_name["loan_purpose"]["confidence"] == 0.7


def test_replace_extracted_fields_skips_insert_when_no_fields() -> None:
    with patch("app.extracted_fields_repository.supabase_client") as mock_client:
        extracted_fields_repository.replace_extracted_fields(
            "doc-1", {}, default_confidence=0.0, required_field_names=set()
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
            default_confidence=0.5,
            required_field_names={"transactions"},
        )

        inserted_rows = mock_client.table.return_value.insert.call_args[0][0]
        by_name = {row["field_name"]: row for row in inserted_rows}
        assert by_name["transactions"]["field_type"] == "array"
        assert by_name["metadata_blob"]["field_type"] == "object"
        assert by_name["missing_field"]["field_type"] == "null"
        assert by_name["is_verified"]["field_type"] == "boolean"


def test_get_extracted_fields_queries_by_document_id_and_wraps_rows() -> None:
    with patch("app.extracted_fields_repository.supabase_client") as mock_client:
        result = MagicMock()
        result.data = [
            {
                "field_name": "individual_name",
                "field_type": "string",
                "field_value": "Jane Tan",
                "confidence": 0.95,
            },
            {
                "field_name": "net_pay",
                "field_type": "number",
                "field_value": 5000,
                "confidence": 0.9,
            },
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = result

        fields = extracted_fields_repository.get_extracted_fields("doc-1")

        mock_client.table.assert_called_once_with("extracted_fields")
        mock_client.table.return_value.select.assert_called_once_with("*")
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with(
            "document_id", "doc-1"
        )
        assert fields == [
            ExtractedField(
                field_name="individual_name", field_type="string", field_value="Jane Tan", confidence=0.95
            ),
            ExtractedField(field_name="net_pay", field_type="number", field_value=5000, confidence=0.9),
        ]


def test_get_extracted_fields_returns_empty_list_when_none_stored() -> None:
    with patch("app.extracted_fields_repository.supabase_client") as mock_client:
        result = MagicMock()
        result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = result

        assert extracted_fields_repository.get_extracted_fields("doc-1") == []


def test_get_extracted_fields_for_documents_returns_empty_dict_without_querying() -> None:
    with patch("app.extracted_fields_repository.supabase_client") as mock_client:
        result = extracted_fields_repository.get_extracted_fields_for_documents([])

        assert result == {}
        mock_client.table.assert_not_called()


def test_get_extracted_fields_for_documents_groups_rows_by_document_id() -> None:
    with patch("app.extracted_fields_repository.supabase_client") as mock_client:
        result = MagicMock()
        result.data = [
            {
                "document_id": "doc-1",
                "field_name": "individual_name",
                "field_type": "string",
                "field_value": "Jane Tan",
                "confidence": 0.95,
            },
            {
                "document_id": "doc-1",
                "field_name": "net_pay",
                "field_type": "number",
                "field_value": 5000,
                "confidence": 0.9,
            },
            {
                "document_id": "doc-2",
                "field_name": "bank_name",
                "field_type": "string",
                "field_value": "OCBC",
                "confidence": 0.8,
            },
        ]
        mock_client.table.return_value.select.return_value.in_.return_value.execute.return_value = result

        by_document = extracted_fields_repository.get_extracted_fields_for_documents(
            ["doc-1", "doc-2"]
        )

        mock_client.table.assert_called_once_with("extracted_fields")
        mock_client.table.return_value.select.assert_called_once_with("*")
        mock_client.table.return_value.select.return_value.in_.assert_called_once_with(
            "document_id", ["doc-1", "doc-2"]
        )
        assert set(by_document.keys()) == {"doc-1", "doc-2"}
        assert len(by_document["doc-1"]) == 2
        assert by_document["doc-2"] == [
            ExtractedField(field_name="bank_name", field_type="string", field_value="OCBC", confidence=0.8)
        ]
