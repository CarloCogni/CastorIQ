# core/services/byok_service.py
"""
BYOK (Bring Your Own Key) service layer.

Encapsulates every mutation on ``UserLLMConfig`` BYOK fields. Views call
into here; per CLAUDE.md "views stay dumb" and `.claude/skills/django-service.md`,
business logic lives at this layer.

Invariants enforced:
- A user can only set ``{ask,modify}_provider_override`` to a cloud provider
  when they have a stored ciphertext for that provider.
- ``remove_key`` atomically resets ``{ask,modify}_provider_override`` if it
  pointed at the provider being removed (no "override=anthropic, key=blank"
  half-state).
- Model identifiers are validated against ``core.llm_catalog``.

Errors are raised as ``BYOKValidationError`` so views can surface a toast.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from core import llm_catalog
from core.crypto import encrypt, mask_key
from core.models import UserLLMConfig

logger = logging.getLogger(__name__)

CLOUD_PROVIDERS = ("anthropic", "groq")
PURPOSES = ("ask", "modify")


class BYOKValidationError(ValueError):
    """Raised when a BYOK mutation is rejected by validation."""


def _provider_key_fields(provider: str) -> tuple[str, str]:
    """Return (ciphertext_field, hint_field) names for a provider."""
    if provider == "anthropic":
        return ("anthropic_api_key_ciphertext", "anthropic_api_key_hint")
    if provider == "groq":
        return ("groq_api_key_ciphertext", "groq_api_key_hint")
    raise BYOKValidationError(f"Unknown provider: {provider!r}")


def save_key(user, provider: str, plaintext: str) -> UserLLMConfig:
    """Encrypt and store a user's API key for a cloud provider.

    A non-empty plaintext shorter than 8 characters is rejected — it's
    almost certainly a paste error rather than a real key.
    """
    plaintext = (plaintext or "").strip()
    if not plaintext:
        raise BYOKValidationError("API key is empty.")
    if len(plaintext) < 16:
        raise BYOKValidationError("That doesn't look like a complete API key.")
    if provider not in CLOUD_PROVIDERS:
        raise BYOKValidationError(f"Unknown provider: {provider!r}")

    cipher_field, hint_field = _provider_key_fields(provider)
    config = UserLLMConfig.load(user)
    setattr(config, cipher_field, encrypt(plaintext))
    setattr(config, hint_field, mask_key(plaintext))
    config.keys_updated_at = timezone.now()
    config.save(
        update_fields=[cipher_field, hint_field, "keys_updated_at", "updated_at"],
    )
    logger.info("BYOK key saved: user=%s provider=%s", user.pk, provider)
    return config


@transaction.atomic
def remove_key(user, provider: str) -> UserLLMConfig:
    """Wipe a stored key AND reset any provider override that pointed at it.

    Atomic to prevent the half-state where override=anthropic but ciphertext
    is empty. Defensive resolution in ``core.llm`` falls back to site default
    if that race ever lands, but the right fix is to clear it here.
    """
    if provider not in CLOUD_PROVIDERS:
        raise BYOKValidationError(f"Unknown provider: {provider!r}")

    cipher_field, hint_field = _provider_key_fields(provider)
    config = UserLLMConfig.load(user)
    setattr(config, cipher_field, "")
    setattr(config, hint_field, "")
    # If either override pointed at this provider, demote it to site default.
    update_fields = [cipher_field, hint_field, "keys_updated_at", "updated_at"]
    if config.ask_provider_override == provider:
        config.ask_provider_override = ""
        update_fields.append("ask_provider_override")
    if config.modify_provider_override == provider:
        config.modify_provider_override = ""
        update_fields.append("modify_provider_override")
    config.keys_updated_at = timezone.now()
    config.save(update_fields=update_fields)
    logger.info("BYOK key removed: user=%s provider=%s", user.pk, provider)
    return config


def set_provider_override(user, purpose: str, provider: str) -> UserLLMConfig:
    """Set the per-purpose provider override.

    ``provider`` is one of ``""`` (site default), ``"ollama"``, ``"anthropic"``,
    or ``"groq"``. Cloud values require a stored key for that provider.
    """
    if purpose not in PURPOSES:
        raise BYOKValidationError(f"Unknown purpose: {purpose!r}")
    valid_values = {choice for choice, _ in UserLLMConfig.ProviderOverride.choices}
    if provider not in valid_values:
        raise BYOKValidationError(f"Unknown provider override: {provider!r}")

    config = UserLLMConfig.load(user)
    if provider in CLOUD_PROVIDERS and not config.has_byok_key(provider):
        raise BYOKValidationError(
            f"Add your {provider.title()} API key first, then choose it as a provider."
        )

    field = f"{purpose}_provider_override"
    setattr(config, field, provider)
    config.save(update_fields=[field, "updated_at"])
    logger.info(
        "BYOK provider override: user=%s purpose=%s provider=%s",
        user.pk,
        purpose,
        provider or "(site default)",
    )
    return config


def set_model(user, provider: str, identifier: str) -> UserLLMConfig:
    """Save the user's curated model choice for a cloud provider."""
    if provider not in CLOUD_PROVIDERS:
        raise BYOKValidationError(f"Unknown provider: {provider!r}")
    if identifier and not llm_catalog.is_valid_model(provider, identifier):
        raise BYOKValidationError(f"Unknown {provider.title()} model: {identifier!r}")

    field = f"{provider}_model"
    config = UserLLMConfig.load(user)
    setattr(config, field, identifier)
    config.save(update_fields=[field, "updated_at"])
    logger.info(
        "BYOK model set: user=%s provider=%s model=%s",
        user.pk,
        provider,
        identifier or "(catalog default)",
    )
    return config
