"""Unit tests for the key-loading and hashing helpers."""

import base64

import pytest

from app.security import (
    _decode_pem,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)

PEM = "-----BEGIN PUBLIC KEY-----\nabc\n-----END PUBLIC KEY-----"


def test_decode_pem_accepts_raw():
    assert _decode_pem(PEM, "X") == PEM


def test_decode_pem_accepts_base64():
    encoded = base64.b64encode(PEM.encode()).decode()
    assert _decode_pem(encoded, "X") == PEM


def test_decode_pem_rejects_garbage():
    with pytest.raises(RuntimeError, match="neither a PEM"):
        _decode_pem("!!! not base64 !!!", "X")


def test_decode_pem_rejects_base64_that_isnt_a_pem():
    encoded = base64.b64encode(b"hello world").decode()
    with pytest.raises(RuntimeError, match="isn't a PEM"):
        _decode_pem(encoded, "X")


def test_password_round_trip():
    hashed = hash_password("correct horse battery")
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong", hashed)


def test_verify_password_survives_a_malformed_hash():
    """A corrupt row must be a failed login, not a 500."""
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_refresh_tokens_are_unique_and_hashed():
    plain_a, hash_a = new_refresh_token()
    plain_b, _ = new_refresh_token()
    assert plain_a != plain_b
    assert hash_a == hash_refresh_token(plain_a)
    assert plain_a not in hash_a
