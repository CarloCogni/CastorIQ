# core/context_processors.py
"""
Global template context processors.

Registered in settings.TEMPLATES — injected into every template render.
"""

from django.conf import settings


def llm_context(request):
    """
    Inject the current user's active LLM model and UI theme into every template.

    Available in templates as:
        {{ active_llm_model }}  — the Ollama tag string
        {{ active_llm_info }}   — ModelInfo dataclass (or None)
        {{ user_theme }}        — "dark" | "light" (always present)
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {"user_theme": "dark"}

    from core.llm_model_registry import get_model_info
    from core.models import UserLLMConfig

    try:
        config = UserLLMConfig.load(request.user)
        model_tag = config.active_model or settings.OLLAMA_MODEL
        user_theme = config.theme
    except Exception:
        model_tag = settings.OLLAMA_MODEL
        user_theme = "dark"

    return {
        "active_llm_model": model_tag,
        "active_llm_info": get_model_info(model_tag),
        "user_theme": user_theme,
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

    Cap=0 hides the banner; UserTokenBudget rows are auto-created by
    UserTokenBudget.load() so any authenticated user has a row.
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {"token_show": False}

    try:
        from core.models import UserTokenBudget

        budget = UserTokenBudget.load(request.user)
        budget.reset_if_new_day()
        cap = budget.daily_cap or 0
        used = budget.used_today or 0
        pct = min(100, int((used / cap) * 100)) if cap else 0
        return {
            "token_show": cap > 0,
            "token_used": used,
            "token_cap": cap,
            "token_pct": pct,
            "token_blocked": budget.hard_blocked,
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
