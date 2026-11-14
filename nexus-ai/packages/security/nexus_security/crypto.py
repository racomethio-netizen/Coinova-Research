"""Authenticated encryption for exchange API credentials at rest.

* AES-256-GCM with a random 96-bit nonce per encryption.
* Ciphertext format: ``b"v1:" + key_id + b":" + nonce(12) + ciphertext||tag``.
* Associated data binds each ciphertext to its owner, exchange, credential row and
  field name, so a ciphertext copied into another row/field fails to decrypt.
* A key ring supports rotation: the first key encrypts; every key can decrypt.

Master keys come from ``NEXUS_CREDENTIAL_ENCRYPTION_KEYS`` (never from the DB).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"v1"
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class DecryptionError(Exception):
    """Raised for any failure to decrypt. Never includes key or plaintext material."""


@dataclass(frozen=True)
class KeyRing:
    primary_id: str
    keys: dict[str, bytes]

    def __repr__(self) -> str:  # never print key bytes
        return f"KeyRing(primary_id={self.primary_id!r}, key_ids={sorted(self.keys)})"

    @classmethod
    def parse(cls, spec: str) -> KeyRing:
        """Parse ``"id1:base64key,id2:base64key"``; the first entry is primary."""
        keys: dict[str, bytes] = {}
        primary: str | None = None
        for raw in spec.split(","):
            entry = raw.strip()
            if not entry:
                continue
            key_id, sep, encoded = entry.partition(":")
            if not sep or not _KEY_ID_RE.match(key_id):
                raise ValueError("invalid key ring entry (expected '<id>:<base64>')")
            try:
                key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"key {key_id!r} is not valid base64") from exc
            if len(key) != 32:
                raise ValueError(f"key {key_id!r} must decode to 32 bytes")
            if key_id in keys:
                raise ValueError(f"duplicate key id {key_id!r}")
            keys[key_id] = key
            primary = primary or key_id
        if primary is None:
            raise ValueError("key ring is empty")
        return cls(primary_id=primary, keys=keys)


def generate_key_entry(key_id: str) -> str:
    if not _KEY_ID_RE.match(key_id):
        raise ValueError("invalid key id")
    return f"{key_id}:{base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('=')}"


def key_fingerprint(api_key: str) -> str:
    """Short non-reversible identifier for showing which key is stored (sha256 prefix)."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:12]


def _aad(context: dict[str, str]) -> bytes:
    # Canonical, unambiguous encoding of the binding context.
    return b"\x1f".join(f"{k}={context[k]}".encode() for k in sorted(context))


class CredentialCipher:
    def __init__(self, ring: KeyRing) -> None:
        self._ring = ring

    def __repr__(self) -> str:
        return f"CredentialCipher({self._ring!r})"

    def encrypt(self, plaintext: str, *, context: dict[str, str]) -> bytes:
        if not context:
            raise ValueError("encryption context is required")
        key_id = self._ring.primary_id
        nonce = os.urandom(12)
        ct = AESGCM(self._ring.keys[key_id]).encrypt(nonce, plaintext.encode(), _aad(context))
        return _VERSION + b":" + key_id.encode() + b":" + nonce + ct

    def decrypt(self, blob: bytes, *, context: dict[str, str]) -> str:
        try:
            version, key_id_b, rest = blob.split(b":", 2)
        except ValueError as exc:
            raise DecryptionError("malformed ciphertext") from exc
        if not hmac.compare_digest(version, _VERSION):
            raise DecryptionError("unsupported ciphertext version")
        key = self._ring.keys.get(key_id_b.decode(errors="replace"))
        if key is None:
            raise DecryptionError("unknown key id (was it removed from the key ring?)")
        if len(rest) < 12 + 16:
            raise DecryptionError("ciphertext too short")
        try:
            return AESGCM(key).decrypt(rest[:12], rest[12:], _aad(context)).decode()
        except InvalidTag as exc:
            raise DecryptionError("authentication failed (wrong key or context)") from exc

    def key_id_of(self, blob: bytes) -> str:
        return blob.split(b":", 2)[1].decode()

    def needs_rotation(self, blob: bytes) -> bool:
        return self.key_id_of(blob) != self._ring.primary_id
