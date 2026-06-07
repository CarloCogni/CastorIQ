# core/views_byok.py
"""
HTMX endpoints for the Bring-Your-Own-Key (BYOK) settings panel.

Each endpoint validates input, calls into ``core.services.byok_service`` for
the mutation, and returns the rendered ``byok_panel.html`` partial so HTMX
can swap the whole panel back in place. Every successful mutation fires a
``castor:toast`` via ``trigger_toast()`` per project convention; validation
failures return a 400 with an error toast.

URL patterns (registered in ``core/urls.py``):
    settings/api/byok/save-key/<provider>/
    settings/api/byok/remove-key/<provider>/
    settings/api/byok/set-provider/<purpose>/
    settings/api/byok/set-model/<provider>/

All views are login-required and CSRF-protected (Django default). No
rate-limiting beyond that: the endpoints only mutate the caller's own row,
so a user spamming them can only annoy themselves. The shape matches the
existing ``SetThemeAPIView`` / ``SetModelAPIView`` pattern in ``core/views.py``.
"""

from __future__ import annotations

import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.views import View

from core import llm_catalog
from core.http import toast_response, trigger_toast
from core.models import UserLLMConfig
from core.services.byok_service import (
    BYOKValidationError,
    remove_key,
    save_key,
    set_model,
    set_provider_override,
)

logger = logging.getLogger(__name__)


def build_byok_context(user) -> dict:
    """Render the context dict the ``byok_panel.html`` partial expects.

    Centralised so both the SettingsView (full-page render) and the HTMX
    endpoints (partial swap) produce identical context shape.

    The "Local Ollama" provider choice is filtered out of the dropdown unless
    ``SiteLLMConfig.expose_ollama_to_users`` is on — the cloud-hosted beta
    can't usefully expose it (user's browser can't reach their own Ollama),
    and self-hosted operators flip the flag on once.
    """
    from core.models import SiteLLMConfig

    cfg = UserLLMConfig.load(user)
    expose_ollama = SiteLLMConfig.load().expose_ollama_to_users
    provider_choices = [
        (value, label)
        for value, label in UserLLMConfig.ProviderOverride.choices
        if value != "ollama" or expose_ollama
    ]
    return {
        "byok_config": cfg,
        "byok_has_anthropic": bool(cfg.anthropic_api_key_ciphertext),
        "byok_has_groq": bool(cfg.groq_api_key_ciphertext),
        "byok_anthropic_hint": cfg.anthropic_api_key_hint,
        "byok_groq_hint": cfg.groq_api_key_hint,
        "byok_ask_override": cfg.ask_provider_override,
        "byok_modify_override": cfg.modify_provider_override,
        "byok_anthropic_model": cfg.anthropic_model or llm_catalog.default_model_for("anthropic"),
        "byok_groq_model": cfg.groq_model or llm_catalog.default_model_for("groq"),
        "byok_anthropic_models": llm_catalog.ANTHROPIC_MODELS,
        "byok_groq_models": llm_catalog.GROQ_MODELS,
        "byok_provider_choices": provider_choices,
        "byok_anthropic_pricing_url": llm_catalog.PROVIDER_PRICING_URL["anthropic"],
        "byok_groq_pricing_url": llm_catalog.PROVIDER_PRICING_URL["groq"],
    }


def _render_panel(request, *, toast: str | None = None, level: str = "success"):
    """Render the byok_panel partial, optionally with a toast trigger."""
    response = render(
        request,
        "core/components/byok_panel.html",
        build_byok_context(request.user),
    )
    if toast:
        trigger_toast(response, toast, level=level)
    return response


class SaveKeyView(LoginRequiredMixin, View):
    """POST settings/api/byok/save-key/<provider>/ — encrypt & store a key."""

    def post(self, request, provider: str):
        plaintext = request.POST.get("api_key", "")
        try:
            save_key(request.user, provider, plaintext)
        except BYOKValidationError as e:
            return toast_response(str(e), level="error", status=400)
        return _render_panel(request, toast=f"{provider.title()} key saved")


class RemoveKeyView(LoginRequiredMixin, View):
    """POST settings/api/byok/remove-key/<provider>/ — wipe a stored key."""

    def post(self, request, provider: str):
        try:
            remove_key(request.user, provider)
        except BYOKValidationError as e:
            return toast_response(str(e), level="error", status=400)
        return _render_panel(request, toast=f"{provider.title()} key removed")


class SetProviderView(LoginRequiredMixin, View):
    """POST settings/api/byok/set-provider/<purpose>/ — switch routing for a purpose."""

    def post(self, request, purpose: str):
        provider = request.POST.get("provider", "").strip()
        try:
            set_provider_override(request.user, purpose, provider)
        except BYOKValidationError as e:
            return toast_response(str(e), level="error", status=400)
        label = (
            dict(UserLLMConfig.ProviderOverride.choices).get(provider, provider) or "site default"
        )
        return _render_panel(request, toast=f"{purpose.title()} routing → {label}")


class SetModelView(LoginRequiredMixin, View):
    """POST settings/api/byok/set-model/<provider>/ — choose a curated model."""

    def post(self, request, provider: str):
        identifier = request.POST.get("model", "").strip()
        try:
            set_model(request.user, provider, identifier)
        except BYOKValidationError as e:
            return toast_response(str(e), level="error", status=400)
        return _render_panel(request, toast=f"{provider.title()} model saved")
