# takeoff/services/quantity_prep_session_state.py
"""FREEZE-UX-1 — lightweight pending review-change detection for Quantities.

Inspects session mapping annotations and unit confirmations only.
No DB writes, no entity scans, no snapshot diff.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from takeoff.services.quantity_prep_row_mapping import QuantityPrepRowMappingService
from takeoff.services.quantity_unit_confirmation import QuantityUnitConfirmationService

_MAPPING_FIELDS = ("classification_code", "package_boq_mapping", "work_package")


def _annotation_has_mapping_value(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    for field in _MAPPING_FIELDS:
        raw = payload.get(field)
        if isinstance(raw, dict) and str(raw.get("value") or "").strip():
            return True
        if isinstance(raw, str) and raw.strip():
            return True
    return False


def detect_pending_quantity_review_changes(
    *,
    project: Any,
    user: Any,
    session: MutableMapping[str, Any],
) -> dict[str, Any]:
    """Return pending mapping/unit flags for Quantities freeze guidance."""
    mapping_svc = QuantityPrepRowMappingService(project, user, session)
    annotations = mapping_svc.get_annotations() or {}
    has_mapping = any(_annotation_has_mapping_value(payload) for payload in annotations.values())

    unit_svc = QuantityUnitConfirmationService(project, user, session)
    has_units = bool(unit_svc.get_confirmation() or {})

    pending_types: list[str] = []
    if has_mapping:
        pending_types.append("mapping")
    if has_units:
        pending_types.append("units")

    has_pending = bool(pending_types)
    return {
        "has_pending_review_changes": has_pending,
        "pending_change_types": pending_types,
        "freeze_cta_enabled": has_pending,
        "has_pending_mapping": has_mapping,
        "has_pending_units": has_units,
    }
