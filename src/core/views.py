# core/views.py
"""Core views."""
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.contrib.auth.decorators import user_passes_test
import requests as http_requests
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils.dateparse import parse_datetime
from core.models import ErrorLog
import requests as http_requests  # avoid clash with django.http request
from django.conf import settings
from core.models import UserLLMConfig
from core.llm_model_registry import MODEL_REGISTRY, VRAM_TIERS, get_model_info
from logging import getLogger
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import (
    TemplateView, View
)

logger = getLogger(__name__)

def health_check(request):
    """Health check endpoint."""
    return JsonResponse({"status": "healthy", "service": "castor"})


@login_required
def home_view(request):
    """Home page - redirect to projects."""
    return redirect("projects:list")


def test_error(request):
    """Test view to trigger an error - REMOVE IN PRODUCTION"""
    raise ValueError("This is a test error to verify error logging works! \n #### Hasta la vista Baby!!!! ####")

@user_passes_test(lambda u: u.is_staff)
def loader_gallery(request):
    """
    A gallery to preview all Castor Loader variants.
    Only accessible by Staff.
    """
    return render(request, 'loaders/loader_gallery.html')

def test_landing_page(request):
    """Test view to trigger an error - REMOVE IN PRODUCTION"""
    return render(request, 'registration/login-matrix.html')


