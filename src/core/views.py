# core/views.py
"""Core views."""

import json
from logging import getLogger

import requests as http_requests
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.cache import cache
from django.db import connections
from django.db.utils import OperationalError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render, resolve_url
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, View

from core.exceptions import get_client_ip
from core.forms import LoginPasswordForm, LoginUsernameForm
from core.http import toast_response, trigger_toast
from core.llm_model_registry import (
    MODEL_REGISTRY,
    VRAM_TIERS,
    get_model_info,
)
from core.models import ErrorLog, SiteLaunchConfig, UserLLMConfig
from core.services.auth_service import (
    clear_login_stage,
    complete_login_attempt,
    get_staged_username,
    stage_login_attempt,
)
from core.services.team_notes import (
    create_note,
    push_notes_to_supabase,
)
from core.services.team_notes import (
    pull_notes_from_supabase as svc_pull_notes,
)
from core.token_budget import get_context_window

logger = getLogger(__name__)


def _fmt_ctx(tokens: int) -> str:
    """Format a context window token count as a compact label, e.g. '8k', '32k'."""
    return f"{tokens // 1024}k" if tokens >= 1024 else str(tokens)


def health_check(request):
    """Liveness + readiness probe.

    Returns 200 only when both the database and Ollama are reachable. nginx,
    uptime monitors, and the M6 pre-flight checklist consume this — a stale-200
    that doesn't actually probe dependencies is worse than no probe.
    """
    db_status = "ok"
    ollama_status = "ok"

    try:
        connections["default"].cursor().execute("SELECT 1")
    except OperationalError as exc:
        db_status = "down"
        logger.warning("healthz db check failed: %s", exc)

    try:
        resp = http_requests.get(f"{settings.OLLAMA_HOST}/api/tags", timeout=2)
        if resp.status_code != 200:
            ollama_status = "down"
    except http_requests.RequestException as exc:
        ollama_status = "down"
        logger.warning("healthz ollama check failed: %s", exc)

    healthy = db_status == "ok" and ollama_status == "ok"
    payload = {
        "status": "healthy" if healthy else "degraded",
        "service": "castor",
        "db": db_status,
        "ollama": ollama_status,
    }
    return JsonResponse(payload, status=200 if healthy else 503)


def home_view(request):
    """Root URL.

    Authenticated users always land on the project list. For unauthenticated
    visitors, ``SiteLaunchConfig`` decides what is shown:

    - ``live`` → the real beta landing page with the application form.
    - ``coming_soon`` → pre-launch splash with the matrix-rain effect.
    - ``maintenance`` → same splash with a different headline.

    The toggle is flippable from /admin/core/sitelaunchconfig/ — no deploy.
    There is intentionally no self-service signup — the only path in is the
    application form, which is itself disabled in the non-live states.
    """
    if request.user.is_authenticated:
        return redirect("projects:list")
    config = SiteLaunchConfig.load()
    if config.is_live:
        return render(request, "core/landing.html")
    mode = "maintenance" if config.state == SiteLaunchConfig.State.MAINTENANCE else "coming_soon"
    return render(request, "core/coming_soon.html", {"mode": mode})


def test_error(request):
    """Test view to trigger an error - REMOVE IN PRODUCTION"""
    raise ValueError(
        "This is a test error to verify error logging works! \n #### Hasta la vista Baby!!!! ####"
    )


@user_passes_test(lambda u: u.is_staff)
def loader_gallery(request):
    """
    A gallery to preview all Castor Loader variants.
    Only accessible by Staff.
    """
    return render(request, "loaders/loader_gallery.html")


def test_landing_page(request):
    """Test view to trigger an error - REMOVE IN PRODUCTION"""
    return render(request, "registration/login-matrix.html")


