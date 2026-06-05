# core/tests/test_crypto.py
"""Tests for core.crypto — Fernet round-trip, masking, missing-key behaviour."""

from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet

from core import crypto


@pytest.fixture(autouse=True)
def _reset_crypto_cache():
    """Clear the Fernet cache between tests so settings overrides land."""
    crypto._reset_cache()
    yield
    crypto._reset_cache()


@pytest.fixture
def explicit_key(settings):
    """Set FIELD_ENCRYPTION_KEY to a freshly generated Fernet key."""
    key = Fernet.generate_key().decode("ascii")
    settings.FIELD_ENCRYPTION_KEY = key
    return key


def test_encrypt_decrypt_roundtrip(explicit_key):
    """A non-empty plaintext encrypts and decrypts to itself."""
    plaintext = "sk-ant-api03-AbCdEf123456_some_long_real_looking_key"
    ciphertext = crypto.encrypt(plaintext)
    assert ciphertext  # non-empty
    assert ciphertext != plaintext  # actually encrypted
    assert crypto.decrypt(ciphertext) == plaintext


def test_encrypt_empty_returns_empty(explicit_key):
    """Empty plaintext short-circuits to empty ciphertext (no Fernet call)."""
    assert crypto.encrypt("") == ""


def test_decrypt_empty_returns_none(explicit_key):
    """Empty ciphertext decrypts to None — caller treats as 'no key stored'."""
    assert crypto.decrypt("") is None


def test_decrypt_garbage_returns_none_not_raises(explicit_key):
    """Unreadable ciphertext returns None rather than propagating InvalidToken."""
    assert crypto.decrypt("not-a-real-fernet-token") is None


def test_decrypt_with_wrong_key_returns_none(settings):
    """Ciphertext encrypted under key A can't be decrypted under key B."""
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    ciphertext = crypto.encrypt("hello-world-key-123456")

    # Rotate to a different key.
    crypto._reset_cache()
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    assert crypto.decrypt(ciphertext) is None


def test_missing_key_in_debug_derives_from_secret_key(settings, caplog):
    """In DEBUG mode, an unset FIELD_ENCRYPTION_KEY is derived from SECRET_KEY (with warning)."""
    settings.DEBUG = True
    settings.FIELD_ENCRYPTION_KEY = ""
    settings.SECRET_KEY = "dev-secret-key-for-this-test-only-1234567890"

    plaintext = "groq-api-key-1234567890"
    ciphertext = crypto.encrypt(plaintext)
    assert crypto.decrypt(ciphertext) == plaintext

    # Same derivation should be deterministic.
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    )
    f = Fernet(expected)
    assert f.decrypt(ciphertext.encode("ascii")).decode("utf-8") == plaintext


def test_missing_key_in_production_raises(settings):
    """In production (DEBUG=False), FIELD_ENCRYPTION_KEY is required."""
    settings.DEBUG = False
    settings.FIELD_ENCRYPTION_KEY = ""

    with pytest.raises(crypto.EncryptionKeyMissingError):
        crypto.encrypt("any-secret")


def test_invalid_fernet_key_raises_descriptive_error(settings):
    """A FIELD_ENCRYPTION_KEY that isn't a valid Fernet key fails loudly."""
    settings.FIELD_ENCRYPTION_KEY = "not-a-valid-fernet-key"

    with pytest.raises(crypto.EncryptionKeyMissingError) as excinfo:
        crypto.encrypt("anything")
    assert "Fernet" in str(excinfo.value)


# ── mask_key ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "plaintext,expected",
    [
        ("sk-ant-api03-AbCdEf123456_blah_blah_blah", "sk-ant-…_blah"),
        ("gsk_abcdef1234567890ABCDEFGHIJ", "gsk_abc…FGHIJ"),
    ],
)
def test_mask_key_keeps_first7_last5(plaintext, expected):
    """mask_key shows first 7 + ellipsis + last 5 for long inputs."""
    assert crypto.mask_key(plaintext) == expected


def test_mask_key_short_input_is_fully_masked():
    """Short plaintexts are fully masked — never echo a short token in full."""
    assert crypto.mask_key("short") == "…" * 8
    assert crypto.mask_key("twelvechars1") == "…" * 8


def test_mask_key_empty_returns_empty():
    """Empty input returns empty (signals 'no key set' to the UI)."""
    assert crypto.mask_key("") == ""
