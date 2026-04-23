# core/http.py
"""HTTP helpers shared across apps.

Toast notifications: every mutation endpoint should give the user explicit
feedback. The browser-side primitive lives in ``core/templates/core/base.html``
(it listens for the ``castor:toast`` event); the helpers below let any view
emit that event by attaching an ``HX-Trigger`` response header.
"""

import json
from typing import Literal

from django.http import HttpResponse

ToastLevel = Literal["success", "error", "info"]


def trigger_toast(
    response: HttpResponse,
    message: str,
    level: ToastLevel = "success",
) -> HttpResponse:
    """Attach a ``castor:toast`` HX-Trigger to ``response``.

    Merges with any pre-existing ``HX-Trigger`` payload so callers that already
    fire other client-side events (e.g. an OOB swap notifier) keep working.
    Returns the same response for fluent use.
    """
    existing = response.get("HX-Trigger")
    payload = json.loads(existing) if existing else {}
    payload["castor:toast"] = {"level": level, "message": message}
    response["HX-Trigger"] = json.dumps(payload)
    return response


def toast_response(
    message: str,
    level: ToastLevel = "success",
    status: int = 200,
) -> HttpResponse:
    """Empty ``HttpResponse`` whose only purpose is to fire a toast.

    Use for endpoints that have no body to swap — e.g. validation errors on a
    form with ``hx-swap="none"``, or bulk actions whose UI updates come from
    a separate refresh trigger.
    """
    return trigger_toast(HttpResponse(status=status), message, level)
