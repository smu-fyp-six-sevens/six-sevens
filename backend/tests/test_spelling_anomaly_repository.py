from __future__ import annotations

from unittest.mock import patch

from app import spelling_anomaly_repository
from app.fraud_detection.spelling_anomaly import SpellingAnomaly


def test_replace_spelling_anomalies_deletes_then_inserts_one_row_per_anomaly() -> None:
    with patch("app.spelling_anomaly_repository.supabase_client") as mock_client:
        spelling_anomaly_repository.replace_spelling_anomalies(
            "doc-1",
            "case-1",
            [
                SpellingAnomaly(
                    page=1, section=0, word="Helllo",
                    surrounding_text="Helllo there", suggested_correction="hello",
                ),
                SpellingAnomaly(
                    page=1, section=2, word="Kowalksi",
                    surrounding_text="Mr Kowalksi signed", suggested_correction=None,
                ),
            ],
        )

        # Scoped to this check's own rows — flags is shared across every
        # detection check, so an unscoped delete would wipe out another
        # check's rows for the same document.
        mock_delete = mock_client.table.return_value.delete.return_value
        mock_delete.eq.assert_called_once_with("document_id", "doc-1")
        mock_delete.eq.return_value.eq.assert_called_once_with("check_name", "spelling_anomaly")

        inserted_rows = mock_client.table.return_value.insert.call_args[0][0]
        assert len(inserted_rows) == 2

        by_word = {row["evidence"]["word"]: row for row in inserted_rows}
        assert by_word["Helllo"]["evidence"]["page"] == 1
        assert by_word["Helllo"]["evidence"]["section"] == 0
        assert by_word["Helllo"]["evidence"]["surrounding_text"] == "Helllo there"
        assert by_word["Helllo"]["evidence"]["suggested_correction"] == "hello"
        assert "hello" in by_word["Helllo"]["description"]
        assert by_word["Kowalksi"]["evidence"]["suggested_correction"] is None
        assert "no clear correction" in by_word["Kowalksi"]["description"]

        for row in inserted_rows:
            assert row["document_id"] == "doc-1"
            assert row["case_id"] == "case-1"
            assert row["check_name"] == "spelling_anomaly"
            assert row["detection_dimension"] == "content_validation"


def test_replace_spelling_anomalies_skips_insert_when_no_anomalies() -> None:
    with patch("app.spelling_anomaly_repository.supabase_client") as mock_client:
        spelling_anomaly_repository.replace_spelling_anomalies("doc-1", None, [])

        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with(
            "document_id", "doc-1"
        )
        mock_client.table.return_value.insert.assert_not_called()
