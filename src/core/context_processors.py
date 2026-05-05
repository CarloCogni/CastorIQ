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
