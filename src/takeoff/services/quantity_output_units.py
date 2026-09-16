# takeoff/services/quantity_output_units.py
"""R5D-QTO-UNIT-03 — session output-unit selection + apply conversion to prep rows.

Model units come from IFC project_units (read-only). Output units are session-only.
Conversion always starts from each row's raw model_total.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any
from uuid import UUID

from takeoff.services.quantity_unit_confirmation import (
    FAMILIES,
    FAMILY_COUNT,
    FAMILY_LABELS,
    QuantityUnitConfirmationService,
    _normalize_stored,
    discover_project_unit_proposals,
)
from takeoff.services.quantity_unit_conversion import (
    CONVERSION_VERSION,
    OUTPUT_CHOICES,
    canonicalize_unit_token,
    conversion_example_text,
    conversion_provenance_payload,
    convert_quantity,
    display_unit_label,
    family_for_measurement_type,
    format_quantity_display,
    output_choices_for_family,
)

logger = logging.getLogger(__name__)

SESSION_KEY_PREFIX = "qty_output_units"
CONTRACT_VERSION = "qty-output-units-v1"

# Pilot / MEASURE-02: count is dimensionless. Length/area/volume must come
# from IFC project_units — never invent mm/m²/m³ when undeclared.
_DEFAULT_MODEL_BY_FAMILY: dict[str, str] = {
    FAMILY_COUNT: "count",
}

UNKNOWN_SOURCE_UNIT_LABEL = "Unknown source unit"


def session_key_for_project(project_id: UUID | str) -> str:
    """Django session key for output-unit selections."""
    return f"{SESSION_KEY_PREFIX}:{project_id}"


def _normalize_output_map(raw: Mapping[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(raw, Mapping):
        return out
    for family in FAMILIES:
        token = canonicalize_unit_token(raw.get(family))
        if not token:
            continue
        allowed = OUTPUT_CHOICES.get(family, ())
        if token not in allowed:
            continue
        out[family] = token
    return out


def _normalize_class_units(raw: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    """Normalize ifc_class → {family: token} overrides."""
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, Mapping):
        return out
    for cls_raw, fam_map in raw.items():
        cls = str(cls_raw or "").strip()
        if not cls or not isinstance(fam_map, Mapping):
            continue
        cleaned = _normalize_output_map(fam_map)
        if cleaned:
            out[cls] = cleaned
    return out


def discover_model_units(project: Any) -> dict[str, str]:
    """Canonical model unit token per family from IFC project_units only.

    Missing declaration → empty token (Unknown source unit). Count stays ``count``.
    """
    proposals = discover_project_unit_proposals(project)
    by_family = {f["family"]: f for f in proposals.get("families") or []}
    out: dict[str, str] = {}
    for family in FAMILIES:
        if family == FAMILY_COUNT:
            out[family] = "count"
            continue
        prop = by_family.get(family) or {}
        token = canonicalize_unit_token(prop.get("proposed_token") or prop.get("proposed_label"))
        if not token and prop.get("available"):
            token = canonicalize_unit_token(prop.get("declared_raw"))
        out[family] = token or ""
    return out


def discover_model_unit_provenance(project: Any) -> dict[str, dict[str, Any]]:
    """Per-family source-unit provenance for Units UI honesty."""
    proposals = discover_project_unit_proposals(project)
    by_family = {f["family"]: f for f in proposals.get("families") or []}
    model = discover_model_units(project)
    out: dict[str, dict[str, Any]] = {}
    for family in FAMILIES:
        prop = by_family.get(family) or {}
        token = model.get(family) or ""
        if family == FAMILY_COUNT:
            out[family] = {
                "token": "count",
                "label": "count",
                "known": True,
                "source": "dimensionless",
                "declared_raw": "count",
                "provenance_note": "Count is dimensionless.",
            }
            continue
        known = bool(token)
        out[family] = {
            "token": token,
            "label": display_unit_label(token) if known else UNKNOWN_SOURCE_UNIT_LABEL,
            "known": known,
            "source": "ifc_project_units" if known else "none",
            "declared_raw": str(prop.get("declared_raw") or ""),
            "provenance_note": (
                f"From IFC project units ({prop.get('ifc_key') or family})."
                if known
                else "No IFC project unit declared for this measure; conversion disabled."
            ),
        }
    return out


class QuantityOutputUnitsService:
    """Session-backed output unit selections for one project."""

    def __init__(self, project: Any, user: Any | None, session: MutableMapping[str, Any]) -> None:
        self.project = project
        self.user = user
        self.session = session
        self._key = session_key_for_project(getattr(project, "pk", ""))

    def _payload(self) -> dict[str, Any]:
        raw = self.session.get(self._key)
        if not isinstance(raw, Mapping):
            return {}
        if str(raw.get("contract_version") or "") != CONTRACT_VERSION:
            return {}
        return dict(raw)

    def get_output_units(self) -> dict[str, str]:
        """Return family → output token (empty means use model unit). Global defaults."""
        return _normalize_output_map(self._payload().get("units"))

    def get_class_units(self) -> dict[str, dict[str, str]]:
        """Return ifc_class → {family: token} overrides (REVIEW-08C)."""
        return _normalize_class_units(self._payload().get("class_units"))

    def _save(
        self,
        units: dict[str, str],
        class_units: dict[str, dict[str, str]] | None = None,
    ) -> None:
        existing_class = _normalize_class_units(self._payload().get("class_units"))
        self.session[self._key] = {
            "contract_version": CONTRACT_VERSION,
            "units": units,
            "class_units": class_units if class_units is not None else existing_class,
        }
        try:
            self.session.modified = True  # type: ignore[attr-defined]
        except Exception:
            pass

    def apply_output_units(self, units: Mapping[str, str]) -> dict[str, Any]:
        """Validate and store global family output units. Rejects incompatible tokens."""
        model = discover_model_units(self.project)
        cleaned: dict[str, str] = {}
        for family, raw_token in units.items():
            fam = str(family or "").strip()
            if fam not in FAMILIES:
                return {"result": None, "error": f"Unknown measure family: {fam}"}
            token = canonicalize_unit_token(raw_token)
            if not token:
                return {"result": None, "error": f"Invalid output unit for {fam}."}
            if token not in OUTPUT_CHOICES.get(fam, ()):
                return {
                    "result": None,
                    "error": f"Unit {token} is not allowed for {fam}.",
                }
            model_tok = model.get(fam) or ""
            if token == model_tok:
                continue
            cleaned[fam] = token
        self._save(cleaned)
        logger.info(
            "qty output units project=%s units=%s",
            getattr(self.project, "pk", None),
            cleaned,
        )
        return {"result": cleaned, "error": None}

    def apply_class_output_unit(
        self,
        *,
        ifc_class: str,
        family: str,
        output_unit: str,
    ) -> dict[str, Any]:
        """Set or clear an output-unit override for one IFC class + measure family."""
        cls = str(ifc_class or "").strip()
        fam = str(family or "").strip()
        if not cls:
            return {"result": None, "error": "IFC class is required."}
        if fam not in FAMILIES:
            return {"result": None, "error": f"Unknown measure family: {fam}"}
        if fam == FAMILY_COUNT:
            return {"result": {"ifc_class": cls, "family": fam, "unit": "count"}, "error": None}

        token = canonicalize_unit_token(output_unit)
        if not token:
            return {"result": None, "error": f"Invalid output unit for {fam}."}
        if token not in OUTPUT_CHOICES.get(fam, ()):
            return {"result": None, "error": f"Unit {token} is not allowed for {fam}."}

        model = discover_model_units(self.project)
        model_tok = model.get(fam) or ""
        class_units = self.get_class_units()
        per = dict(class_units.get(cls) or {})
        # Identity with known model unit clears the class override.
        if model_tok and token == model_tok:
            per.pop(fam, None)
        else:
            per[fam] = token
        if per:
            class_units[cls] = per
        else:
            class_units.pop(cls, None)
        self._save(self.get_output_units(), class_units=class_units)
        logger.info(
            "qty class output unit project=%s class=%s family=%s unit=%s",
            getattr(self.project, "pk", None),
            cls,
            fam,
            token,
        )
        return {
            "result": {"ifc_class": cls, "family": fam, "unit": per.get(fam, model_tok)},
            "error": None,
        }

    def reset_class_output_units(self, *, ifc_class: str) -> dict[str, Any]:
        """Clear class-scoped output overrides for one IFC class only."""
        cls = str(ifc_class or "").strip()
        if not cls:
            return {"result": None, "error": "IFC class is required."}
        class_units = self.get_class_units()
        class_units.pop(cls, None)
        self._save(self.get_output_units(), class_units=class_units)
        return {"result": {"ifc_class": cls}, "error": None}

    def reset_to_model_units(self) -> dict[str, Any]:
        """Clear global family overrides. Preserves class-scoped overrides."""
        self._save({}, class_units=self.get_class_units())
        return {"result": {}, "error": None}

    def effective_output_units(self) -> dict[str, str]:
        """Model units with global session overrides (empty when source unknown)."""
        model = discover_model_units(self.project)
        overrides = self.get_output_units()
        out: dict[str, str] = {}
        for fam in FAMILIES:
            model_tok = model.get(fam) or ""
            if not model_tok and fam != FAMILY_COUNT:
                out[fam] = ""
                continue
            out[fam] = overrides.get(fam) or model_tok
        return out

    def effective_output_units_for_class(self, ifc_class: str) -> dict[str, str]:
        """Global effective units with class overrides for ``ifc_class``."""
        base = self.effective_output_units()
        cls = str(ifc_class or "").strip()
        if not cls:
            return base
        overrides = self.get_class_units().get(cls) or {}
        out = dict(base)
        for fam, token in overrides.items():
            if fam in FAMILIES and token:
                out[fam] = token
        return out

    def build_panel(self) -> dict[str, Any]:
        """Units modal/panel context."""
        model = discover_model_units(self.project)
        provenance = discover_model_unit_provenance(self.project)
        overrides = self.get_output_units()
        class_units = self.get_class_units()
        effective = self.effective_output_units()
        rows: list[dict[str, Any]] = []
        for family in FAMILIES:
            model_tok = model[family]
            prov = provenance.get(family) or {}
            known = bool(prov.get("known"))
            out_tok = effective[family] if known else ""
            rows.append(
                {
                    "family": family,
                    "label": FAMILY_LABELS[family],
                    "model_unit": model_tok,
                    "model_unit_label": (
                        display_unit_label(model_tok) if known else UNKNOWN_SOURCE_UNIT_LABEL
                    ),
                    "source_known": known,
                    "source_provenance": prov.get("source") or "none",
                    "source_declared_raw": prov.get("declared_raw") or "",
                    "provenance_note": prov.get("provenance_note") or "",
                    "output_unit": out_tok,
                    "output_unit_label": (
                        display_unit_label(out_tok)
                        if out_tok
                        else (UNKNOWN_SOURCE_UNIT_LABEL if not known else "—")
                    ),
                    "overridden": family in overrides and known,
                    "choices": output_choices_for_family(family) if known else [],
                    "locked": family == FAMILY_COUNT or not known,
                    "example_text": (
                        conversion_example_text(model_unit=model_tok, output_unit=out_tok)
                        if known and model_tok and out_tok
                        else ("Conversion unavailable — unknown source unit." if not known else "—")
                    ),
                }
            )
        return {
            "rows": rows,
            "model_units": model,
            "model_unit_provenance": provenance,
            "output_units": overrides,
            "class_units": class_units,
            "effective": effective,
            "any_override": bool(overrides) or bool(class_units),
            "conversion_version": CONVERSION_VERSION,
            "helper": (
                "Session family units are defaults. Class Apply can override output "
                "unit for one IFC class without changing other classes. "
                "Unknown source units do not convert."
            ),
            "gap_note": (
                "Per-quantity/property units are not indexed separately; "
                "measure families use IFC project unit declarations."
            ),
        }


def resolve_original_unit(
    *,
    project: Any | None = None,
    family: str | None = None,
    model_units: Mapping[str, str] | None = None,
    provenance: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Shared original-unit resolution for settings, table, calc, export, restore.

    Precedence:
    1. Explicit per-source unit when indexed (not available today — reserved).
    2. IFC project unit for the measure family.
    3. Unknown — never invent m/mm/m²/m³.
    """
    fam = str(family or "").strip()
    if fam == FAMILY_COUNT:
        return {
            "token": "count",
            "label": "count",
            "known": True,
            "source": "dimensionless",
            "provenance_note": "Count is dimensionless.",
        }
    if not fam:
        return {
            "token": "",
            "label": UNKNOWN_SOURCE_UNIT_LABEL,
            "known": False,
            "source": "none",
            "provenance_note": "No measure family selected.",
        }
    units = dict(model_units or {})
    prov_map = dict(provenance or {})
    if project is not None and (not units or fam not in units):
        units = discover_model_units(project)
    if project is not None and fam not in prov_map:
        prov_map = discover_model_unit_provenance(project)
    token = canonicalize_unit_token(units.get(fam))
    prov = dict(prov_map.get(fam) or {})
    if token:
        return {
            "token": token,
            "label": display_unit_label(token),
            "known": True,
            "source": str(prov.get("source") or "ifc_project_units"),
            "declared_raw": str(prov.get("declared_raw") or ""),
            "provenance_note": str(
                prov.get("provenance_note")
                or "From IFC project unit declarations for this measure family."
            ),
        }
    return {
        "token": "",
        "label": UNKNOWN_SOURCE_UNIT_LABEL,
        "known": False,
        "source": "none",
        "declared_raw": str(prov.get("declared_raw") or ""),
        "provenance_note": str(
            prov.get("provenance_note")
            or "No IFC project unit declared for this measure; conversion disabled."
        ),
    }


