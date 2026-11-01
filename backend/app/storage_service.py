# storage_service.py
from __future__ import annotations

import threading
import time
import uuid

from storage3.types import URLOptions

from .config import settings
from .supabase_client import supabase_client

# Signed URLs handed out, reused while they have at least _MIN_LEFT_SECONDS to run: opening a document's preview
# again needn't sign its file again. Per process, keyed by (path, download filename); oldest dropped first.
_MIN_LEFT_SECONDS = 300
_MAX_SIGNED = 500
_signed: dict[tuple[str, str | None], tuple[str, float]] = {}
_signed_lock = threading.Lock()


def upload_file(
    case_id: str | None, filename: str, file_bytes: bytes, content_type: str
) -> str:
    """Uploads the file and returns its storage path."""
    storage_folder = case_id or "unassigned"
    storage_path = f"{storage_folder}/{uuid.uuid4()}_{filename}"
    supabase_client.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
        storage_path, file_bytes, {"content-type": content_type}
    )
    return storage_path


def download_file(storage_path: str) -> bytes:
    return supabase_client.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(storage_path)


def _forget_signed(storage_paths: list[str]) -> None:
    paths = set(storage_paths)
    with _signed_lock:
        for key in [key for key in _signed if key[0] in paths]:
            _signed.pop(key, None)


def delete_file(storage_path: str) -> None:
    _forget_signed([storage_path])
    supabase_client.storage.from_(settings.SUPABASE_STORAGE_BUCKET).remove([storage_path])


def delete_files(storage_paths: list[str]) -> None:
    """Batch version of delete_file — one round-trip for many paths."""
    if not storage_paths:
        return
    _forget_signed(storage_paths)
    supabase_client.storage.from_(settings.SUPABASE_STORAGE_BUCKET).remove(storage_paths)


def signed_url(storage_path: str, expires_in: int, download_filename: str | None = None) -> tuple[str, int]:
    """A signed URL for the file and the seconds it has left: one already handed out while it has enough time left,
    else a new one."""
    key = (storage_path, download_filename)
    now = time.monotonic()
    with _signed_lock:
        hit = _signed.get(key)
    if hit is not None and hit[1] - now >= _MIN_LEFT_SECONDS:
        return hit[0], int(hit[1] - now)
    url = create_signed_url(storage_path, expires_in, download_filename)
    with _signed_lock:
        _signed.pop(key, None)
        _signed[key] = (url, now + expires_in)
        while len(_signed) > _MAX_SIGNED:
            _signed.pop(next(iter(_signed)), None)
    return url, expires_in


def create_signed_url(
    storage_path: str,
    expires_in: int,
    download_filename: str | None = None,
) -> str:
    options: URLOptions = {}
    if download_filename:
        options["download"] = download_filename
    data = supabase_client.storage.from_(settings.SUPABASE_STORAGE_BUCKET).create_signed_url(
        storage_path, expires_in, options
    )
    signed_url = data["signedUrl"]
    assert signed_url is not None, "Supabase returned no signedUrl for a successful create_signed_url call"
    return signed_url
