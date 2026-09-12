"""
Generate documents for a range (or explicit list) of cases in one go.

Usage (from backend/):
    python -m scripts.run_batch C001 C008        # every case from C001 to C008 inclusive
    python -m scripts.run_batch C001 C003 C007    # only these cases
"""

from __future__ import annotations

import re
import sys

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


def main() -> None:
    args = sys.argv[1:]
    if len(args) == 2:
        case_ids = expand_range(args[0], args[1])
    elif len(args) >= 1:
        case_ids = args
    else:
        print("Usage: python -m scripts.run_batch <START_CASE> <END_CASE>")
        print("       python -m scripts.run_batch <CASE_ID> [<CASE_ID> ...]")
        sys.exit(1)

    print(f"Generating {len(case_ids)} case(s): {', '.join(case_ids)}")
    failures = []
    for case_id in case_ids:
        print(f"\n=== {case_id} ===")
        try:
            generate_for_case(case_id)
        except Exception as e:
            print(f"Case {case_id} failed: {e}")
            failures.append(case_id)

    if failures:
        print(f"\nDone with failures: {', '.join(failures)}")
        sys.exit(1)
    print("\nAll cases generated successfully.")


if __name__ == "__main__":
    main()
