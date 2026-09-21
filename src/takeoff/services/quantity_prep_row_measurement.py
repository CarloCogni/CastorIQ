# takeoff/services/quantity_prep_row_measurement.py
"""Session-only prep-row measurement choices (R5D-QTO-MEASURE-02).

Keys off MEASURE-01 ``measurement_target_key`` (not legacy ``row_key``).
Does not rewrite frozen snapshots, HASH-1 contracts, or IFC writeback.
Legacy ``row_key`` (includes quantity_basis) stays stable so mappings/reviews survive.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, MutableMapping
from typing import Any
from uuid import UUID

from takeoff.services.measurement_resolver import (
    MEASUREMENT_TYPE_LABELS,
    MEASUREMENT_TYPES,
    inventory_from_aggregate_row,
    normalize_measurement_type,
    resolve_measurement,
)
from takeoff.services.measurement_target import (
    MEASUREMENT_TARGET_VERSION,
    measurement_target_from_aggregate_row,
)

logger = logging.getLogger(__name__)

CONTRACT_VERSION_V1 = "qty-prep-row-measurement-v1"
SESSION_KEY_PREFIX = "qty_prep_row_measurement"

_MT_KEY_SAFE = re.compile(
    rf"^{re.escape(MEASUREMENT_TARGET_VERSION)}\|[^|]{{1,40}}\|[^|]{{1,200}}\|[^|]{{1,240}}$"
)

# Legacy alias kept for imports; do not invent SI labels here.
# Original units come from IFC project_units via quantity_output_units.
MODEL_UNIT_LABELS: dict[str, str] = {
    "count": "count",
}

# Legacy class basis label → (measurement_type, preferred_source_or_None)
_LEGACY_BASIS_TO_CHOICE: dict[str, tuple[str, str | None]] = {
    "Count": ("count", "element_count"),
    "Length": ("length", "Length"),
    "NetArea": ("area", "NetArea"),
    "GrossArea": ("area", "GrossArea"),
    "NetVolume": ("volume", "NetVolume"),
    "GrossVolume": ("volume", "GrossVolume"),
}


def session_key_for_project(project_id: UUID | str) -> str:
    """Django session key for measurement choices."""
    return f"{SESSION_KEY_PREFIX}:{project_id}"


def empty_payload() -> dict[str, Any]:
    """Empty session contract."""
    return {"contract_version": CONTRACT_VERSION_V1, "choices": {}}


def load_payload(session: MutableMapping[str, Any], project_id: UUID | str) -> dict[str, Any]:
    """Load and sanitize measurement session payload."""
    raw = session.get(session_key_for_project(project_id))
    if not isinstance(raw, dict):
        return empty_payload()
    if str(raw.get("contract_version") or "") != CONTRACT_VERSION_V1:
        return empty_payload()
    choices_in = raw.get("choices")
    if not isinstance(choices_in, dict):
        return empty_payload()
    cleaned: dict[str, dict[str, str]] = {}
    for key, value in choices_in.items():
        key_s = str(key or "").strip()
        if not _MT_KEY_SAFE.match(key_s):
            continue
        if not isinstance(value, dict):
            continue
        mtype = normalize_measurement_type(value.get("measurement_type"))
        if mtype is None:
            continue
        source = str(value.get("selected_source") or "").strip()
        cleaned[key_s] = {
            "measurement_type": mtype,
            "selected_source": source,
        }
    return {"contract_version": CONTRACT_VERSION_V1, "choices": cleaned}


def save_payload(
    session: MutableMapping[str, Any],
    project_id: UUID | str,
    payload: Mapping[str, Any],
) -> None:
    """Persist sanitized payload and mark session modified when supported."""
    session[session_key_for_project(project_id)] = {
        "contract_version": CONTRACT_VERSION_V1,
        "choices": dict(payload.get("choices") or {}),
    }
    if hasattr(session, "modified"):
        session.modified = True  # type: ignore[attr-defined]


def legacy_default_choice(row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Map legacy class basis suggestion to measurement type + optional source."""
    if row.get("basis_unresolved"):
        return None, None
    label = str(row.get("legacy_quantity_basis") or row.get("quantity_basis") or "").strip()
    if not label:
        return None, None
    mapped = _LEGACY_BASIS_TO_CHOICE.get(label)
    if mapped:
        return mapped
    mtype = normalize_measurement_type(label)
    return mtype, None


