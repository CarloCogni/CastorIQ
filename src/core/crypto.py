# core/crypto.py
"""
Symmetric encryption for at-rest user secrets (BYOK API keys).

Uses Fernet (cryptography library) keyed by ``settings.FIELD_ENCRYPTION_KEY``.
The key is a 32-byte url-safe base64 string generated once with
``Fernet.generate_key()`` and stored in ``.env`` on each environment.

Resolution order:
    1. ``settings.FIELD_ENCRYPTION_KEY`` (production must set this).
    2. If absent and ``settings.DEBUG`` is True, derive a key from
       ``SECRET_KEY`` (dev convenience — logs a warning).
    3. Otherwise raise at first call. Production must not run without an
       explicit key.

Plaintext API keys are never logged. ``mask_key()`` derives a display-safe
form for the settings UI (first 7 + last 5 characters).
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


class EncryptionKeyMissingError(RuntimeError):
    """Raised when an encryption operation runs without a configured key."""


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """Construct the Fernet instance from settings.FIELD_ENCRYPTION_KEY.

    Cached so we don't re-derive on every call. The cache is process-local;
    settings changes during tests are handled by clearing via _reset_cache().
    """
    raw = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or ""
    if raw:
        try:
            return Fernet(raw.encode("ascii") if isinstance(raw, str) else raw)
        except (ValueError, TypeError) as exc:
            raise EncryptionKeyMissingError(
                "FIELD_ENCRYPTION_KEY is set but not a valid Fernet key. "
                "Generate one with: python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            ) from exc

    if getattr(settings, "DEBUG", False):
        logger.warning(
            "FIELD_ENCRYPTION_KEY is unset; deriving a dev key from SECRET_KEY. "
            "Production MUST set FIELD_ENCRYPTION_KEY explicitly."
        )
        digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        derived = base64.urlsafe_b64encode(digest)
        return Fernet(derived)

    raise EncryptionKeyMissingError(
        "FIELD_ENCRYPTION_KEY is required in production. "
        "Generate one with: python -c 'from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())' and add it to your environment."
    )


def _reset_cache() -> None:
    """Clear the Fernet cache. For tests only."""
    _get_fernet.cache_clear()


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string. Empty input returns empty string."""
    if not plaintext:
        return ""
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt(ciphertext: str) -> str | None:
    """Decrypt a ciphertext string. Returns None on any decryption failure.

    Returning None (rather than raising) lets callers distinguish "no key
    set" from "key rotated and old ciphertext can no longer be read" without
    leaking the failure to the response.
    """
    if not ciphertext:
        return None
    try:
        plaintext = _get_fernet().decrypt(ciphertext.encode("ascii"))
        return plaintext.decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        logger.warning("decrypt() failed — stored ciphertext is unreadable with the current key")
        return None
    except EncryptionKeyMissingError:
        raise


def mask_key(plaintext: str) -> str:
    """Render an API key safely for UI display: first 7 + last 5 characters.

    Examples:
        sk-ant-api03-AbCdEf...12345 → sk-ant-…12345
        gsk_abcdef0123456789        → gsk_abc…56789

    Returns at most 16 characters (7 + ellipsis + 5). Shorter inputs return
    a fully-masked placeholder so we never accidentally echo a short key in
    full.
    """
    if not plaintext:
        return ""
    if len(plaintext) <= 12:
        return "…" * 8
    return f"{plaintext[:7]}…{plaintext[-5:]}"
