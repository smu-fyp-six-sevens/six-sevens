"""
Rewrites the regression fixtures the Playwright suite's mocked backend serves (tests/fixtures/regression/*.json)
from what the backend produces now, then runs the same tests again to confirm each one matches.

    cd backend
    python scripts/regenerate_regression_fixtures.py

The backend tests only compare (tests/support/regression_fixtures.py); this is the one place that writes. Review the
diff of tests/fixtures/regression/ before committing: a changed fixture is a changed answer from the backend. It
runs only the fixture tests, which stub Claude and never touch Supabase.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FIXTURE_TESTS = [
    "tests/test_regression_audit_trail.py",
    "tests/test_regression_case_match.py",
    "tests/test_regression_case_risk.py",
    "tests/test_regression_case_views.py",
    "tests/test_regression_checklist.py",
    "tests/test_regression_detection.py",
    "tests/test_regression_detection_metrics.py",
    "tests/test_regression_fixtures.py",
]
# The comparing tests are named for what they check: the Playwright suite's data, or the mocked backend's.
SELECT = "playwright_suite or mocked_backend"


def pytest_run(*, regenerate: bool) -> int:
    env = {**os.environ, "REGENERATE_REGRESSION_FIXTURES": "1"} if regenerate else {
        key: value for key, value in os.environ.items() if key != "REGENERATE_REGRESSION_FIXTURES"
    }
    command = [sys.executable, "-m", "pytest", *FIXTURE_TESTS, "-k", SELECT, "-q", "-p", "no:cacheprovider"]
    return subprocess.run(command, cwd=BACKEND, env=env, check=False).returncode


def main() -> int:
    print("Rewriting the fixtures...")
    if pytest_run(regenerate=True) not in (0,):
        print("The fixture tests failed while rewriting; nothing was verified.")
        return 1
    print("Comparing them against the backend again...")
    if pytest_run(regenerate=False) != 0:
        print("The rewritten fixtures still don't match.")
        return 1
    print("Done. Review `git diff tests/fixtures/regression` before committing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
