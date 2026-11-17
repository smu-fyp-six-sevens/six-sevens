"""
Disaster-recovery drill (HOW_IT_WORKS.md §9, "Disaster recovery"): dump the Supabase database, restore it into a
throwaway local Postgres, and check every table came back, timed. Reads production only (pg_dump); writes nothing
there. Needs Docker. Run from backend/, with the database's connection string from Supabase (Connect -> Session
pooler) in SUPABASE_DB_URL, never in a file:

    set SUPABASE_DB_URL=postgresql://postgres.<ref>:<password>@<host>:5432/postgres
    python scripts/dr_drill.py            # --keep leaves the restored database running on localhost:55432

Prints the timings (the measured recovery time) and each table's rows before and after.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

_CONTAINER = "dr-drill-postgres"
_IMAGE = "postgres:17"
_PORT = "55432"
_DUMP = "/tmp/six-sevens.dump"


def _run(args: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, env={**os.environ, **(env or {})}, capture_output=True, text=True, check=check)


def _in_container(command: str, *, source: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    # The connection string goes in as an environment variable, so it never shows in a process list or the output.
    env_args = ["-e", "SRC"] if source else []
    done = _run(["docker", "exec", *env_args, _CONTAINER, "sh", "-c", command],
                env={"SRC": source} if source else None, check=False)
    if check and done.returncode != 0:
        error = done.stderr.strip()
        if source:
            error = error.replace(source, "<SUPABASE_DB_URL>")
        raise SystemExit(f"Failed ({done.returncode}): {error or done.stdout.strip()}")
    return done


def _counts(where: str, source: str | None) -> dict[str, int]:
    target = '"$SRC"' if source else "-U postgres -d postgres"
    tables = _in_container(
        f"psql {target} -At -c \"select tablename from pg_tables where schemaname = 'public' order by 1\"",
        source=source,
    ).stdout.split()
    counts: dict[str, int] = {}
    for table in tables:
        out = _in_container(f"psql {target} -At -c 'select count(*) from public.\"{table}\"'", source=source)
        counts[table] = int(out.stdout.strip())
    print(f"  {where}: {len(counts)} tables, {sum(counts.values())} rows")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep", action="store_true", help="leave the restored database running")
    args = parser.parse_args()
    source = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not source:
        print("Set SUPABASE_DB_URL first (Supabase -> Connect -> Session pooler).", file=sys.stderr)
        return 2

    _run(["docker", "rm", "-f", _CONTAINER], check=False)
    print("Starting a local Postgres to restore into...")
    _run(["docker", "run", "-d", "--name", _CONTAINER, "-e", "POSTGRES_PASSWORD=drill", "-p", f"{_PORT}:5432", _IMAGE])
    for _ in range(60):
        if _in_container("pg_isready -U postgres", check=False).returncode == 0:
            break
        time.sleep(1)

    try:
        print("Rows in production:")
        before = _counts("production", source)

        started = time.monotonic()
        # The app's tables only: Supabase's own schemas (auth, storage, realtime) are restored by Supabase itself.
        _in_container(f'pg_dump "$SRC" --schema=public --no-owner --no-privileges -Fc -f {_DUMP}', source=source)
        dumped = time.monotonic()
        size = _in_container(f"du -h {_DUMP} | cut -f1").stdout.strip()
        restore = _in_container(
            f"pg_restore -U postgres -d postgres --no-owner --no-privileges {_DUMP}", check=False,
        )
        restored = time.monotonic()

        print("Rows after the restore:")
        after = _counts("restored", None)
        # Counted before the dump: a table written to meanwhile (audit_events) may come back with more, never fewer.
        missing = {t: (n, after.get(t)) for t, n in before.items() if (after.get(t) or 0) < n}

        print()
        print(f"Dump:    {dumped - started:6.1f} s ({size})")
        print(f"Restore: {restored - dumped:6.1f} s")
        print(f"Total:   {restored - started:6.1f} s  <- the measured recovery time for the database")
        if restore.returncode != 0:
            # Supabase-only objects (its roles, extensions in other schemas) can't exist in plain Postgres.
            warnings = [line for line in restore.stderr.splitlines() if line.strip()]
            print(f"pg_restore reported {len(warnings)} lines; the first few:")
            for line in warnings[:8]:
                print(f"  {line}")
        if missing:
            print("Tables that differ (production, restored):")
            for table, pair in missing.items():
                print(f"  {table}: {pair}")
            return 1
        print(f"Every table came back with the same rows ({len(before)} tables).")
        return 0
    finally:
        if args.keep:
            print(f"Restored database left running: postgresql://postgres:drill@localhost:{_PORT}/postgres")
        else:
            _run(["docker", "rm", "-f", _CONTAINER], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
