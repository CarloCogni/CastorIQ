.claude/skills/htmx-patterns.md
# Skill: HTMX Patterns

Read before adding or modifying interactive UI elements.

## Partial Template Pattern

```html
<!-- Full page template -->
{% block content %}
<div id="chat-messages" hx-get="{% url 'message-list' %}" hx-trigger="load">
  {% include "app/components/message_list.html" %}
</div>
{% endblock %}

<!-- Partial (returned by HTMX endpoint) -->
<!-- app/components/message_list.html -->
{% for msg in messages %}
<div class="message">{{ msg.content }}</div>
{% endfor %}
```

## Common Patterns
- hx-post with hx-target: Form submits that update a specific div
- hx-trigger="every 2s": Polling for async operations
- hx-swap="beforeend": Append to list (chat messages)
- hx-swap="outerHTML": Replace entire element (status badges)
- hx-indicator: Show spinner during request

## Django View for HTMX
```python
def message_list(request, pk):
    messages = Message.objects.filter(session__pk=pk).select_related("session")
    if request.headers.get("HX-Request"):
        return render(request, "app/components/message_list.html", {"messages": messages})
    return render(request, "app/full_page.html", {"messages": messages})
```

## Rules
- Always use select_related in HTMX views (they fire frequently)
- Partial templates go in app/templates/app/components/
- Check HX-Request header to distinguish HTMX from full page loads
- Use hx-swap-oob for updating multiple elements from one response