# islam/scheduling/services/trade_resolver.py
"""Single trade-resolution point — audit override layer on top of coded CSI.

Every trade-based analytic calls resolve_csi() / resolve_csi_trade() instead
of applying _CSI_RE directly.  With override_map=None (the default, used in
As-coded mode) these functions are identical to the raw regex path — zero
behavioural change.

In Audit-suggested mode (?audit_view=corrected), the override_map contains
{task_pk: ai_csi} entries built exclusively from CONFIRMED audit items.
The map is applied on top of the original activity_code; task.activity_code
is never modified.

Public API
----------
  raw_csi(activity_code)                          — raw coded CSI, no override
  resolve_csi(code, task_pk, override_map)        — coded CSI + optional override
  resolve_csi_trade(code, task_pk, override_map)  — human-readable trade name
  is_overridden(task_pk, override_map)            — True when override applied

  build_override_map(audit_result)                — {task_pk: ai_csi} from confirmed items
  save_override_map(project_pk, audit_result)     — build + persist in Django cache
  load_override_map(project_pk)                   — retrieve from cache (or {})
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── CSI regex (same pattern used across all consumer services) ─────────────────

_CSI_RE = re.compile(r"-[A-Z]*(\d{2})\d{4}")

# ── Trade name lookup — superset of all consumer tables ───────────────────────

_TRADE_NAMES: dict[str, str] = {
    "00": "General",
    "01": "General Requirements",
    "02": "Existing Conditions",
    "03": "Concrete",
    "04": "Masonry",
    "05": "Metals",
    "06": "Wood & Plastics",
    "07": "Thermal & Moisture",
    "08": "Openings",
    "09": "Finishes",
    "10": "Specialties",
    "11": "Equipment",
    "12": "Furnishings",
    "13": "Special Construction",
    "14": "Conveying (Elevators)",
    "21": "Fire Suppression",
    "22": "Plumbing",
    "23": "HVAC",
    "25": "Integrated Automation",
    "26": "Electrical",
    "27": "Communications",
    "28": "Electronic Safety / Security",
    "31": "Earthwork",
    "32": "Exterior Improvements",
    "33": "Utilities",
}

# ── Cache config ───────────────────────────────────────────────────────────────

_CACHE_PREFIX = "audit_overrides_"
_CACHE_TTL = 7200  # 2 hours

# Type alias
OverrideMap = dict[str, str]  # {str(task.pk): "09"}


# ── Core resolution ───────────────────────────────────────────────────────────


def raw_csi(activity_code: str) -> str:
    """2-digit CSI from activity_code regex; 'XX' if unparseable."""
    m = _CSI_RE.search(activity_code or "")
    return m.group(1) if m else "XX"


def resolve_csi(
    activity_code: str,
    task_pk: str,
    override_map: OverrideMap | None,
) -> str:
    """Effective CSI for a task, optionally with a confirmed audit override."""
    if override_map and task_pk in override_map:
        return override_map[task_pk]
    return raw_csi(activity_code)


def resolve_csi_trade(
    activity_code: str,
    task_pk: str,
    override_map: OverrideMap | None,
    fallback: str = "Unknown",
) -> str:
    """Human-readable trade name after applying any override."""
    csi = resolve_csi(activity_code, task_pk, override_map)
    if csi == "XX":
        return fallback
    return _TRADE_NAMES.get(csi, f"Div {csi}")


def is_overridden(task_pk: str, override_map: OverrideMap | None) -> bool:
    """True when a confirmed audit override is applied to this task."""
    return bool(override_map and task_pk in override_map)


def trade_name(csi: str) -> str:
    """Trade name from a 2-digit CSI string."""
    return _TRADE_NAMES.get(csi, f"Div {csi}") if csi != "XX" else "Unknown"


# ── Override map lifecycle ─────────────────────────────────────────────────────


def build_override_map(audit_result: dict) -> OverrideMap:
    """Build {task_pk: ai_csi} from confirmed-only audit items."""
    if not audit_result.get("has_data"):
        return {}
    return {
        item["task_id"]: item["ai_csi"]
        for item in audit_result.get("items", [])
        if item.get("verdict") == "confirmed" and item.get("ai_csi") and item.get("task_id")
    }


def save_override_map(project_pk: str, audit_result: dict) -> OverrideMap:
    """Build, persist to DB + warm cache, and return the override map.

    Persists to Project.audit_override_map (DB) so the map survives server
    restarts and Django auto-reload.  Also writes a warm locmem cache entry
    so repeated reads within the same server process are fast.

    A non-empty map is only written if it improves on the existing DB value.
    An empty map (0 confirmed) does NOT overwrite a previously stored map —
    the toggle button is already disabled when confirmed_count=0, so stale DB
    entries are inaccessible and preserving them costs nothing.
    """
    from django.core.cache import cache

    from environments.models import Project

    om = build_override_map(audit_result)

    if om:
        # Non-empty: always persist (this is the authoritative fresh audit result)
        Project.objects.filter(pk=project_pk).update(audit_override_map=om)
        cache.set(f"{_CACHE_PREFIX}{project_pk}", om, timeout=_CACHE_TTL)
        logger.info(
            "Trade override map persisted — project %s: %d confirmed overrides",
            project_pk,
            len(om),
        )
    else:
        # Empty: audit returned 0 confirmed — toggle button will be disabled, so
        # do NOT overwrite a previously stored map.  Just evict the cache so the
        # next load_override_map() falls back to DB and returns the prior entries.
        cache.delete(f"{_CACHE_PREFIX}{project_pk}")
        logger.info(
            "Trade override map: 0 confirmed for project %s — keeping previous DB value",
            project_pk,
        )

    return om


def load_override_map(project_pk: str) -> OverrideMap:
    """Return the override map for a project.

    Tries locmem cache first (fast path).  An empty cache hit is treated as a
    miss — falls through to DB — so a stale empty-cache from a bad LLM run
    never shadows real DB entries.  Returns {} only when DB is also empty.
    """
    from django.core.cache import cache

    cached = cache.get(f"{_CACHE_PREFIX}{project_pk}")
    if cached:  # non-empty cache hit → fast return
        return cached

    # Cache miss or empty cache → always check DB
    from environments.models import Project

    try:
        om = Project.objects.values_list("audit_override_map", flat=True).get(pk=project_pk)
        om = om or {}
        if om:
            cache.set(f"{_CACHE_PREFIX}{project_pk}", om, timeout=_CACHE_TTL)
        return om
    except Project.DoesNotExist:
        return {}