def _model_unit_for_row(row: Mapping[str, Any], model_units: Mapping[str, str]) -> str:
    """Original unit token from IFC project units only — never invent from labels."""
    family = family_for_measurement_type(row.get("measurement_type"))
    if not family:
        family = str(row.get("model_unit_family") or "").strip()
    if family == FAMILY_COUNT:
        return "count"
    # Do not fall back to row.model_unit_label — that was inventing m³/mm via
    # MODEL_UNIT_LABELS while Measurement settings correctly showed Unknown.
    return canonicalize_unit_token(model_units.get(family))


def apply_output_conversion_to_row(
    row: MutableMapping[str, Any],
    *,
    model_units: Mapping[str, str],
    output_units: Mapping[str, str],
) -> None:
    """Preserve model_total/unit; set display total/unit from conversion."""
    status = str(row.get("measurement_status") or "")
    family = family_for_measurement_type(row.get("measurement_type")) or str(
        row.get("model_unit_family") or ""
    )

    # Establish raw model value once (never convert from a prior output).
    if "model_total" not in row or row.get("_model_total_locked") is not True:
        raw = row.get("total")
        row["model_total"] = raw
        row["_model_total_locked"] = True

    # Idle / unresolved: do not claim Unknown solely because no source is selected.
    if status in {"unavailable", "choice_required", "unresolved"} or not family:
        model_unit = _model_unit_for_row(row, model_units) if family else ""
        row["model_unit"] = model_unit
        row["original_unit"] = model_unit
        if family and model_unit:
            row["model_unit_label"] = display_unit_label(model_unit)
            row["source_unit_known"] = True
            row["unit_provenance"] = "ifc_project_units"
        elif family and not model_unit:
            row["model_unit_label"] = UNKNOWN_SOURCE_UNIT_LABEL
            row["source_unit_known"] = False
            row["unit_provenance"] = "none"
        else:
            row["model_unit_label"] = "—"
            row["source_unit_known"] = False
            row["unit_provenance"] = "none"
        row["output_total"] = None
        row["output_unit"] = ""
        row["output_unit_label"] = "—"
        row["unit_converted"] = False
        row["conversion_factor"] = None
        if status == "unavailable":
            row["total"] = None
        row["unit_basis"] = model_unit or row.get("unit_basis") or ""
        return

    model_unit = _model_unit_for_row(row, model_units)
    row["model_unit"] = model_unit
    row["original_unit"] = model_unit
    if model_unit:
        row["model_unit_label"] = display_unit_label(model_unit)
        row["source_unit_known"] = True
        row["unit_provenance"] = "ifc_project_units"
        row["unit_provenance_note"] = (
            "From IFC project unit declarations for this measure family "
            "(per-quantity units are not indexed separately)."
        )
    else:
        row["model_unit_label"] = UNKNOWN_SOURCE_UNIT_LABEL
        row["source_unit_known"] = False
        row["unit_provenance"] = "none"
        row["unit_provenance_note"] = (
            "No IFC project unit declared for this measure; conversion disabled."
        )

    if not model_unit:
        # Honest unknown: show raw total with unknown unit; do not convert.
        row["output_total"] = None
        row["output_unit"] = ""
        row["output_unit_label"] = UNKNOWN_SOURCE_UNIT_LABEL
        row["unit_converted"] = False
        row["conversion_factor"] = None
        row["unit_basis"] = ""
        row["unit_basis_display"] = UNKNOWN_SOURCE_UNIT_LABEL
        row["model_value_hint"] = "Unknown source unit — conversion disabled."
        return

    out_unit = canonicalize_unit_token(output_units.get(family)) or model_unit
    result = convert_quantity(
        model_total=row.get("model_total"),
        model_unit=model_unit,
        output_unit=out_unit,
    )
    row["unit_conversion"] = conversion_provenance_payload(result)
    row["unit_conversion"]["measurement_type"] = str(row.get("measurement_type") or "")
    row["conversion_factor"] = format(result.factor, "f") if result.factor is not None else None

    if not result.ok:
        row["output_total"] = None
        row["output_unit"] = out_unit
        row["output_unit_label"] = display_unit_label(out_unit) if out_unit else "—"
        row["unit_converted"] = False
        if result.error == "missing_model_total":
            row["total"] = None
            if row.get("total_display") not in {"Not available", "Unresolved", "Choose source"}:
                row["total_display"] = "—"
        return

    out_float = result.as_float_output()
    row["output_total"] = out_float
    row["output_unit"] = result.output_unit
    row["output_unit_label"] = display_unit_label(result.output_unit)
    row["unit_converted"] = result.model_unit != result.output_unit
    row["total"] = out_float
    row["total_display"] = format_quantity_display(result.output_total, family=family)
    row["unit_basis"] = result.output_unit
    row["unit_basis_display"] = display_unit_label(result.output_unit)
    if row["unit_converted"] and result.model_total is not None:
        row["model_value_hint"] = (
            f"Model value: {format_quantity_display(result.model_total, family=family)} "
            f"{display_unit_label(result.model_unit)}"
        )
    else:
        row["model_value_hint"] = ""