@csrf_exempt
@require_POST
def log_ws_client_error(request):
    """Browser-side WebSocket-error beacon → ``ErrorLog``.

    Called by page JS via ``navigator.sendBeacon`` when the WS layer reports
    a problem the server has no other way to see — typically a 1006 abnormal
    close (network drop, Daphne crash) or an ``onerror`` event. Lands one
    ``ErrorLog`` row at ``severity="warning"`` so the operator's main feed
    surfaces client-side WS faults alongside the server-side ones.

    Per-user rate limit (1 row / 60 s) prevents an unhealthy client from
    looping the endpoint into a thousand rows. The browser doesn't get to
    see the throttle — every successful POST returns 204.

    CSRF is intentionally exempted: ``sendBeacon`` cannot attach the CSRF
    token. The session cookie still authenticates the request, and the
    payload is treated as untrusted (truncated, no fields are rendered).
    """
    if not request.user.is_authenticated:
        return HttpResponse(status=401)

    throttle_key = f"ws_error_beacon:{request.user.pk}"
    if cache.get(throttle_key):
        return HttpResponse(status=204)
    cache.set(throttle_key, 1, timeout=60)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    code = str(payload.get("code", ""))[:32]
    reason = str(payload.get("reason", ""))[:255]
    page_url = str(payload.get("url", ""))[:500]
    ws_path = str(payload.get("ws_path", ""))[:500]

    ErrorLog.objects.create(
        severity="warning",
        message=f"WS client beacon: code={code or '?'} reason={reason or '(none)'}",
        exception_type="WebSocketClientBeacon",
        stacktrace=(
            f"WS path: {ws_path}\nPage URL: {page_url}\nClose code: {code}\nReason: {reason}"
        ),
        url=page_url,
        method="WS",
        view_name="core.views.log_ws_client_error",
        user=request.user,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        ip_address=get_client_ip(request),
        request_data={
            "ws_path": ws_path,
            "close_code": code,
            "reason": reason,
            "page_url": page_url,
        },
    )
    return HttpResponse(status=204)


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
        payload.append(
            {
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
            }
        )

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
            supabase_endpoint,
            headers=headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        remote_errors = resp.json()
    except http_requests.RequestException as e:
        return JsonResponse(
            {"success": False, "error": f"Supabase request failed: {str(e)}"},
            status=502,
        )

    if not remote_errors:
        return JsonResponse(
            {"success": True, "imported": 0, "skipped": 0, "message": "No errors on Supabase"}
        )

    # Get all supabase_ids we already have locally to skip duplicates
    existing_supabase_ids = set(
        ErrorLog.objects.filter(supabase_id__isnull=False).values_list("supabase_id", flat=True)
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

            User = get_user_model()  #  noqa: N806
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

    return JsonResponse(
        {
            "success": True,
            "imported": imported,
            "skipped": skipped,
            "message": f"Imported {imported} error(s), skipped {skipped}",
        }
    )


class SettingsView(LoginRequiredMixin, TemplateView):
    """User account preferences, plus a staff-only system-administration block.

    Regular users see theme + read-only account info, with placeholders for
    BYOK / RAG tuning / notifications (all v1.1+). Staff users additionally
    see the Ollama model picker, embedding-model display, and Ollama endpoint
    status — the original local-setup admin surface, gated by ``is_staff`` in
    the template. Theme is provided by the global ``user_theme`` context
    processor.
    """

    template_name = "core/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # BYOK panel context (every authenticated user).
        from core.views_byok import build_byok_context

        context.update(build_byok_context(self.request.user))

        if self.request.user.is_staff:
            config = UserLLMConfig.load(self.request.user)
            active_model = config.active_model or settings.OLLAMA_MODEL
            context["active_model"] = active_model
            context["active_model_info"] = get_model_info(active_model)
            context["default_model"] = settings.OLLAMA_MODEL
            context["is_using_default"] = not config.active_model
            context["embed_model"] = settings.OLLAMA_EMBED_MODEL
            context["embed_dimensions"] = settings.PGVECTOR_DIMENSIONS
            context["ollama_host"] = settings.OLLAMA_HOST
            context["vram_tiers"] = VRAM_TIERS

        return context


class OllamaModelsAPIView(LoginRequiredMixin, UserPassesTestMixin, View):
    """HTMX endpoint: fetch available Ollama models, return HTML partial.

    Staff-only — the model picker is part of the system-administration surface
    on the Settings page. Non-staff users get a 403 to keep the cloud-LLM
    posture clean during the beta.
    """

    def test_func(self):
        return self.request.user.is_staff

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
            return render(
                request,
                "core/components/model_selector.html",
                {
                    "error": "Cannot reach Ollama. Is it running?",
                    "installed_by_tier": {},
                    "recommended_by_tier": {},
                    "active_model": active_model,
                    "default_model": settings.OLLAMA_MODEL,
                },
            )

        # Collect pulled model tags
        pulled_tags = set()
        installed = []
        for m in ollama_models:
            tag = m.get("name", "")
            if "embed" in tag.lower():
                continue

            pulled_tags.add(tag)
            info = get_model_info(tag)
            installed.append(
                {
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
                    "context_window_label": _fmt_ctx(get_context_window(tag)),
                }
            )

        # Build recommended list: registry models NOT yet pulled
        recommended = []
        for tag, info in MODEL_REGISTRY.items():
            if tag not in pulled_tags:
                recommended.append(
                    {
                        "tag": tag,
                        "label": info.label,
                        "family": info.family,
                        "tier": info.tier,
                        "vram_gb": info.vram_gb,
                        "description": info.description,
                        "is_moe": info.is_moe,
                        "supports_thinking": info.supports_thinking,
                        "context_window_label": _fmt_ctx(info.context_window_size),
                    }
                )

        # Group both lists by tier
        installed_by_tier = self._group_by_tier(installed)
        recommended_by_tier = self._group_by_tier(recommended)

        return render(
            request,
            "core/components/model_selector.html",
            {
                "installed_by_tier": installed_by_tier,
                "recommended_by_tier": recommended_by_tier,
                "active_model": active_model,
                "default_model": settings.OLLAMA_MODEL,
                "error": None,
            },
        )

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


class SetModelAPIView(LoginRequiredMixin, UserPassesTestMixin, View):
    """HTMX endpoint: save the user's model choice. Staff-only (see
    ``OllamaModelsAPIView`` for the same gate)."""

    def test_func(self):
        return self.request.user.is_staff

    def post(self, request):
        tag = request.POST.get("model_tag", "").strip()

        config = UserLLMConfig.load(request.user)

        if tag == settings.OLLAMA_MODEL or not tag:
            # Reset to default
            config.active_model = ""
            config.save()
        else:
            config.active_model = tag
            config.save()
        logger.info("LLM model changed to: %s", config.active_model or "(default)")

        active = config.active_model or settings.OLLAMA_MODEL
        info = get_model_info(active)
        label = (info.label if info else active) or active
        response = render(
            request,
            "core/components/model_status.html",
            {
                "active_model": active,
                "active_model_info": info,
                "is_using_default": not config.active_model,
                "just_saved": True,
            },
        )
        return trigger_toast(response, f"Active model: {label}")


class SetThemeAPIView(LoginRequiredMixin, View):
    """HTMX endpoint: persist the user's theme choice."""

    VALID = {"dark", "light"}

    def post(self, request):
        theme = request.POST.get("theme", "").strip().lower()
        if theme not in self.VALID:
            return toast_response("Invalid theme", level="error", status=400)

        config = UserLLMConfig.load(request.user)
        config.theme = theme
        config.save(update_fields=["theme", "updated_at"])
        logger.info("Theme changed to: %s", theme)

        response = render(
            request,
            "core/components/theme_toggle.html",
            {"user_theme": theme},
        )
        # Fire `castor:theme-changed` so the live-flip listener in base.html can
        # update <html data-bs-theme> and the cookie without a full reload.
        existing = response.get("HX-Trigger")
        payload = json.loads(existing) if existing else {}
        payload["castor:theme-changed"] = {"theme": theme}
        response["HX-Trigger"] = json.dumps(payload)
        return trigger_toast(response, f"Theme: {theme.title()}")


@require_POST
@login_required
def create_team_note(request):
    """Create a new TeamNote from the modal form."""
    import json

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    title = data.get("title", "").strip()
    body = data.get("body", "").strip()
    category = data.get("category", "note")
    priority = data.get("priority", "medium")

    if not title or not body:
        return JsonResponse({"success": False, "error": "Title and body are required"}, status=400)

    note = create_note(
        author_username=request.user.username,
        title=title,
        body=body,
        category=category,
        priority=priority,
        page_url=data.get("page_url", ""),
        browser_info=data.get("browser_info"),
    )

    response = JsonResponse({"success": True, "id": str(note.id)})
    return trigger_toast(response, "Team note saved")


@require_POST
@staff_member_required
def send_notes_to_supabase(request):
    """Push unsent TeamNotes to Supabase."""
    try:
        result = push_notes_to_supabase(developer_name=request.user.username)
        return JsonResponse({"success": True, **result})
    except ValueError as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)
    except http_requests.RequestException as e:
        return JsonResponse(
            {"success": False, "error": f"Supabase request failed: {e}"},
            status=502,
        )


