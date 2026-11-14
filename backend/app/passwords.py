"""
Officer passwords are stored only as salted scrypt hashes (AUTH_USERS), never as plain text. A leaked setting then
gives an attacker a slow hash per account to crack, not the passwords. Standard library only.

Stored form: scrypt:<n>:<r>:<p>:<salt>:<hash>, salt and hash in base64url without padding, so a value never holds
the ; , $ or # that AUTH_USERS and .env files treat specially. The cost is stored with each hash, so it can be raised
later without breaking older entries. Make one with `python -m scripts.hash_password` (in backend/).

A leaf module: config.py imports it, so it imports nothing from the app.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets

_PREFIX = "scrypt"
# OWASP's equivalent scrypt settings include N=2^14, r=8, p=5: about 16 MB per login, so concurrent logins stay
# well inside the api's memory.
_N, _R, _P = 2**14, 8, 5
_KEY_BYTES = 32
_SALT_BYTES = 16
_MAX_MEMORY = 64 * 1024 * 1024


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _derive(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_MAX_MEMORY, dklen=length)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _derive(password, salt, _N, _R, _P, _KEY_BYTES)
    return f"{_PREFIX}:{_N}:{_R}:{_P}:{_encode(salt)}:{_encode(key)}"


def _parsed(stored: str) -> tuple[int, int, int, bytes, bytes] | None:
    parts = stored.split(":")
    if len(parts) != 6 or parts[0] != _PREFIX:
        return None
    try:
        n, r, p = (int(x) for x in parts[1:4])
        salt, key = _decode(parts[4]), _decode(parts[5])
    except (ValueError, binascii.Error):
        return None
    # n must be a power of two above 1; the rest only needs to be sane.
    if n < 2 or n & (n - 1) or not 0 < r <= 64 or not 0 < p <= 64 or len(salt) < 8 or len(key) < 16:
        return None
    if 128 * r * n > _MAX_MEMORY:
        return None
    return n, r, p, salt, key


def is_hash(stored: str) -> bool:
    return _parsed(stored) is not None


def verify(password: str, stored: str) -> bool:
    """Whether the password matches the stored hash, compared in constant time. A malformed hash never matches."""
    parsed = _parsed(stored)
    if parsed is None:
        return False
    n, r, p, salt, key = parsed
    return hmac.compare_digest(_derive(password, salt, n, r, p, len(key)), key)


# Checked when no account has the name given, so a wrong name takes as long as a wrong password.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
