"""
Makes one AUTH_USERS entry: prompts for a password (twice, not echoed) and prints `<name>:<hash>`. Join several
with ";" for AUTH_USERS in backend/.env and the auth-users secret. From backend/:

    python -m scripts.hash_password alice
"""

from __future__ import annotations

import getpass
import sys

from app.passwords import hash_password


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip() or any(c in sys.argv[1] for c in ":;,$# "):
        print("Usage: python -m scripts.hash_password <username>  (no spaces or : ; , $ #)", file=sys.stderr)
        return 2
    password = getpass.getpass("Password: ")
    if not password:
        print("An empty password isn't allowed.", file=sys.stderr)
        return 1
    if getpass.getpass("Again: ") != password:
        print("The two passwords differ.", file=sys.stderr)
        return 1
    print(f"{sys.argv[1].strip()}:{hash_password(password)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