@require_POST
@staff_member_required
def pull_notes_from_supabase(request):
    """Pull TeamNotes from Supabase."""
    try:
        result = svc_pull_notes(current_developer=request.user.username)
        return JsonResponse({"success": True, **result})
    except ValueError as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)
    except http_requests.RequestException as e:
        return JsonResponse(
            {"success": False, "error": f"Supabase request failed: {e}"},
            status=502,
        )


# ---------------------------------------------------------------------------
# Public unauthenticated surfaces: two-step login, privacy, terms
# ---------------------------------------------------------------------------


def login_page_view(request):
    """GET /login/ — render the full login template.

    The page hosts the matrix background plus a swappable form region; initial
    render shows step 1 (username). Authenticated users skip the page entirely.
    """
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    clear_login_stage(request)
    return render(
        request,
        "registration/login.html",
        {"form": LoginUsernameForm()},
    )


@require_POST
def login_step1_view(request):
    """POST /login/step1/ — stage username, render step 2 partial.

    Anti-enumeration: this endpoint NEVER queries the user table. Any well-formed
    submission proceeds to step 2, regardless of whether the username exists.
    """
    form = LoginUsernameForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "registration/_login_step1.html",
            {"form": form},
            status=400,
        )

    username = form.cleaned_data["username"]
    stage_login_attempt(request, username)
    return render(
        request,
        "registration/_login_step2.html",
        {"form": LoginPasswordForm(), "username": username},
    )


