from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

__test__ = False  # Prevent pytest from trying to run this file as a test

def build_client() -> Client:
    load_dotenv()

    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")

    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is not set")
    if not supabase_key:
        raise RuntimeError("SUPABASE_PUBLISHABLE_KEY is not set")

    return create_client(supabase_url, supabase_key)


def test_db_connection(client: Client) -> None:
    # Perform a read-only operation to verify database connectivity.
    # Selecting 1 row from the 'documents' table confirms the DB is reachable.
    client.table("documents").select("id").limit(1).execute()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test Supabase database connection (Read-Only)."
    )
    # Kept to prevent your GitHub Action command from breaking, but the file is not used.
    parser.add_argument("file_path", type=Path, nargs='?', help="Path to a local file (Ignored)")
    parser.add_argument(
        "--case-id",
        default=None,
        help="Optional cases.id (Ignored in read-only test)",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("SUPABASE_STORAGE_BUCKET", "documents"),
        help="Supabase storage bucket name (Ignored in read-only test)",
    )
    args = parser.parse_args()

    try:
        client = build_client()
        print("Testing Supabase database connection...")
        
        test_db_connection(client)
        
        print("Connection OK: Successfully communicated with the database.")
    except Exception as e:
        print(f"Smoke test failed: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())