def _format_total_display(resolution: Any, *, legacy_unresolved: bool) -> str | float | int:
    status = resolution.status
    if status == "choice_required":
        return "Choose source"
    if status == "unavailable":
        return "Not available"
    if status == "unresolved" or resolution.measurement_type in {"", "unresolved"}:
        if legacy_unresolved and not resolution.selected_source:
            return "Unresolved"
        return "—"
    if resolution.total is None:
        return "Not available"
    return resolution.total


def apply_resolution_fields(
    row: dict[str, Any],
    *,
    measurement_type: str | None,
    selected_source: str | None,
    session_owned: bool = False,
) -> None:
    """Mutate prep row display fields from resolver; never changes ``row_key``."""
    inventory = inventory_from_aggregate_row(row)
    if not measurement_type:
        row["measurement_type"] = ""
        row["measurement_type_label"] = ""
        row["measurement_status"] = "unresolved"
        row["compatible_sources"] = []
        row["ifc_quantity_source"] = ""
        row["selected_source"] = ""
        row["model_unit_family"] = ""
        row["model_unit_label"] = "—"
        row["measurement_session"] = False
        row["source_choice_required"] = False
        if row.get("basis_unresolved"):
            row["total"] = None
            row["total_display"] = "Unresolved"
            row["missing_quantity_source"] = True
        return

    resolution = resolve_measurement(
        measurement_type=measurement_type,
        inventory=inventory,
        selected_source=selected_source or None,
    )
    row["measurement_type"] = resolution.measurement_type
    row["measurement_type_label"] = MEASUREMENT_TYPE_LABELS.get(
        resolution.measurement_type, resolution.measurement_type
    )
    row["measurement_status"] = resolution.status
    row["compatible_sources"] = list(resolution.compatible_sources)
    row["ifc_quantity_source"] = resolution.selected_source or ""
    row["selected_source"] = resolution.selected_source or ""
    row["model_unit_family"] = resolution.model_unit_family
    # Family only here — original unit label/token applied by output-units layer
    # from IFC project_units (shared path). Never invent m³/mm/m².
    if resolution.model_unit_family == "count":
        row["model_unit_label"] = "count"
    else:
        row["model_unit_label"] = "—"
    row["measurement_session"] = bool(session_owned)
    row["source_choice_required"] = resolution.status == "choice_required"
    row["measurement_resolution_reason"] = resolution.resolution_reason

    row["total"] = resolution.total
    row["total_display"] = _format_total_display(
        resolution, legacy_unresolved=bool(row.get("basis_unresolved"))
    )
    row["missing_quantity_source"] = (
        resolution.status
        in {
            "unavailable",
            "choice_required",
            "unresolved",
        }
        or resolution.total is None
    )

    # Working-set labels for freeze/export honesty (row_key unchanged).
    if resolution.status == "resolved" and resolution.selected_source:
        row["quantity_source"] = resolution.selected_source
        row["quantity_basis"] = resolution.selected_source
        row["basis_unresolved"] = False
    elif resolution.status == "choice_required":
        row["quantity_source"] = ""
        row["quantity_basis"] = ""
    elif resolution.status == "unavailable":
        row["quantity_source"] = ""
        row["quantity_basis"] = ""
        row["basis_unresolved"] = False
    elif resolution.status == "unresolved":
        pass


def attach_measurement_defaults_to_prep_rows(
    prep_rows: list[dict[str, Any]],
    *,
    grain: str,
) -> None:
    """Attach target keys + default resolution from legacy class suggestions."""
    for row in prep_rows:
        row["element_type_id"] = row.get("element_type_id")
        row["measure_inventory"] = row.get("measure_inventory")
        row["legacy_quantity_basis"] = str(row.get("quantity_basis") or "")
        row["legacy_row_key"] = str(row.get("row_key") or "")
        row["measurement_target_key"] = measurement_target_from_aggregate_row(row, grain=grain)
        mtype, source = legacy_default_choice(row)
        apply_resolution_fields(
            row,
            measurement_type=mtype,
            selected_source=source,
            session_owned=False,
        )