def apply_output_units_to_qty_prep(
    qty_prep: MutableMapping[str, Any],
    *,
    project: Any,
    user: Any | None,
    session: MutableMapping[str, Any],
) -> dict[str, Any]:
    """Apply session output units + conversion to all prep rows; attach panel.

    REVIEW-08C: class-scoped overrides win over global family defaults per row.
    """
    svc = QuantityOutputUnitsService(project, user, session)
    panel = svc.build_panel()
    model_units = panel["model_units"]
    global_effective = panel["effective"]
    class_units = panel.get("class_units") or {}

    for row_list_key in ("prep_rows", "prep_rows_export"):
        for row in qty_prep.get(row_list_key) or []:
            if not isinstance(row, dict):
                continue
            # Clear lock so each rebuild starts from the measurement-layer total.
            row.pop("_model_total_locked", None)
            row.pop("model_total", None)
            cls = str(row.get("ifc_class") or "").strip()
            if cls and class_units.get(cls):
                output_units = svc.effective_output_units_for_class(cls)
            else:
                output_units = global_effective
            apply_output_conversion_to_row(row, model_units=model_units, output_units=output_units)

    qty_prep["output_units"] = panel
    # SEM-4A readiness: model units known ≠ user-confirmed. Wire real session
    # confirmation (and output overrides) so freeze immutability stays honest.
    confirmation = QuantityUnitConfirmationService(project, user, session).get_confirmation()
    qty_prep["unit_confirmation"] = _compat_unit_confirmation_panel(
        panel,
        confirmation=confirmation,
    )
    qty_prep["session_output_units_note"] = panel["helper"]
    return panel