@require_POST
@staff_member_required
def send_errors_to_supabase(request):
    """Send all unsent ErrorLog entries to Supabase."""

    if not settings.SUPABASE_URL or not settings.SUPABASE_PUBLISHABLE_KEY:
        return JsonResponse(
            {"success": False, "error": "Supabase not configured"},
            status=500,
        )

    unsent = ErrorLog.objects.filter(sent_to_supabase=False)
    count = unsent.count()

    if count == 0:
        return JsonResponse({"success": True, "sent": 0, "message": "No new errors to send"})

    # Serialize to match the Supabase table schema
    payload = []
    for err in unsent:
        payload.append({
            "developer_name": request.user.username,
            "severity": err.severity,
            "message": err.message,
            "exception_type": err.exception_type,
            "stacktrace": err.stacktrace,
            "url": err.url,
            "method": err.method,
            "view_name": err.view_name,
            "username": err.user.username if err.user else "",
            "user_agent": err.user_agent,
            "ip_address": err.ip_address,
            "request_data": err.request_data,
            "is_resolved": err.is_resolved,
            "resolution_note": err.resolution_note,
            "original_created_at": err.created_at.isoformat() if err.created_at else None,
            "original_updated_at": err.updated_at.isoformat() if err.updated_at else None,
        })

    # POST to Supabase REST API
    supabase_endpoint = f"{settings.SUPABASE_URL}/rest/v1/error_logs"
    headers = {
        "apikey": settings.SUPABASE_PUBLISHABLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_PUBLISHABLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    try:
        resp = http_requests.post(supabase_endpoint, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
    except http_requests.RequestException as e:
        return JsonResponse(
            {"success": False, "error": f"Supabase request failed: {str(e)}"},
            status=502,
        )

    # Mark as sent only after successful upload
    unsent.update(sent_to_supabase=True)

    return JsonResponse({"success": True, "sent": count})

@require_POST
@staff_member_required
def pull_errors_from_supabase(request):
    """Pull error logs from Supabase that weren't generated locally."""

    if not settings.SUPABASE_URL or not settings.SUPABASE_PUBLISHABLE_KEY:
        return JsonResponse(
            {"success": False, "error": "Supabase not configured"},
            status=500,
        )

    supabase_endpoint = f"{settings.SUPABASE_URL}/rest/v1/error_logs"
    headers = {
        "apikey": settings.SUPABASE_PUBLISHABLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_PUBLISHABLE_KEY}",
    }

    # Fetch all errors from Supabase, newest first
    params = {
        "order": "uploaded_at.desc",
        "limit": 500,
    }

    try:
        resp = http_requests.get(
            supabase_endpoint, headers=headers, params=params, timeout=15,
        )
        resp.raise_for_status()
        remote_errors = resp.json()
    except http_requests.RequestException as e:
        return JsonResponse(
            {"success": False, "error": f"Supabase request failed: {str(e)}"},
            status=502,
        )

    if not remote_errors:
        return JsonResponse({"success": True, "imported": 0, "skipped": 0, "message": "No errors on Supabase"})

    # Get all supabase_ids we already have locally to skip duplicates
    existing_supabase_ids = set(
        ErrorLog.objects.filter(supabase_id__isnull=False)
        .values_list("supabase_id", flat=True)
    )

    # Also skip errors uploaded by THIS developer (they're already local)
    current_developer = request.user.username

    imported = 0
    skipped = 0

    for entry in remote_errors:
        supabase_id = entry.get("id")

        # Skip if already imported
        if supabase_id and __import__("uuid").UUID(supabase_id) in existing_supabase_ids:
            skipped += 1
            continue

        # Skip own errors — they already exist locally
        if entry.get("developer_name") == current_developer:
            skipped += 1
            continue

        # Try to resolve user FK locally, fallback to string
        remote_username = entry.get("username", "")
        local_user = None
        if remote_username:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            local_user = User.objects.filter(username=remote_username).first()

        # Safety net: get_or_create prevents duplicates even under race conditions
        _, created = ErrorLog.objects.get_or_create(
            supabase_id=supabase_id,
            defaults={
                "severity": entry.get("severity", "error"),
                "message": entry.get("message", ""),
                "exception_type": entry.get("exception_type", ""),
                "stacktrace": entry.get("stacktrace", ""),
                "url": entry.get("url", ""),
                "method": entry.get("method", ""),
                "view_name": entry.get("view_name", ""),
                "user": local_user,
                "original_username": remote_username or entry.get("developer_name", ""),
                "user_agent": entry.get("user_agent", ""),
                "ip_address": entry.get("ip_address") or None,
                "request_data": entry.get("request_data", {}),
                "is_resolved": entry.get("is_resolved", False),
                "resolution_note": entry.get("resolution_note", ""),
                "sent_to_supabase": True,
            },
        )
        imported += 1

    return JsonResponse({
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "message": f"Imported {imported} error(s), skipped {skipped}",
    })

class SettingsView(LoginRequiredMixin, TemplateView):
    """System-wide application settings."""

    template_name = "core/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        config = UserLLMConfig.load(self.request.user)
        active_model = config.active_model or settings.OLLAMA_MODEL
        active_info = get_model_info(active_model)

        context["active_model"] = active_model
        context["active_model_info"] = active_info
        context["default_model"] = settings.OLLAMA_MODEL
        context["is_using_default"] = not config.active_model
        context["embed_model"] = settings.OLLAMA_EMBED_MODEL
        context["embed_dimensions"] = settings.PGVECTOR_DIMENSIONS
        context["ollama_host"] = settings.OLLAMA_HOST
        context["vram_tiers"] = VRAM_TIERS

        return context

class OllamaModelsAPIView(LoginRequiredMixin, View):
    """HTMX endpoint: fetch available Ollama models, return HTML partial."""

    def get(self, request):
        config = UserLLMConfig.load(request.user)
        active_model = config.active_model or settings.OLLAMA_MODEL

        # Query Ollama for locally pulled models
        try:
            resp = http_requests.get(
                f"{settings.OLLAMA_HOST}/api/tags",
                timeout=5,
            )
            resp.raise_for_status()
            ollama_models = resp.json().get("models", [])
        except Exception as e:
            logger.warning("Failed to reach Ollama at %s: %s", settings.OLLAMA_HOST, e)
            return render(request, "core/components/model_selector.html", {
                "error": "Cannot reach Ollama. Is it running?",
                "installed_by_tier": {},
                "recommended_by_tier": {},
                "active_model": active_model,
                "default_model": settings.OLLAMA_MODEL,
            })

        # Collect pulled model tags
        pulled_tags = set()
        installed = []
        for m in ollama_models:
            tag = m.get("name", "")
            if "embed" in tag.lower():
                continue

            pulled_tags.add(tag)
            info = get_model_info(tag)
            installed.append({
                "tag": tag,
                "size_gb": round(m.get("size", 0) / (1024**3), 1),
                "known": info is not None,
                "label": info.label if info else tag,
                "family": info.family if info else "unknown",
                "tier": info.tier if info else "unknown",
                "vram_gb": info.vram_gb if info else None,
                "description": info.description if info else "Not in Castor registry",
                "is_moe": info.is_moe if info else False,
                "supports_thinking": info.supports_thinking if info else False,
            })

        # Build recommended list: registry models NOT yet pulled
        recommended = []
        for tag, info in MODEL_REGISTRY.items():
            if tag not in pulled_tags:
                recommended.append({
                    "tag": tag,
                    "label": info.label,
                    "family": info.family,
                    "tier": info.tier,
                    "vram_gb": info.vram_gb,
                    "description": info.description,
                    "is_moe": info.is_moe,
                    "supports_thinking": info.supports_thinking,
                })

        # Group both lists by tier
        installed_by_tier = self._group_by_tier(installed)
        recommended_by_tier = self._group_by_tier(recommended)

        return render(request, "core/components/model_selector.html", {
            "installed_by_tier": installed_by_tier,
            "recommended_by_tier": recommended_by_tier,
            "active_model": active_model,
            "default_model": settings.OLLAMA_MODEL,
            "error": None,
        })

    @staticmethod
    def _group_by_tier(models_list):
        """Group a list of model dicts by their VRAM tier label."""
        grouped = {}
        # Preserve tier ordering from VRAM_TIERS
        tier_order = list(VRAM_TIERS.keys())

        for m in models_list:
            raw_tier = m.get("tier", "unknown")
            tier_meta = VRAM_TIERS.get(raw_tier, {"label": raw_tier.title()})
            tier_label = tier_meta.get("label", raw_tier)
            grouped.setdefault(tier_label, []).append(m)

        # Sort by tier order (lite → workstation)
        ordered = {}
        for key in tier_order:
            label = VRAM_TIERS[key]["label"]
            if label in grouped:
                ordered[label] = grouped[label]
        # Append any unknown tiers at the end
        for label, models in grouped.items():
            if label not in ordered:
                ordered[label] = models

        return ordered

class SetModelAPIView(LoginRequiredMixin, View):
    """HTMX endpoint: save the user's model choice."""

    def post(self, request):
        tag = request.POST.get("model_tag", "").strip()

        config = UserLLMConfig.load(request.user)

        if tag == settings.OLLAMA_MODEL or not tag:
            # Reset to default
            config.active_model = ""
            config.save()
            label = f"{settings.OLLAMA_MODEL} (default)"
        else:
            config.active_model = tag
            config.save()
            info = get_model_info(tag)
            label = info.label if info else tag

        logger.info("LLM model changed to: %s", config.active_model or "(default)")

        # Return updated status fragment for HTMX swap
        return render(request, "core/components/model_status.html", {
            "active_model": config.active_model or settings.OLLAMA_MODEL,
            "active_model_info": get_model_info(config.active_model or settings.OLLAMA_MODEL),
            "is_using_default": not config.active_model,
            "just_saved": True,
        })


