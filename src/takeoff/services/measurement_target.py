# takeoff/services/measurement_target.py
"""Stable measurement-target identity for editable quantity selection (R5D-QTO-MEASURE-01).

Separates row/group identity from measurement type and IFC quantity source.

Legacy ``row_key`` (``v1|…|quantity_basis``) remains unchanged for mappings,
reviews, exports, and frozen snapshots. New measurement state must key off
``build_measurement_target_key`` only.

Collision / fallback notes
--------------------------
* IFC class is always included so identical type names under different
  classes cannot share a target.
* Prefer ``element_type_id`` (Castor ``IFCElementType`` PK) when the aggregate
  reports a single unambiguous type id.
* If type id is missing or mixed across the bucket, fall back to a normalized
  type-name token. Name fallback can still collide when two distinct IFC type
  objects share the same name under one class — documented limitation until
  aggregates always carry a stable type id.
* Class-grain targets use ``type_token='-'`` (no type identity).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

MEASUREMENT_TARGET_VERSION = "mt1"
ALLOWED_GRAINS: frozenset[str] = frozenset({"type", "ifc_class", "instance"})

_PIPE_SAFE = re.compile(r"[|\r\n]+")


def _scrub(part: str) -> str:
    """Remove delimiter characters from a key segment."""
    return _PIPE_SAFE.sub("/", (part or "").strip())


def normalize_type_name(type_name: str | None) -> str:
    """Deterministic type-name fallback token (whitespace collapsed, case kept)."""
    raw = (type_name or "").strip()
    if not raw:
        return ""
    return re.sub(r"\s+", " ", raw)


def type_identity_token(
    *,
    element_type_id: UUID | str | None = None,
    type_name: str | None = None,
) -> str:
    """Return stable type identity segment: ``id:<uuid>``, ``name:<norm>``, or ``-``."""
    if element_type_id is not None and str(element_type_id).strip():
        return f"id:{_scrub(str(element_type_id).strip())}"
    name = normalize_type_name(type_name)
    if name:
        return f"name:{_scrub(name)}"
    return "-"


def build_measurement_target_key(
    *,
    grain: str,
    ifc_class: str,
    element_type_id: UUID | str | None = None,
    type_name: str | None = None,
    global_id: str | None = None,
) -> str:
    """Build stable measurement-target key excluding measurement/source choice.

    Format: ``mt1|{grain}|{ifc_class}|{type_token}``

    ``type_token`` prefers ``id:<element_type_id>``; falls back to
    ``name:<normalized_type_name>``; class grain uses ``-``.
    Instance grain uses ``gid:<global_id>``.
    """
    grain_norm = grain if grain in ALLOWED_GRAINS else "ifc_class"
    ifc = _scrub(ifc_class) or "-"
    if grain_norm == "ifc_class":
        type_token = "-"
    elif grain_norm == "instance":
        gid = _scrub(str(global_id or "").strip()) or "-"
        type_token = f"gid:{gid}"
    else:
        type_token = type_identity_token(
            element_type_id=element_type_id,
            type_name=type_name,
        )
    return f"{MEASUREMENT_TARGET_VERSION}|{grain_norm}|{ifc}|{type_token}"


def parse_measurement_target_key(key: str) -> dict[str, str] | None:
    """Parse a measurement-target key into parts, or None if invalid."""
    parts = str(key or "").split("|")
    if len(parts) != 4:
        return None
    version, grain, ifc_class, type_token = parts
    if version != MEASUREMENT_TARGET_VERSION:
        return None
    if grain not in ALLOWED_GRAINS:
        return None
    if not ifc_class:
        return None
    return {
        "version": version,
        "grain": grain,
        "ifc_class": ifc_class,
        "type_token": type_token,
    }


def measurement_target_from_aggregate_row(
    row: Mapping[str, Any],
    *,
    grain: str,
) -> str:
    """Build target key from a ModelQuantities / prep-like aggregate dict."""
    return build_measurement_target_key(
        grain=grain,
        ifc_class=str(row.get("ifc_class") or row.get("ifc_type") or ""),
        element_type_id=row.get("element_type_id"),
        type_name=str(row.get("type_name") or ""),
    )
