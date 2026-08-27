from __future__ import annotations

import uuid
from typing import Optional

from storage3.types import URLOptions

from .config import settings
from .supabase_client import supabase_client


def upload_file(
    case_id: Optional[str], filename: str, file_bytes: bytes, content_type: str
) -> str:
    """Uploads the file and returns its storage path."""
    storage_folder = case_id or "unassigned"
    storage_path = f"{storage_folder}/{uuid.uuid4()}_{filename}"
    supabase_client.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
        storage_path, file_bytes, {"content-type": content_type}
    )
    return storage_path


def delete_file(storage_path: str) -> None:
    supabase_client.storage.from_(settings.SUPABASE_STORAGE_BUCKET).remove([storage_path])


def create_signed_url(
    storage_path: str,
    expires_in: int,
    download_filename: Optional[str] = None,
) -> str:
    options: URLOptions = {}
    if download_filename:
        options["download"] = download_filename
    data = supabase_client.storage.from_(settings.SUPABASE_STORAGE_BUCKET).create_signed_url(
        storage_path, expires_in, options
    )
    return data["signedUrl"]
