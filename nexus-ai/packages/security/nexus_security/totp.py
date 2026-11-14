"""RFC 6238 TOTP (SHA-1, 6 digits, 30 s) using only the standard library."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import quote


def new_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def verify(secret: str, code: str, *, at: float | None = None, window: int = 1) -> int | None:
    """Return the matched time step (for replay protection) or None."""
    if not code.isdigit() or len(code) != 6:
        return None
    step = int((at if at is not None else time.time()) // 30)
    for delta in range(-window, window + 1):
        if hmac.compare_digest(_code(secret, step + delta), code):
            return step + delta
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "NEXUS AI") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30")
