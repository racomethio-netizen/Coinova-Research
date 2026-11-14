from __future__ import annotations

import pytest
from nexus_security import CredentialCipher, DecryptionError, KeyRing, generate_key_entry
from nexus_security.crypto import key_fingerprint

CTX = {"user_id": "1", "exchange": "binance", "credential_id": "7", "field": "api_secret"}


@pytest.fixture
def cipher() -> CredentialCipher:
    return CredentialCipher(KeyRing.parse(generate_key_entry("k1")))


def test_roundtrip(cipher: CredentialCipher) -> None:
    blob = cipher.encrypt("my-secret", context=CTX)
    assert b"my-secret" not in blob
    assert cipher.decrypt(blob, context=CTX) == "my-secret"


def test_nonce_is_random(cipher: CredentialCipher) -> None:
    assert cipher.encrypt("x", context=CTX) != cipher.encrypt("x", context=CTX)


@pytest.mark.parametrize("change", [{"field": "api_key"}, {"user_id": "2"},
                                    {"exchange": "kraken"}, {"credential_id": "8"}])
def test_context_binding(cipher: CredentialCipher, change: dict[str, str]) -> None:
    blob = cipher.encrypt("s", context=CTX)
    with pytest.raises(DecryptionError):
        cipher.decrypt(blob, context={**CTX, **change})


def test_tamper_detected(cipher: CredentialCipher) -> None:
    blob = bytearray(cipher.encrypt("secret", context=CTX))
    blob[-1] ^= 0x01
    with pytest.raises(DecryptionError):
        cipher.decrypt(bytes(blob), context=CTX)


def test_wrong_key(cipher: CredentialCipher) -> None:
    other = CredentialCipher(KeyRing.parse(generate_key_entry("k1")))
    with pytest.raises(DecryptionError):
        other.decrypt(cipher.encrypt("s", context=CTX), context=CTX)


def test_rotation() -> None:
    old_entry, new_entry = generate_key_entry("old"), generate_key_entry("new")
    old = CredentialCipher(KeyRing.parse(old_entry))
    blob = old.encrypt("s", context=CTX)
    rotated = CredentialCipher(KeyRing.parse(f"{new_entry},{old_entry}"))
    assert rotated.decrypt(blob, context=CTX) == "s"
    assert rotated.needs_rotation(blob)
    assert not rotated.needs_rotation(rotated.encrypt("s", context=CTX))
    # Removing the old key makes old ciphertexts undecryptable (explicit failure).
    with pytest.raises(DecryptionError, match="unknown key id"):
        CredentialCipher(KeyRing.parse(new_entry)).decrypt(blob, context=CTX)


@pytest.mark.parametrize("spec", ["", "nokey", "k1:short", "bad id:" + "A" * 43,
                                  "k1:" + "A" * 43 + ",k1:" + "B" * 43])
def test_invalid_key_ring(spec: str) -> None:
    with pytest.raises(ValueError):
        KeyRing.parse(spec)


def test_malformed_blob(cipher: CredentialCipher) -> None:
    for blob in (b"", b"garbage", b"v2:k1:" + b"0" * 40, b"v1:k1:short"):
        with pytest.raises(DecryptionError):
            cipher.decrypt(blob, context=CTX)


def test_key_material_not_in_repr() -> None:
    entry = generate_key_entry("k1")
    ring = KeyRing.parse(entry)
    assert entry.split(":")[1] not in repr(ring)
    assert entry.split(":")[1] not in repr(CredentialCipher(ring))


def test_context_required(cipher: CredentialCipher) -> None:
    with pytest.raises(ValueError):
        cipher.encrypt("s", context={})


def test_fingerprint_is_not_reversible_prefix() -> None:
    fp = key_fingerprint("ABCDEFGHIJKLMNOP")
    assert len(fp) == 12 and "ABCD" not in fp
