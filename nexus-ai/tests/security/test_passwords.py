from __future__ import annotations

import pytest
from nexus_security.passwords import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)


def test_hash_and_verify() -> None:
    h = hash_password("correct horse battery")
    assert h.startswith("scrypt$15$8$1$")
    assert verify_password("correct horse battery", h)
    assert not verify_password("correct horse batterY", h)
    assert hash_password("correct horse battery") != h  # salted
    assert not needs_rehash(h)


@pytest.mark.parametrize("bad", ["", "garbage", "bcrypt$x", "scrypt$15$8$1$!!$!!"])
def test_malformed_hash_never_verifies(bad: str) -> None:
    assert verify_password("anything", bad) is False


@pytest.mark.parametrize("pw", ["short", "aaaaaaaaaaaaaaaa", " leading-space-pass"])
def test_weak_passwords_rejected(pw: str) -> None:
    with pytest.raises(ValueError):
        validate_password_strength(pw)
