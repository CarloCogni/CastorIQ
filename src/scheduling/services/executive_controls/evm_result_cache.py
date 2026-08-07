# scheduling/services/executive_controls/evm_result_cache.py
"""Short-TTL shared compute_evm cache across parallel HTMX requests.

Executive EVM / Overview cost fragments each create a new E8EVMComputeSession.
Without a cross-request cache, four parallel HTMX loads recompute the same
payload. This module caches the raw compute_evm result for a short TTL keyed
by project and data date. Metric definitions are unchanged.
"""

from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Any

from django.core.cache import cache

logger = logging.getLogger(__name__)

EVM_RESULT_CACHE_TTL_SECONDS = 45
_CACHE_VERSION = 1

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def evm_result_cache_key(project_id: str, as_of_date: date | None) -> str:
    """Stable cache key for a project + as-of date pair."""
    dd = as_of_date.isoformat() if as_of_date else "default"
    return f"e8:compute_evm:v{_CACHE_VERSION}:{project_id}:{dd}"


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def get_or_compute_evm(project_id: str, as_of_date: date | None = None) -> dict[str, Any]:
    """Return compute_evm output, reusing a short-TTL cache when present.

    Double-checked locking avoids a thundering herd when several HTMX
    fragments miss the cache at the same time in one process.
    """
    project_key = str(project_id)
    key = evm_result_cache_key(project_key, as_of_date)
    cached = cache.get(key)
    if cached is not None:
        logger.debug("EVM result cache hit project=%s as_of=%s", project_key, as_of_date)
        return cached

    with _lock_for(key):
        cached = cache.get(key)
        if cached is not None:
            return cached

        from scheduling.services.evm import compute_evm

        result = compute_evm(project_key, as_of_date=as_of_date)
        try:
            cache.set(key, result, EVM_RESULT_CACHE_TTL_SECONDS)
        except Exception:
            logger.exception("Failed to store EVM result cache for project=%s", project_key)
        return result
