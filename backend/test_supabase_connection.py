from __future__ import annotations

import argparse
import mimetypes
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client


def build_client() -> Client:
    load_dotenv()

    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")

    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is not set")
    if not supabase_key:
        raise RuntimeError("SUPABASE_PUBLISHABLE_KEY is not set")

    return create_client(supabase_url, supabase_key)


def guess_content_type(file_path: Path) -> str:
    content_type, _ = mimetypes.guess_type(str(file_path))
    return content_type or "application/octet-stream"


def upload_file(client: Client, bucket: str, case_id: str | None, file_path: Path) -> str:
    storage_folder = case_id or "unassigned"
    storage_path = f"{storage_folder}/{uuid.uuid4()}_{file_path.name}"
    file_bytes = file_path.read_bytes()
    content_type = guess_content_type(file_path)

    client.storage.from_(bucket).upload(
        storage_path,
        file_bytes,
        {"content-type": content_type},
    )

    return storage_path


def insert_document_row(client: Client, case_id: str | None, filename: str, storage_path: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": str(uuid.uuid4()),
        "case_id": case_id,
        "filename": filename,
        "storage_path": storage_path,
        "status": "uploaded",
        "retry_count": 0,
        "uploaded_at": now,
    }

    result = client.table("documents").insert(row).execute()
    if not result.data:
        raise RuntimeError("Insert returned no data")

    return result.data[0]


def update_document_row(client: Client, document_id: str) -> dict:
    result = (
        client.table("documents")
        .update({"status": "queued"})
        .eq("id", document_id)
        .execute()
    )

    if not result.data:
        raise RuntimeError("Update returned no data")

    return result.data[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test Supabase storage upload plus documents table insert/update."
    )
    parser.add_argument("file_path", type=Path, help="Path to a local file to upload")
    parser.add_argument(
        "--case-id",
        default=None,
        help="Optional cases.id value to use in storage path and row",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("SUPABASE_STORAGE_BUCKET", "documents"),
        help="Supabase storage bucket name",
    )
    args = parser.parse_args()

    if not args.file_path.exists():
        raise FileNotFoundError(f"File not found: {args.file_path}")

    client = build_client()

    print(f"Uploading {args.file_path} to bucket {args.bucket}...")
    storage_path = upload_file(client, args.bucket, args.case_id, args.file_path)
    print(f"Upload ok: {storage_path}")

    print("Inserting document row...")
    document = insert_document_row(client, args.case_id, args.file_path.name, storage_path)
    print(f"Insert ok: {document['id']}")

    print("Updating document row to queued...")
    updated = update_document_row(client, document["id"])
    print(f"Update ok: {updated['id']} -> {updated['status']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())