def _families_with_class_output_override(panel: Mapping[str, Any]) -> set[str]:
    """Return measure families that have at least one class-scoped output override."""
    found: set[str] = set()
    raw = panel.get("class_units") or {}
    if not isinstance(raw, Mapping):
        return found
    for fam_map in raw.values():
        if not isinstance(fam_map, Mapping):
            continue
        for family, token in fam_map.items():
            fam = str(family or "").strip()
            if fam in FAMILIES and str(token or "").strip():
                found.add(fam)
    return found


def _compat_unit_confirmation_panel(
    panel: Mapping[str, Any],
    *,
    confirmation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bridge output-units panel → SEM-4A ``unit_confirmation`` readiness shape.

    Explicit confirmation requires ``_normalize_stored`` (status confirmed/overridden
    + non-empty token). Non-empty / stale / invalid family blobs are ignored.
    Global family overrides (``row.overridden``) and class-scoped output overrides
    also count as user unit decisions. Model units alone stay AVAILABLE.
    ``any_confirmed`` / ``all_available_confirmed`` are derived from row statuses.
    """
    conf = _normalize_stored(confirmation)
    class_overridden_families = _families_with_class_output_override(panel)
    rows: list[dict[str, Any]] = []
    for r in panel.get("rows") or []:
        family = str(r.get("family") or "")
        model_known = bool(r.get("model_unit"))
        explicit = family in conf
        global_override = bool(r.get("overridden"))
        class_override = family in class_overridden_families
        if explicit or global_override or class_override:
            status = "confirmed"
            status_label = "Confirmed"
        elif model_known:
            status = "available"
            status_label = "Model unit"
        else:
            status = "unresolved"
            status_label = "Unresolved"
        rows.append(
            {
                "family": family,
                "label": r["label"],
                "proposed_label": r["model_unit_label"],
                "proposed_token": r["model_unit"],
                "available": model_known,
                "status": status,
                "status_label": status_label,
                "active_token": r["output_unit"],
                "active_label": r["output_unit_label"],
            }
        )
    any_confirmed = any(row["status"] == "confirmed" for row in rows)
    all_available_confirmed = all(
        (not row.get("available")) or row.get("status") == "confirmed" for row in rows
    )
    return {
        "rows": rows,
        "any_confirmed": any_confirmed,
        "all_available_confirmed": all_available_confirmed,
        "helper": panel.get("helper") or "",
        "conflict": False,
    }
