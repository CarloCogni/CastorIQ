# scheduling/services/source_version/failure_hooks.py
"""Deterministic failure injection points for import transaction tests (DF-A1.2)."""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

_HOOKS: dict[str, Callable[[], None]] = {}


def set_failure_hook(point: str, hook: Callable[[], None] | None) -> None:
    """Register or clear a single failure hook by injection point name."""
    if hook is None:
        _HOOKS.pop(point, None)
    else:
        _HOOKS[point] = hook


def clear_failure_hooks() -> None:
    """Remove all registered failure hooks."""
    _HOOKS.clear()


def maybe_raise(point: str) -> None:
    """Invoke registered hook for *point*, raising if the hook raises."""
    hook = _HOOKS.get(point)
    if hook is not None:
        hook()