def apply_session_measurements_to_ui(
    qty_prep: dict[str, Any],
    choices: Mapping[str, Mapping[str, str]],
) -> None:
    """Overlay session measurement choices onto prep rows by target key."""
    if not choices:
        qty_prep["session_measurement_note"] = (
            "Row measurement choices are stored in this working session "
            "(measurement target key). They do not change Assign Values mappings."
        )
        return
    for row in qty_prep.get("prep_rows") or []:
        key = str(row.get("measurement_target_key") or "")
        choice = choices.get(key)
        if not choice:
            continue
        apply_resolution_fields(
            row,
            measurement_type=choice.get("measurement_type"),
            selected_source=choice.get("selected_source") or None,
            session_owned=True,
        )
        from takeoff.services.quantity_preparation_ui import refresh_prep_row_status

        refresh_prep_row_status(row)
    qty_prep["session_measurement_active"] = True
    qty_prep["session_measurement_note"] = (
        "Measurement choices are saved in this working session. "
        "Version/export capture of these choices is included in the working "
        "prep rows for new freezes/exports; legacy snapshot hashes are unchanged."
    )


class QuantityPrepRowMeasurementService:
    """Load/save per-target measurement choices in the Django session."""

    def __init__(self, project, user, session: MutableMapping[str, Any]) -> None:
        self.project = project
        self.user = user
        self.session = session
        self.project_id = str(project.pk)

    def get_choices(self) -> dict[str, dict[str, str]]:
        """Return sanitized choice map."""
        return dict(load_payload(self.session, self.project_id).get("choices") or {})

    def apply_choice(
        self,
        *,
        measurement_target_key: str,
        measurement_type: str,
        selected_source: str = "",
        known_target_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        """Store one target choice."""
        key = str(measurement_target_key or "").strip()
        if not _MT_KEY_SAFE.match(key):
            return {"result": None, "error": "Invalid measurement target key."}
        if known_target_keys is not None and key not in known_target_keys:
            return {"result": None, "error": "Unknown measurement target for this project."}
        mtype = normalize_measurement_type(measurement_type)
        if mtype is None or mtype not in MEASUREMENT_TYPES:
            return {"result": None, "error": "Invalid measurement type."}
        source = str(selected_source or "").strip()
        payload = load_payload(self.session, self.project_id)
        choices = dict(payload.get("choices") or {})
        choices[key] = {"measurement_type": mtype, "selected_source": source}
        save_payload(self.session, self.project_id, {"choices": choices})
        return {"result": {"key": key, "choice": choices[key]}, "error": None}

    def apply_batch(
        self,
        *,
        measurement_target_keys: list[str],
        measurement_type: str,
        selected_source: str = "",
        known_target_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        """Store the same choice for many targets."""
        mtype = normalize_measurement_type(measurement_type)
        if mtype is None or mtype not in MEASUREMENT_TYPES:
            return {"result": None, "error": "Invalid measurement type."}
        source = str(selected_source or "").strip()
        payload = load_payload(self.session, self.project_id)
        choices = dict(payload.get("choices") or {})
        applied = 0
        for raw_key in measurement_target_keys:
            key = str(raw_key or "").strip()
            if not _MT_KEY_SAFE.match(key):
                continue
            if known_target_keys is not None and key not in known_target_keys:
                continue
            choices[key] = {"measurement_type": mtype, "selected_source": source}
            applied += 1
        if applied == 0:
            return {"result": None, "error": "No valid measurement targets selected."}
        save_payload(self.session, self.project_id, {"choices": choices})
        return {"result": {"applied": applied}, "error": None}

    def clear_choice(self, *, measurement_target_key: str) -> dict[str, Any]:
        """Remove session choice (row returns to legacy default suggestion)."""
        key = str(measurement_target_key or "").strip()
        payload = load_payload(self.session, self.project_id)
        choices = dict(payload.get("choices") or {})
        if key in choices:
            del choices[key]
            save_payload(self.session, self.project_id, {"choices": choices})
        return {"result": {"key": key}, "error": None}
