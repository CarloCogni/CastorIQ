# core/context_processors.py
"""
Global template context processors.

Registered in settings.TEMPLATES — injected into every template render.
"""

from django.conf import settings


def _routing_badge(choice) -> dict:
    """Translate a ResolvedLLM into a small dict the routing-badge partial reads."""
    if choice.provider == "ollama":
        return {
            "label": "Local Ollama",
            "detail": choice.model,
            "tone": "ollama",
            "byok": False,
        }
    suffix = "your key" if choice.byok else "shared pool"
    provider_label = "Anthropic" if choice.provider == "anthropic" else "Groq"
    return {
        "label": f"{provider_label} ({suffix})",
        "detail": choice.model,
        "tone": "byok" if choice.byok else "shared",
        "byok": choice.byok,
    }


def llm_context(request):
    """
    Inject the current user's active LLM model, UI theme, and per-purpose
    routing badge data into every template.

    Available in templates as:
        {{ active_llm_model }}  — the Ollama tag string
        {{ active_llm_info }}   — ModelInfo dataclass (or None)
        {{ user_theme }}        — "dark" | "light" (always present)
        {{ llm_routing.ask }}   — dict with label/detail/tone/byok for Ask
        {{ llm_routing.modify }} — same for Modify (rendered by
                                   ``core/components/llm_routing_badge.html``)
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {"user_theme": "dark"}

    from core.llm import _resolve_llm_choice
    from core.llm_model_registry import get_model_info
    from core.models import UserLLMConfig

    try:
        config = UserLLMConfig.load(request.user)
        model_tag = config.active_model or settings.OLLAMA_MODEL
        user_theme = config.theme
    except Exception:
        model_tag = settings.OLLAMA_MODEL
        user_theme = "dark"

    # Per-call routing disclosure — spec requires every cloud call to surface
    # which provider it will use. Cheap: _resolve_llm_choice reads the same
    # UserLLMConfig already loaded above (no extra round trip in practice).
    try:
        ask_choice = _resolve_llm_choice(request.user, "ask")
        modify_choice = _resolve_llm_choice(request.user, "modify")
        routing = {
            "ask": _routing_badge(ask_choice),
            "modify": _routing_badge(modify_choice),
        }
    except Exception:
        routing = {}

    return {
        "active_llm_model": model_tag,
        "active_llm_info": get_model_info(model_tag),
        "user_theme": user_theme,
        "llm_routing": routing,
    }


def maintenance_banner(request):
    """
    Inject the master-kill flag into every template.

    Available in templates as ``{{ llm_master_kill }}``. The base template
    renders a global banner when truthy. Wired separately from llm_context
    so the banner shows even on unauthenticated pages (landing, login).
    """
    return {"llm_master_kill": getattr(settings, "LLM_MASTER_KILL", False)}


def token_budget(request):
    """
    Inject the authenticated user's daily token budget into every template.

    Available in templates as:
        {{ token_used }}      — tokens consumed today
        {{ token_cap }}       — daily cap (0 means unlimited)
        {{ token_pct }}       — used / cap × 100, capped at 100
        {{ token_blocked }}   — True when hard_blocked is set
        {{ token_show }}      — whether to render the banner at all
        {{ token_mode }}      — "managed" | "byok" | "ollama" (the actual
                                routing in effect for this user — used by
                                the help modal to explain the banner state)

    The banner is hidden when neither Ask nor Modify routes through the
    managed shared pool — i.e. both purposes are on BYOK or both are on
    Ollama. In that case the 50K/day cap has nothing to gate, so showing
    "X / 50000 tokens today" is misleading.

    Cap=0 also hides the banner; UserTokenBudget rows are auto-created by
    UserTokenBudget.load() so any authenticated user has a row.
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {"token_show": False}

    try:
        from core.llm import _resolve_llm_choice
        from core.models import UserTokenBudget

        budget = UserTokenBudget.load(request.user)
        budget.reset_if_new_day()
        cap = budget.daily_cap or 0
        used = budget.used_today or 0
        pct = min(100, int((used / cap) * 100)) if cap else 0

        # Decide whether the banner is meaningful for this user RIGHT NOW.
        # If neither Ask nor Modify will draw from the managed pool, the cap
        # doesn't constrain anything — hide the banner. Carry the mode tag
        # so the usage help modal can explain why.
        ask = _resolve_llm_choice(request.user, "ask")
        modify = _resolve_llm_choice(request.user, "modify")

        def _routes_to_managed(choice):
            return choice.provider != "ollama" and not choice.byok

        any_metered = _routes_to_managed(ask) or _routes_to_managed(modify)
        all_byok = ask.byok and modify.byok
        all_ollama = ask.provider == "ollama" and modify.provider == "ollama"

        if all_byok:
            mode = "byok"
        elif all_ollama:
            mode = "ollama"
        else:
            mode = "managed"

        return {
            "token_show": cap > 0 and any_metered,
            "token_used": used,
            "token_cap": cap,
            "token_pct": pct,
            "token_blocked": budget.hard_blocked,
            "token_mode": mode,
        }
    except Exception:
        # DB not ready or table missing — skip the banner silently.
        return {"token_show": False}


def storage_quota(request):
    """
    Inject the authenticated user's storage quota into every template.

    Available in templates as:
        {{ storage_used }}      — bytes currently occupied (cached or fresh)
        {{ storage_files }}     — bytes attributable to uploaded files
        {{ storage_history }}   — bytes attributable to per-project Git history
        {{ storage_limit }}     — effective quota in bytes
        {{ storage_pct }}       — used / limit × 100, capped at 100
        {{ storage_blocked }}   — True when hard_blocked is set
        {{ storage_show }}      — whether to render the dashboard tile

    On the projects landing page the quota is recalculated from disk so the
    dashboard reflects Git-history growth from Modify approvals. Everywhere
    else we read the cached total to avoid a disk walk per page render.
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {"storage_show": False}

    try:
        from core.models import UserStorageQuota
        from core.services.storage_usage import compute_user_storage

        quota = UserStorageQuota.load(request.user)

        # Recompute on the projects landing page so the user sees a fresh
        # breakdown. ``request.path`` is "/projects/" for the list view.
        is_projects_page = request.path.rstrip("/") == "/projects"
        if is_projects_page:
            breakdown = compute_user_storage(request.user)
            from django.utils import timezone

            quota.cached_used_bytes = breakdown.total_bytes
            quota.last_recalculated_at = timezone.now()
            quota.save(
                update_fields=[
                    "cached_used_bytes",
                    "last_recalculated_at",
                    "updated_at",
                ]
            )
            files_bytes = breakdown.files_bytes
            history_bytes = breakdown.history_bytes
        else:
            files_bytes = None
            history_bytes = None

        limit = quota.effective_quota()
        used = quota.cached_used_bytes
        pct = min(100, int((used / limit) * 100)) if limit else 0
        return {
            "storage_show": limit > 0,
            "storage_used": used,
            "storage_files": files_bytes,
            "storage_history": history_bytes,
            "storage_limit": limit,
            "storage_pct": pct,
            "storage_blocked": quota.hard_blocked,
        }
    except Exception:
        # DB not ready or table missing — skip the dashboard silently.
        return {"storage_show": False}
