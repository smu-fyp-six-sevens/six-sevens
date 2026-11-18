from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import patch

from app import storage_service
from app.config import settings


class _FakeBucket:
    """An in-memory bucket with the few storage3 calls the service makes."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.remove_calls: list[list[str]] = []
        self.refuse_removal = False
        self.signed: list[tuple[str, int, dict]] = []

    def upload(self, path: str, data: bytes, options: dict) -> None:
        self.files[path] = data
        self.content_types[path] = options["content-type"]

    def download(self, path: str) -> bytes:
        return self.files[path]

    def remove(self, paths: list[str]) -> list[dict]:
        self.remove_calls.append(list(paths))
        if self.refuse_removal:  # what storage does when no policy allows the delete: an empty answer, no error
            return []
        for path in paths:
            self.files.pop(path, None)
        return [{"name": path} for path in paths]

    def create_signed_url(self, path: str, expires_in: int, options: dict) -> dict:
        self.signed.append((path, expires_in, options))
        query = f"?download={options['download']}" if "download" in options else ""
        return {"signedUrl": f"https://example.supabase.co/signed/{path}{query}"}


class _FakeClient:
    """Hands out the same bucket, and remembers which bucket name was asked for."""

    def __init__(self) -> None:
        self.bucket = _FakeBucket()
        self.buckets_used: list[str] = []
        self.storage = SimpleNamespace(from_=self._from)

    def _from(self, name: str) -> _FakeBucket:
        self.buckets_used.append(name)
        return self.bucket


def _storage():
    client = _FakeClient()
    return client, patch("app.storage_service.supabase_client", client)


def test_an_uploaded_file_lands_under_its_case_folder_and_can_be_read_back() -> None:
    client, patched = _storage()
    with patched:
        path = storage_service.upload_file("case-1", "statement.pdf", b"%PDF-1.4", "application/pdf")
        content = storage_service.download_file(path)

    assert path.startswith("case-1/") and path.endswith(".pdf") and "statement" not in path
    assert content == b"%PDF-1.4"
    assert client.bucket.content_types[path] == "application/pdf"
    assert set(client.buckets_used) == {settings.SUPABASE_STORAGE_BUCKET}


def test_the_same_filename_uploaded_twice_gets_two_distinct_paths() -> None:
    client, patched = _storage()
    with patched:
        first = storage_service.upload_file("case-1", "statement.pdf", b"one", "application/pdf")
        second = storage_service.upload_file("case-1", "statement.pdf", b"two", "application/pdf")

    assert first != second
    assert client.bucket.files == {first: b"one", second: b"two"}


def test_a_file_with_no_case_goes_to_the_unassigned_folder() -> None:
    _, patched = _storage()
    with patched:
        path = storage_service.upload_file(None, "statement.pdf", b"%PDF-1.4", "application/pdf")

    assert path.startswith("unassigned/")


def test_the_officers_file_name_never_shapes_the_path() -> None:
    _, patched = _storage()
    with patched:
        traversal = storage_service.upload_file("c", "../a b.PDF", b"x", "application/pdf")
        odd = storage_service.upload_file("c", "x.weird-ext!", b"x", "application/pdf")

    assert re.fullmatch(r"c/[0-9a-f-]{36}\.pdf", traversal)
    assert re.fullmatch(r"c/[0-9a-f-]{36}", odd)


def test_a_deleted_file_is_gone_and_the_others_stay() -> None:
    client, patched = _storage()
    with patched:
        kept = storage_service.upload_file("case-1", "keep.pdf", b"keep", "application/pdf")
        gone = storage_service.upload_file("case-1", "gone.pdf", b"gone", "application/pdf")
        storage_service.delete_file(gone)

    assert set(client.bucket.files) == {kept}


def test_deleting_many_files_is_one_removal_call() -> None:
    client, patched = _storage()
    with patched:
        paths = [storage_service.upload_file("case-1", f"{name}.pdf", b"x", "application/pdf") for name in "ab"]
        storage_service.delete_files(paths)

    assert client.bucket.files == {}
    assert client.bucket.remove_calls == [paths]


def test_a_removal_storage_refuses_is_logged() -> None:
    client, patched = _storage()
    with patched, patch("app.storage_service.logger") as logger:
        path = storage_service.upload_file("case-1", "stuck.pdf", b"x", "application/pdf")
        client.bucket.refuse_removal = True
        storage_service.delete_file(path)

    assert path in client.bucket.files
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args[1:3] == (0, 1)


def test_deleting_no_files_does_not_call_storage_at_all() -> None:
    client, patched = _storage()
    with patched:
        storage_service.delete_files([])

    assert client.buckets_used == []


def test_a_signed_url_for_viewing_has_no_download_option() -> None:
    client, patched = _storage()
    with patched:
        url = storage_service.create_signed_url("case-1/uuid_statement.pdf", 300)

    assert url == "https://example.supabase.co/signed/case-1/uuid_statement.pdf"
    assert client.bucket.signed == [("case-1/uuid_statement.pdf", 300, {})]


def test_a_signed_url_is_reused_while_it_has_time_left_and_dropped_with_its_file() -> None:
    client, patched = _storage()
    path = "case-1/uuid_reused.pdf"
    with patched:
        first, first_left = storage_service.signed_url(path, 1800)
        again, again_left = storage_service.signed_url(path, 1800)
        download, _ = storage_service.signed_url(path, 1800, download_filename="reused.pdf")
        storage_service.delete_file(path)
        storage_service.signed_url(path, 1800)

    assert again == first and first_left == 1800 and 0 < again_left <= 1800
    assert download.endswith("?download=reused.pdf")  # another option set: signed on its own
    assert [signed[0] for signed in client.bucket.signed] == [path, path, path]


def test_a_signed_url_can_ask_the_browser_to_download_under_a_filename() -> None:
    client, patched = _storage()
    with patched:
        url = storage_service.create_signed_url("case-1/uuid_statement.pdf", 300, download_filename="statement.pdf")

    assert url.endswith("?download=statement.pdf")
    assert client.bucket.signed == [("case-1/uuid_statement.pdf", 300, {"download": "statement.pdf"})]


def test_a_signed_url_past_its_expiry_by_the_wall_clock_is_signed_again(monkeypatch) -> None:
    # After the machine slept, a monotonic clock says little time passed while Supabase's (wall) clock says the
    # URL has expired: the URL must not be handed out again.
    client, patched = _storage()
    path = "case-1/uuid_slept.pdf"
    clock = [1_000_000.0]
    monkeypatch.setattr(storage_service.time, "time", lambda: clock[0])
    monkeypatch.setattr(storage_service.time, "monotonic", lambda: 0.0)  # stood still through the sleep
    with patched:
        storage_service.signed_url(path, 1800)
        clock[0] += 3600  # an hour asleep
        _, left = storage_service.signed_url(path, 1800)

    assert len(client.bucket.signed) == 2 and left == 1800