def login_step1_reset_view(request):
    """GET /login/step1/ — return the step 1 partial (used by 'use a different account')."""
    clear_login_stage(request)
    return render(
        request,
        "registration/_login_step1.html",
        {"form": LoginUsernameForm()},
    )


@require_POST
def login_step2_view(request):
    """POST /login/step2/ — authenticate the staged username + this password.

    Success: HX-Redirect header pointing at LOGIN_REDIRECT_URL.
    Failure (any cause): re-render step 1 + generic toast. Never reveals which
    field caused the failure.
    """
    staged = get_staged_username(request)
    if not staged:
        # Stage expired or missing — bounce back to step 1 with a hint.
        response = render(
            request,
            "registration/_login_step1.html",
            {"form": LoginUsernameForm()},
        )
        return trigger_toast(response, "Session expired — start over", level="info")

    form = LoginPasswordForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "registration/_login_step2.html",
            {"form": form, "username": staged},
            status=400,
        )

    user = complete_login_attempt(request, form.cleaned_data["password"])
    if user is None:
        # Single, generic error path — wrong password, unknown user, inactive
        # account all collapse to the same message.
        clear_login_stage(request)
        response = render(
            request,
            "registration/_login_step1.html",
            {"form": LoginUsernameForm()},
        )
        return trigger_toast(response, "Invalid credentials", level="error")

    # Success — tell HTMX to do a full-page redirect to the post-login landing.
    # ``LOGIN_REDIRECT_URL`` may be a URL name (e.g. "projects:list"); resolve
    # it before handing it to HTMX so the browser navigates to a real path.
    response = HttpResponse(status=204)
    response["HX-Redirect"] = resolve_url(settings.LOGIN_REDIRECT_URL)
    return response


def privacy_view(request):
    """Public privacy / data-handling notice — short, transparent, no legal entity."""
    return render(request, "core/privacy.html")


def terms_view(request):
    """Public terms-of-use notice for the beta — short, plain language."""
    return render(request, "core/terms.html")


# ---------------------------------------------------------------------------
# Custom error handlers (400, 403, 404, 500)
#
# Wired into the root URLconf via `handlerXXX = "core.views.error_NNN_view"`.
# Django only invokes these when DEBUG=False; in DEBUG=True the technical
# error page is shown instead. Use ``preview_error_view`` below to design /
# inspect the templates without flipping DEBUG.
#
# The templates intentionally do NOT extend core/base.html — they are
# standalone HTML so they keep working even when context processors or
# middleware are part of what just broke.
# ---------------------------------------------------------------------------


def error_400_view(request, exception=None):
    """Bad Request handler — malformed request the framework rejected."""
    return render(request, "errors/400.html", status=400)


def error_403_view(request, exception=None):
    """Forbidden handler — user lacks permission, or middleware rejected the request."""
    return render(request, "errors/403.html", status=403)


def error_404_view(request, exception=None):
    """Not Found handler — URL didn't match a route, or get_object_or_404 missed."""
    return render(request, "errors/404.html", status=404)


def error_500_view(request):
    """Internal Server Error handler — uncaught exception in a view.

    Note the signature: handler500 takes no ``exception`` argument, unlike
    400/403/404. The template renders with a bare context, so it must not
    depend on the request user, context processors, or DB state.
    """
    return render(request, "errors/500.html", status=500)


def preview_error_view(request, code: int):
    """DEBUG-only: render an error template at an explicit URL.

    Lets us iterate on the design without flipping DEBUG=False, since
    Django's handlerXXX only fire for real errors when DEBUG is off.
    Returns 404 outside of DEBUG so the route can't be probed in production.
    """
    if not settings.DEBUG:
        from django.http import Http404

        raise Http404()

    template_by_code = {400: "400", 403: "403", 404: "404", 500: "500"}
    name = template_by_code.get(code)
    if name is None:
        from django.http import Http404

        raise Http404()
    return render(request, f"errors/{name}.html", status=code)
