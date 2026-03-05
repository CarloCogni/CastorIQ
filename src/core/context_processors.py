# core/context_processors.py
"""
Global template context processors.

Registered in settings.TEMPLATES — injected into every template render.
"""

from django.conf import settings


def llm_context(request):
    """
    Inject the current user's active LLM model into every template.

    Available in templates as:
        {{ active_llm_model }}  — the Ollama tag string
        {{ active_llm_info }}   — ModelInfo dataclass (or None)
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {}

    from core.llm_model_registry import get_model_info
    from core.models import UserLLMConfig

    try:
        config = UserLLMConfig.load(request.user)
        model_tag = config.active_model or settings.OLLAMA_MODEL
    except Exception:
        model_tag = settings.OLLAMA_MODEL

    return {
        "active_llm_model": model_tag,
        "active_llm_info": get_model_info(model_tag),
    }
