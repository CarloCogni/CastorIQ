# beta/throttle.py
"""Rate-limit and daily send-count helpers for the beta funnel.

Single-source-of-truth for: client-IP extraction, the per-IP rate-limit key
function used by ``django-ratelimit``, and the day-bounded counter we use as a
last-line circuit breaker against Brevo budget exhaustion. All state lives in
the ``throttle`` cache (Postgres-backed) so limits are enforced once per
IP/day across every worker, not once per worker.
"""

import logging
from datetime import date

from django.core.cache import caches

logger = logging.getLogger(__name__)

THROTTLE_CACHE = "throttle"
DAILY_KEY_PREFIX = "beta:daily_sends"


def client_ip(request) -> str:
    """Return the originating client IP, honouring ``HTTP_X_FORWARDED_FOR``.

    nginx is the only public ingress in production, so the leftmost address in
    XFF is the real public IP. Falls back to ``REMOTE_ADDR`` for direct
    connections (dev, healthchecks).
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def ratelimit_ip_key(group, request) -> str:
    """``key=`` callable for ``@ratelimit`` decorators on public forms."""
    return client_ip(request) or "unknown"


def _today_key() -> str:
    return f"{DAILY_KEY_PREFIX}:{date.today().isoformat()}"


def today_send_count() -> int:
    """Outbound emails sent so far today (confirmation + operator pings combined).

    Returns 0 if the counter has not been bumped yet today.
    """
    return caches[THROTTLE_CACHE].get(_today_key(), 0)


def bump_daily_count(n: int = 1) -> int:
    """Atomically increment today's send counter; create on first hit.

    Returns the new value. The 48h timeout is belt-and-braces — the date
    embedded in the key already prevents stale values from leaking into
    tomorrow's window.
    """
    cache = caches[THROTTLE_CACHE]
    key = _today_key()
    try:
        return cache.incr(key, n)
    except ValueError:
        cache.set(key, n, timeout=60 * 60 * 48)
        return n
