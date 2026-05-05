# beta/views.py
"""Public-facing views for the beta funnel.

Stub for M3.3 — the application form handler proper lands in M3.4 with a
honeypot, rate limit, and confirmation email. This stub returns 405 so the
URL route is reachable from the landing template without exposing a half-
working POST path.
"""

from django.http import HttpResponseNotAllowed


def apply_view(request):
    """Beta application form receiver.

    M3.4 will implement validation, honeypot detection, throttling, persistence,
    and the confirmation email. Until then we reject all methods so the route
    exists for the landing template's form action target.
    """
    return HttpResponseNotAllowed(permitted_methods=["POST"])
