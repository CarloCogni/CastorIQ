# environments/templatetags/chat_filters.py
import markdown
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name="render_markdown")
def render_markdown(text: str) -> str:
    """
    Converts Markdown text to safe HTML.
    Uses extensions for code blocks, tables, lists, and strict newlines.
    """
    if not text:
        return ""

    # Extensions ensure Gemini/ChatGPT-style formatting works correctly
    html_content = markdown.markdown(
        text,
        extensions=[
            "fenced_code",  # Supports ```code blocks```
            "tables",  # Supports Markdown tables
            "nl2br",  # Treats single linebreaks as <br>
            "sane_lists",  # Prevents weird list numbering behaviors
        ],
    )

    return mark_safe(html_content)
