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
    """Build, cache (2 h), and return the override map from a fresh audit result."""
    from django.core.cache import cache

    om = build_override_map(audit_result)
    cache.set(f"{_CACHE_PREFIX}{project_pk}", om, timeout=_CACHE_TTL)
    logger.info(
        "Trade override map cached — project %s: %d confirmed overrides",
        project_pk,
        len(om),
    )
    return om


def load_override_map(project_pk: str) -> OverrideMap:
    """Retrieve the cached override map; returns {} if no audit has been run."""
    from django.core.cache import cache

    return cache.get(f"{_CACHE_PREFIX}{project_pk}", {})
