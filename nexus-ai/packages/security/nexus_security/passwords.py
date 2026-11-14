"""Password hashing with scrypt (stdlib, memory-hard). Format:
``scrypt$<log2 n>$<r>$<p>$<salt b64>$<hash b64>``."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_LOG_N, _R, _P, _DKLEN = 15, 8, 1, 32
MIN_PASSWORD_LENGTH = 12


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password.strip() != password or len(set(password)) < 6:
        raise ValueError("password is too weak")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**_LOG_N, r=_R, p=_P,
                            dklen=_DKLEN, maxmem=128 * 1024 * 1024)
    return f"scrypt${_LOG_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, log_n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64),
                                n=2 ** int(log_n), r=int(r), p=int(p), dklen=len(expected),
                                maxmem=128 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def needs_rehash(encoded: str) -> bool:
    parts = encoded.split("$")
    return len(parts) != 6 or parts[:4] != ["scrypt", str(_LOG_N), str(_R), str(_P)]


# A valid hash of a random password, used to equalize timing for unknown users.
DUMMY_HASH = hash_password(_b64(os.urandom(18)))
