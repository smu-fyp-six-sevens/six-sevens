"""
Generate documents for a range (or explicit list) of cases in one go, optionally
in parallel across worker processes.

Usage (from backend/):
    python -m scripts.run_batch C001 C008              # every case from C001 to C008 inclusive
    python -m scripts.run_batch C001 C003 C007          # only these cases
    python -m scripts.run_batch C001 C066 --jobs 4      # 4 worker processes instead of the default
    python -m scripts.run_batch C001 C066 --jobs 1      # sequential (same as the old behaviour)

Each case is fully self-contained (its own output folder, its own seeded RNGs), so
running several at once in separate processes is safe -- see generate_for_case().
Uses a process pool rather than threads because the work itself (ReportLab canvas
drawing, PDF/image manipulation) is CPU-bound Python, which threads can't actually
parallelize past the GIL.

Defaults to half the machine's logical cores (minimum 1) rather than all of them --
this is meant to run alongside whatever else you're using the laptop for, not to
grab every core. Pass --jobs to override in either direction.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

from scripts.generate_from_mastersheet import generate_for_case

CASE_ID_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def expand_range(start: str, end: str) -> list[str]:
    start_m, end_m = CASE_ID_RE.match(start), CASE_ID_RE.match(end)
    if not start_m or not end_m or start_m.group(1) != end_m.group(1):
        raise ValueError(f"Cannot form a range from {start!r} and {end!r}")
    prefix = start_m.group(1)
    width = len(start_m.group(2))
    lo, hi = int(start_m.group(2)), int(end_m.group(2))
    return [f"{prefix}{n:0{width}d}" for n in range(lo, hi + 1)]


def default_jobs() -> int:
    cores = os.cpu_count() or 2
    return max(1, cores // 2)


def _run_one(case_id: str) -> tuple[str, bool, str, str]:
    """Runs in a worker process. Captures generate_for_case()'s own print() output
    into a string instead of letting it write straight to stdout, so parallel
    workers' output doesn't interleave line-by-line in the parent's terminal --
    each case's block gets printed whole, as soon as it finishes."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            generate_for_case(case_id)
        return case_id, True, buf.getvalue(), ""
    except Exception as e:
        return case_id, False, buf.getvalue(), str(e)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate documents for a range or explicit list of cases.",
    )
    parser.add_argument("cases", nargs="+", help="START END (a range) or one or more explicit case IDs")
    parser.add_argument(
        "--jobs", "-j", type=int, default=None,
        help=f"Worker processes to run in parallel (default: {default_jobs()}, half the machine's cores). "
             "Use --jobs 1 for the old sequential behaviour.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if len(args.cases) == 2:
        case_ids = expand_range(args.cases[0], args.cases[1])
    else:
        case_ids = args.cases

    jobs = args.jobs if args.jobs is not None else default_jobs()
    jobs = max(1, min(jobs, len(case_ids)))

    print(f"Generating {len(case_ids)} case(s) with {jobs} worker(s): {', '.join(case_ids)}")
    failures = []

    if jobs == 1:
        for case_id in case_ids:
            print(f"\n=== {case_id} ===")
            try:
                generate_for_case(case_id)
            except Exception as e:
                print(f"Case {case_id} failed: {e}")
                failures.append(case_id)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_run_one, case_id): case_id for case_id in case_ids}
            for future in as_completed(futures):
                case_id, ok, log, error = future.result()
                print(f"\n=== {case_id} ===")
                print(log, end="")
                if not ok:
                    print(f"Case {case_id} failed: {error}")
                    failures.append(case_id)

    if failures:
        print(f"\nDone with failures: {', '.join(sorted(failures))}")
        sys.exit(1)
    print("\nAll cases generated successfully.")


if __name__ == "__main__":
    main()
