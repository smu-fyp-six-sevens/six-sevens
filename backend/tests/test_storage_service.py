from __future__ import annotations

from unittest.mock import patch

from app import storage_service
from app.config import settings


def test_upload_file_builds_uuid_prefixed_path_under_case_folder() -> None:
    with patch("app.storage_service.supabase_client") as mock_client:
        storage_path = storage_service.upload_file(
            "case-1", "statement.pdf", b"%PDF-1.4", "application/pdf"
        )

        assert storage_path.startswith("case-1/")
        assert storage_path.endswith("_statement.pdf")
        mock_client.storage.from_.assert_called_once_with(settings.SUPABASE_STORAGE_BUCKET)
        upload_call = mock_client.storage.from_.return_value.upload
        upload_call.assert_called_once_with(
            storage_path, b"%PDF-1.4", {"content-type": "application/pdf"}
        )


def test_upload_file_uses_unassigned_folder_when_no_case_id() -> None:
    with patch("app.storage_service.supabase_client"):
        storage_path = storage_service.upload_file(
            None, "statement.pdf", b"%PDF-1.4", "application/pdf"
        )

        assert storage_path.startswith("unassigned/")


def test_download_file_returns_bytes_from_bucket() -> None:
    with patch("app.storage_service.supabase_client") as mock_client:
        mock_client.storage.from_.return_value.download.return_value = b"%PDF-1.4 bytes"

        content = storage_service.download_file("case-1/uuid_statement.pdf")

        assert content == b"%PDF-1.4 bytes"
        mock_client.storage.from_.assert_called_once_with(settings.SUPABASE_STORAGE_BUCKET)
        mock_client.storage.from_.return_value.download.assert_called_once_with(
            "case-1/uuid_statement.pdf"
        )


def test_delete_file_removes_by_path() -> None:
    with patch("app.storage_service.supabase_client") as mock_client:
        storage_service.delete_file("case-1/uuid_statement.pdf")

        mock_client.storage.from_.assert_called_once_with(settings.SUPABASE_STORAGE_BUCKET)
        mock_client.storage.from_.return_value.remove.assert_called_once_with(
            ["case-1/uuid_statement.pdf"]
        )


def test_delete_files_batches_removal_of_many_paths() -> None:
    with patch("app.storage_service.supabase_client") as mock_client:
        storage_service.delete_files(["case-1/a.pdf", "case-1/b.pdf"])

        mock_client.storage.from_.assert_called_once_with(settings.SUPABASE_STORAGE_BUCKET)
        mock_client.storage.from_.return_value.remove.assert_called_once_with(
            ["case-1/a.pdf", "case-1/b.pdf"]
        )


def test_delete_files_is_a_noop_for_an_empty_list() -> None:
    with patch("app.storage_service.supabase_client") as mock_client:
        storage_service.delete_files([])

        mock_client.storage.from_.assert_not_called()


def test_create_signed_url_without_download_filename() -> None:
    with patch("app.storage_service.supabase_client") as mock_client:
        mock_client.storage.from_.return_value.create_signed_url.return_value = {
            "signedUrl": "https://example.supabase.co/signed/doc-1",
            "signedURL": "https://example.supabase.co/signed/doc-1",
        }

        url = storage_service.create_signed_url("case-1/uuid_statement.pdf", 300)

        assert url == "https://example.supabase.co/signed/doc-1"
        mock_client.storage.from_.return_value.create_signed_url.assert_called_once_with(
            "case-1/uuid_statement.pdf", 300, {}
        )


def test_create_signed_url_with_download_filename_sets_download_option() -> None:
    with patch("app.storage_service.supabase_client") as mock_client:
        mock_client.storage.from_.return_value.create_signed_url.return_value = {
            "signedUrl": "https://example.supabase.co/signed/doc-1",
        }

        storage_service.create_signed_url(
            "case-1/uuid_statement.pdf", 300, download_filename="statement.pdf"
        )

        mock_client.storage.from_.return_value.create_signed_url.assert_called_once_with(
            "case-1/uuid_statement.pdf", 300, {"download": "statement.pdf"}
        )
