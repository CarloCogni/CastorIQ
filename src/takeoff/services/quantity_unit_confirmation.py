# takeoff/services/quantity_unit_confirmation.py
"""UNIT-2 — project quantity unit proposals and session confirmation.

Reads ``IFCFile.project_units`` (IfcUnitAssignment) as proposals only.
User must confirm before prep rows store canonical tokens (m3/m2/mm/count).
No numeric conversion, no cost, no IFC mutation, no migrations.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any
from uuid import UUID

from takeoff.services.quantity_unit_display import attach_unit_basis_display

logger = logging.getLogger(__name__)

SESSION_KEY_PREFIX = "qty_unit_confirm"

FAMILY_VOLUME = "volume"
FAMILY_AREA = "area"
FAMILY_LENGTH = "length"
FAMILY_COUNT = "count"

FAMILIES: tuple[str, ...] = (FAMILY_VOLUME, FAMILY_AREA, FAMILY_LENGTH, FAMILY_COUNT)

FAMILY_LABELS: dict[str, str] = {
    FAMILY_VOLUME: "Volume",
    FAMILY_AREA: "Area",
    FAMILY_LENGTH: "Length",
    FAMILY_COUNT: "Count",
}

# Quantity basis labels → measure family
_BASIS_TO_FAMILY: dict[str, str] = {
    "NetVolume": FAMILY_VOLUME,
    "GrossVolume": FAMILY_VOLUME,
    "NetArea": FAMILY_AREA,
    "NetSideArea": FAMILY_AREA,
    "Length": FAMILY_LENGTH,
    "Count": FAMILY_COUNT,
}

_IFC_KEY_BY_FAMILY: dict[str, str] = {
    FAMILY_VOLUME: "VOLUMEUNIT",
    FAMILY_AREA: "AREAUNIT",
    FAMILY_LENGTH: "LENGTHUNIT",
}

_STATUS_UNRESOLVED = "unresolved"
_STATUS_CONFIRMED = "confirmed"
_STATUS_OVERRIDDEN = "overridden"

# Declared IFC label → canonical storage token + display label
_DECLARED_TO_CANONICAL: dict[str, tuple[str, str]] = {
    "m³": ("m3", "m³"),
    "m3": ("m3", "m³"),
    "m^3": ("m3", "m³"),
    "m²": ("m2", "m²"),
    "m2": ("m2", "m²"),
    "m^2": ("m2", "m²"),
    "mm": ("mm", "mm"),
    "m": ("m", "m"),
    "cm": ("cm", "cm"),
    "km": ("km", "km"),
    "ft": ("ft", "ft"),
    "in": ("in", "in"),
    "kg": ("kg", "kg"),
    "degree": ("degree", "degree"),
    "rad": ("rad", "rad"),
    "count": ("count", "count"),
}


def session_key_for_project(project_id: UUID | str) -> str:
    """Return Django session key for unit confirmation payload."""
    return f"{SESSION_KEY_PREFIX}:{project_id}"


def family_for_quantity_basis(basis: str | None) -> str:
    """Return measure family for a prep quantity_basis label."""
    return _BASIS_TO_FAMILY.get(str(basis or "").strip(), "")


def canonicalize_declared_unit(raw: str | None) -> tuple[str, str]:
    """Map an IFC-declared unit label to (token, display_label) or empty."""
    text = str(raw or "").strip()
    if not text:
        return "", ""
    if text in _DECLARED_TO_CANONICAL:
        return _DECLARED_TO_CANONICAL[text]
    lower = text.lower()
    for key, pair in _DECLARED_TO_CANONICAL.items():
        if key.lower() == lower:
            return pair
    # Unknown declared string — keep as-is token for override honesty, no SI invent.
    safe = text[:32]
    return safe, safe


def _collect_project_units(project: Any) -> tuple[dict[str, str], bool, int]:
    """Return merged project_units, conflict flag, and file count with units."""
    from ifc_processor.models import IFCFile

    files = list(
        IFCFile.objects.filter(project_id=getattr(project, "pk", None), status="completed")
        .only("project_units", "name")
        .order_by("created_at")
    )
    merged: dict[str, str] = {}
    conflict = False
    with_units = 0
    for ifc in files:
        units = ifc.project_units if isinstance(ifc.project_units, dict) else {}
        if not units:
            continue
        with_units += 1
        for key, value in units.items():
            text = str(value or "").strip()
            if not text:
                continue
            if key in merged and merged[key] != text:
                conflict = True
            elif key not in merged:
                merged[key] = text
    return merged, conflict, with_units


def discover_project_unit_proposals(project: Any) -> dict[str, Any]:
    """Build measure-family unit proposals from indexed IFC project_units."""
    units, conflict, file_count = _collect_project_units(project)
    families: list[dict[str, Any]] = []

    for family in FAMILIES:
        if family == FAMILY_COUNT:
            token, label = "count", "count"
            declared = "count"
            available = True
        else:
            ifc_key = _IFC_KEY_BY_FAMILY[family]
            declared = str(units.get(ifc_key) or "").strip()
            token, label = canonicalize_declared_unit(declared)
            available = bool(token)
        families.append(
            {
                "family": family,
                "label": FAMILY_LABELS[family],
                "ifc_key": _IFC_KEY_BY_FAMILY.get(family, ""),
                "declared_raw": declared,
                "proposed_token": token if available and not conflict else "",
                "proposed_label": label if available and not conflict else "",
                "available": available and not conflict,
                "source": "ifc_project_units" if family != FAMILY_COUNT else "dimensionless",
            }
        )

    source = "ifc_project_units" if file_count else "none"
    if conflict:
        source = "conflict"
    return {
        "families": families,
        "source": source,
        "conflict": conflict,
        "ifc_file_count_with_units": file_count,
        "project_units": dict(units),
        "helper": (
            "Confirm IFC-declared units before quantity totals use confirmed labels. "
            "No conversion is applied; labels only."
            if not conflict
            else "IFC files declare conflicting units — leave unresolved or override explicitly."
        ),
    }


def _normalize_stored(raw: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, Mapping):
        return out
    for family in FAMILIES:
        item = raw.get(family)
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "").strip()
        if status not in {_STATUS_CONFIRMED, _STATUS_OVERRIDDEN}:
            continue
        token = str(item.get("token") or "").strip()
        if not token:
            continue
        label = str(item.get("label") or token).strip() or token
        out[family] = {
            "status": status,
            "token": token,
            "label": label,
            "source": str(item.get("source") or "user"),
        }
    return out


class QuantityUnitConfirmationService:
    """Session-backed unit confirmation for one project."""

    def __init__(self, project: Any, user: Any | None, session: MutableMapping[str, Any]) -> None:
        self.project = project
        self.user = user
        self.session = session
        self._key = session_key_for_project(getattr(project, "pk", ""))

    def get_confirmation(self) -> dict[str, dict[str, Any]]:
        """Return confirmed/overridden families from session."""
        return _normalize_stored(self.session.get(self._key))

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.session[self._key] = data
        try:
            self.session.modified = True  # type: ignore[attr-defined]
        except Exception:
            pass

    def confirm_families(self, families: Sequence[str]) -> dict[str, Any]:
        """Confirm proposed IFC units for the given families."""
        proposals = discover_project_unit_proposals(self.project)
        by_family = {f["family"]: f for f in proposals["families"]}
        current = self.get_confirmation()
        for family in families:
            fam = str(family or "").strip()
            if fam not in FAMILIES:
                continue
            prop = by_family.get(fam) or {}
            token = str(prop.get("proposed_token") or "").strip()
            label = str(prop.get("proposed_label") or "").strip()
            if not token or not prop.get("available"):
                continue
            current[fam] = {
                "status": _STATUS_CONFIRMED,
                "token": token,
                "label": label,
                "source": "ifc_project_units",
            }
        self._save(current)
        logger.info(
            "qty unit confirm project=%s families=%s",
            getattr(self.project, "pk", None),
            sorted(current.keys()),
        )
        return {"result": current, "error": None}

    def clear_families(self, families: Sequence[str] | None = None) -> dict[str, Any]:
        """Leave families unresolved (remove session confirmation)."""
        current = self.get_confirmation()
        if families is None:
            current = {}
        else:
            for family in families:
                current.pop(str(family or "").strip(), None)
        self._save(current)
        return {"result": current, "error": None}

    def override_family(self, family: str, token: str) -> dict[str, Any]:
        """User override with an explicit canonical token (no conversion)."""
        fam = str(family or "").strip()
        if fam not in FAMILIES:
            return {"result": None, "error": "Unknown measure family."}
        canon_token, canon_label = canonicalize_declared_unit(token)
        if not canon_token:
            return {"result": None, "error": "Override unit is empty."}
        # Reject inventing metres when proposal was mm? Allow explicit override.
        current = self.get_confirmation()
        current[fam] = {
            "status": _STATUS_OVERRIDDEN,
            "token": canon_token,
            "label": canon_label,
            "source": "user_override",
        }
        self._save(current)
        return {"result": current, "error": None}

    def build_panel(self) -> dict[str, Any]:
        """Panel context for Quantities Unit Confirmation UI."""
        proposals = discover_project_unit_proposals(self.project)
        confirmed = self.get_confirmation()
        rows: list[dict[str, Any]] = []
        for prop in proposals["families"]:
            family = prop["family"]
            conf = confirmed.get(family)
            if conf:
                status = conf["status"]
                active_token = conf["token"]
                active_label = conf["label"]
            else:
                status = _STATUS_UNRESOLVED
                active_token = ""
                active_label = ""
            rows.append(
                {
                    **prop,
                    "status": status,
                    "active_token": active_token,
                    "active_label": active_label,
                    "status_label": {
                        _STATUS_UNRESOLVED: "Unresolved",
                        _STATUS_CONFIRMED: "Confirmed",
                        _STATUS_OVERRIDDEN: "Overridden",
                    }.get(status, status),
                }
            )
        return {
            **proposals,
            "rows": rows,
            "confirmation": confirmed,
            "any_confirmed": bool(confirmed),
            "all_available_confirmed": all(
                (not r["available"]) or r["status"] in {_STATUS_CONFIRMED, _STATUS_OVERRIDDEN}
                for r in rows
            ),
        }


def apply_unit_confirmation_to_qty_prep(
    qty_prep: MutableMapping[str, Any],
    *,
    confirmation: Mapping[str, Mapping[str, Any]],
) -> None:
    """Rewrite prep/rule ``unit_basis`` from confirmation; refresh display labels.

    Does not change numeric totals. Unconfirmed families keep model-unit tokens.
    """
    conf = _normalize_stored(confirmation)

    def _apply_row(row: MutableMapping[str, Any]) -> None:
        basis = str(row.get("quantity_basis") or "").strip()
        family = family_for_quantity_basis(basis)
        if not family:
            attach_unit_basis_display(row)
            return
        if family == FAMILY_COUNT:
            # Count is dimensionless; keep/ensure count token for display.
            if basis == "Count":
                row["unit_basis"] = "count"
            attach_unit_basis_display(row)
            return
        entry = conf.get(family)
        if entry and entry.get("token"):
            row["unit_basis"] = entry["token"]
        attach_unit_basis_display(row)

    for row in qty_prep.get("prep_rows") or []:
        if isinstance(row, dict):
            _apply_row(row)
    for rule in qty_prep.get("basis_rules") or []:
        if isinstance(rule, dict):
            _apply_row(rule)

    qty_prep["unit_confirmation_applied"] = bool(conf)


def apply_unit_confirmation_service_to_qty_prep(
    qty_prep: MutableMapping[str, Any],
    *,
    project: Any,
    user: Any | None,
    session: MutableMapping[str, Any],
) -> dict[str, Any]:
    """Discover panel + apply session confirmation onto qty_prep."""
    svc = QuantityUnitConfirmationService(project, user, session)
    panel = svc.build_panel()
    apply_unit_confirmation_to_qty_prep(qty_prep, confirmation=svc.get_confirmation())
    qty_prep["unit_confirmation"] = panel
    return panel
