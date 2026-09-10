# takeoff/services/quantity_prep_export.py
"""Quantity Preparation Model export — CSV+JSON ZIP (Slice 5e-1).

Exports the current Generated Preparation Data Model (settings + session
overlays) as an in-memory ZIP. Separate from legacy QTOCache Excel export.
Not BOQ, not cost estimate, not certification, not writeback, not Modify.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from takeoff.services.quantity_prep_row_mapping import (
    MAPPING_FIELD_KEYS,
    ORIGIN_MANUAL_SESSION,
    ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
    field_is_eligible,
    normalize_mapping_field_value,
    sanitize_mapping_value,
)
from takeoff.services.quantity_prep_row_review import sanitize_note

logger = logging.getLogger(__name__)

CONTRACT_VERSION_V1 = "qty-prep-export-v1"
EXPORT_KIND = "quantity_preparation_model"

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_BOUNDARY_LINES: tuple[str, ...] = (
    "Quantity Preparation Model Export",
    "",
    "This archive is a session preparation model snapshot only.",
    "It reflects the current Generated Preparation Data Model, preparation",
    "settings, and session-only row reviews / manual mapping values.",
    "",
    "Not BOQ.",
    "Not QS-certified takeoff.",
    "Not approved, certified, validated, or published quantities.",
    "Not a cost estimate.",
    "Not rates, budget, payment, or procurement.",
    "Not 5D readiness.",
    "Not EVM.",
    "Not IFC writeback.",
    "Not a Modify proposal.",
    "Not Ask / RAG / ML suggestions.",
    "",
    "Manual mapping values and row reviews are session-only.",
    "Configuration drafts save settings only — not generated rows,",
    "row reviews, or manual mapping values.",
    "Package Mapping is a schema field name, not BOQ output.",
    "Schema-backed session mapping metadata is optional and not approved.",
    "Quantities are IFC-reported model units as indexed,",
    "not SI-normalized claims.",
    "",
    f"Contract: {CONTRACT_VERSION_V1}",
    f"Export kind: {EXPORT_KIND}",
)

_SOURCE_ATTR: dict[str, str] = {
    "classification_code": "classification_source",
    "package_boq_mapping": "package_boq_mapping_source",
    "work_package": "work_package_source",
}

_HINT_ATTR: dict[str, str] = {
    "classification_code": "classification_hint",
    "package_boq_mapping": "package_boq_mapping_hint",
    "work_package": "work_package_hint",
}

_MISSING_ATTR: dict[str, str] = {
    "classification_code": "missing_classification",
    "package_boq_mapping": "missing_package",
    "work_package": "missing_work_package",
}

# Overlay origin attrs (package uses package_mapping_*; work uses work_package_mapping_*).
_ROW_ORIGIN_ATTR: dict[str, str] = {
    "classification_code": "classification_mapping_origin",
    "package_boq_mapping": "package_mapping_origin",
    "work_package": "work_package_mapping_origin",
}

_ROW_SCHEMA_BACKED_ATTR: dict[str, str] = {
    "classification_code": "classification_is_schema_backed",
    "package_boq_mapping": "package_mapping_is_schema_backed",
    "work_package": "work_package_is_schema_backed",
}

# Optional additive export metadata keys → overlay row attributes.
_SCHEMA_META_EXPORT: dict[str, tuple[tuple[str, str], ...]] = {
    "classification_code": (
        ("classification_schema_id", "classification_schema_id"),
        ("classification_schema_key", "classification_schema_key"),
        ("classification_node_id", "classification_node_id"),
        ("classification_label", "classification_label"),
    ),
    "package_boq_mapping": (
        ("package_mapping_schema_id", "package_mapping_schema_id"),
        ("package_mapping_schema_key", "package_mapping_schema_key"),
        ("package_mapping_node_id", "package_mapping_node_id"),
        ("package_mapping_label", "package_mapping_label"),
    ),
    "work_package": (
        ("work_package_schema_id", "work_package_schema_id"),
        ("work_package_schema_key", "work_package_schema_key"),
        ("work_package_node_id", "work_package_node_id"),
        ("work_package_label", "work_package_label"),
    ),
}


def boundary_text() -> str:
    """Return BOUNDARY.txt body."""
    return "\n".join(_BOUNDARY_LINES) + "\n"


def safe_project_slug(name: str) -> str:
    """Sanitize project name for download filenames."""
    cleaned = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (name or "project"))
    cleaned = "_".join(cleaned.split()) or "project"
    return cleaned[:80]


def export_filename(*, project_name: str, when: datetime | None = None) -> str:
    """Build qty_prep_<project>_<UTC timestamp>.zip filename."""
    stamp = (when or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"qty_prep_{safe_project_slug(project_name)}_{stamp}.zip"


def neutralize_csv_cell(value: Any) -> str:
    """Escape CSV formula injection and strip unsafe control/HTML characters."""
    text = sanitize_mapping_value(str(value if value is not None else ""))
    # sanitize_mapping_value collapses whitespace; preserve longer notes separately
    if value is not None and not isinstance(value, str):
        text = sanitize_mapping_value(str(value))
    if text and text[0] in _FORMULA_PREFIXES:
        return f"'{text}"
    return text


def neutralize_csv_note(value: Any) -> str:
    """Sanitize review notes for CSV (longer than mapping values)."""
    text = sanitize_note(str(value if value is not None else ""))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if text and text[0] in _FORMULA_PREFIXES:
        return f"'{text}"
    return text


def mapping_field_origin(
    *,
    included: bool,
    source_intent: str,
    value: str,
    session_origin: str = "",
) -> str:
    """Return provenance token for a mapping field.

    When ``session_origin`` is ``manual_session_schema_node`` and the field is a
    filled manual_field value, prefer that over free-text ``manual_session``.
    """
    if not included:
        return "not_applicable"
    if source_intent == "not_mapped":
        return "not_mapped"
    if source_intent == "future_modify_handoff":
        return "deferred_modify"
    if source_intent == "manual_field":
        if not value:
            return "empty"
        origin = (session_origin or "").strip()
        if origin == ORIGIN_MANUAL_SESSION_SCHEMA_NODE:
            return ORIGIN_MANUAL_SESSION_SCHEMA_NODE
        if origin == ORIGIN_MANUAL_SESSION:
            return ORIGIN_MANUAL_SESSION
        return ORIGIN_MANUAL_SESSION
    return "not_applicable"


def session_origin_from_prep_row(row: Mapping[str, Any], field: str) -> str:
    """Resolve overlay session origin for a Quantities mapping field."""
    origin_attr = _ROW_ORIGIN_ATTR.get(field)
    backed_attr = _ROW_SCHEMA_BACKED_ATTR.get(field)
    if not origin_attr:
        return ""
    origin = str(row.get(origin_attr) or "").strip()
    if origin:
        return origin
    if backed_attr and row.get(backed_attr):
        return ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    return ""


def _append_schema_meta(out: dict[str, Any], row: Mapping[str, Any], field: str) -> None:
    """Copy optional schema metadata onto the export row when present."""
    for export_key, row_attr in _SCHEMA_META_EXPORT.get(field) or ():
        val = str(row.get(row_attr) or "").strip()
        if val:
            out[export_key] = val


def serialize_export_row(row: Mapping[str, Any], *, show: Mapping[str, bool]) -> dict[str, Any]:
    """Serialize one prep row for JSON export (post-overlay)."""
    intents_present = {
        field: str(row.get(_SOURCE_ATTR[field]) or "") for field in MAPPING_FIELD_KEYS
    }
    out: dict[str, Any] = {
        "row_key": str(row.get("row_key") or ""),
        "model_group": str(row.get("model_group") or ""),
        "ifc_class": str(row.get("ifc_class") or ""),
        "quantity_source": str(row.get("quantity_source") or ""),
        "quantity_basis": str(row.get("quantity_basis") or ""),
        "unit_basis": str(row.get("unit_basis") or ""),
        "total_quantity_display": str(row.get("total_display") or ""),
        "total_quantity": row.get("total"),
        "computed_status": str(row.get("computed_review_status") or row.get("review_status") or ""),
        "handoff_status": str(row.get("handoff_status") or ""),
        "session_review_status": str(row.get("session_review_status") or ""),
        "session_review_note": sanitize_note(row.get("session_review_note")),
        "missing_quantity_source": bool(row.get("missing_quantity_source")),
        "basis_unresolved": bool(row.get("basis_unresolved")),
    }

    if show.get("type_name"):
        out["type_name"] = str(row.get("type_name") or "")
    if show.get("level_storey"):
        out["level_storey"] = str(row.get("level_storey") or "")
    if show.get("zone"):
        out["zone"] = str(row.get("zone") or "")

    for field in MAPPING_FIELD_KEYS:
        if not show.get(field):
            continue
        source = intents_present[field]
        eligible = field_is_eligible(included=True, source_intent=source)
        value = sanitize_mapping_value(row.get(field)) if eligible else ""
        out[field] = value
        out[f"{field}_source_intent"] = source
        out[f"{field}_hint"] = str(row.get(_HINT_ATTR[field]) or "")
        out[f"{field}_origin"] = mapping_field_origin(
            included=True,
            source_intent=source,
            value=value,
            session_origin=session_origin_from_prep_row(row, field) if eligible else "",
        )
        out[f"missing_{field}"] = bool(row.get(_MISSING_ATTR[field]))
        if eligible and value:
            _append_schema_meta(out, row, field)

    out["manual_mapping_applied"] = bool(row.get("manual_mapping"))
    out["manual_mapping_fields"] = list(row.get("manual_mapping_fields") or [])
    return out


def _prep_config_ref(loaded_config: Any) -> dict[str, str] | None:
    if loaded_config is None:
        return None
    if isinstance(loaded_config, Mapping):
        cfg_id = str(loaded_config.get("id") or loaded_config.get("pk") or "").strip()
        name = str(loaded_config.get("name") or "").strip()
        if cfg_id or name:
            return {"id": cfg_id, "name": name}
        return None
    cfg_id = str(getattr(loaded_config, "pk", "") or getattr(loaded_config, "id", "") or "")
    name = str(getattr(loaded_config, "name", "") or "")
    if not cfg_id and not name:
        return None
    return {"id": cfg_id, "name": name}


def _filter_session_annotations(
    annotations: Mapping[str, Mapping[str, Any]],
    known_keys: set[str],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, value in annotations.items():
        key_s = str(key or "").strip()
        if key_s not in known_keys:
            continue
        if not isinstance(value, Mapping):
            continue
        out[key_s] = {str(k): v for k, v in value.items()}
    return out


def build_export_document(
    *,
    project,
    qty_prep: Mapping[str, Any],
    basis_overrides: Mapping[str, str],
    schema_includes: Mapping[str, bool],
    source_mappings: Mapping[str, str],
    loaded_config: Any,
    review_annotations: Mapping[str, Mapping[str, Any]],
    mapping_annotations: Mapping[str, Mapping[str, Any]],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build preparation_export.json document root."""
    show = dict(qty_prep.get("show") or {})
    rows_in = list(qty_prep.get("prep_rows") or [])
    known_keys = {str(r.get("row_key") or "") for r in rows_in if r.get("row_key")}
    rows_out = [serialize_export_row(r, show=show) for r in rows_in]
    when = generated_at or datetime.now(UTC)

    # Session annotations: only known keys; mapping values only for eligible fields.
    reviews_out = _filter_session_annotations(review_annotations, known_keys)
    mappings_raw = _filter_session_annotations(mapping_annotations, known_keys)
    mappings_out: dict[str, dict[str, str]] = {}
    intents = dict(qty_prep.get("source_mapping_intents") or source_mappings)
    for key, fields in mappings_raw.items():
        cleaned: dict[str, str] = {}
        for field in MAPPING_FIELD_KEYS:
            if field not in fields:
                continue
            if not field_is_eligible(
                included=bool(show.get(field)),
                source_intent=str(intents.get(field) or ""),
            ):
                continue
            norm = normalize_mapping_field_value(fields.get(field))
            val = sanitize_mapping_value(norm.get("value"))
            if val:
                cleaned[field] = val
        if cleaned:
            mappings_out[key] = cleaned

    register = dict(qty_prep.get("unresolved_register") or {})
    # Drop readiness-sounding aliases from export register copy if present —
    # keep only stable keys used by UI.
    register_export = {
        "row_count": int(register.get("row_count") or len(rows_out)),
        "missing_quantity_basis_rule": int(register.get("missing_quantity_basis_rule") or 0),
        "missing_selected_quantity_source": int(
            register.get("missing_selected_quantity_source") or 0
        ),
        "missing_classification": int(register.get("missing_classification") or 0),
        "missing_package_boq_mapping": int(register.get("missing_package_boq_mapping") or 0),
        "missing_work_package": int(register.get("missing_work_package") or 0),
        "eligible_for_modify_handoff": int(register.get("eligible_for_modify_handoff") or 0),
        "not_eligible_for_handoff": int(register.get("not_eligible_for_handoff") or 0),
    }

    return {
        "contract_version": CONTRACT_VERSION_V1,
        "export_kind": EXPORT_KIND,
        "generated_at": when.isoformat().replace("+00:00", "Z"),
        "project": {
            "id": str(getattr(project, "pk", "")),
            "name": str(getattr(project, "name", "") or ""),
        },
        "boundary": {
            "title": "Quantity Preparation Model Export",
            "session_snapshot": True,
            "not_boq": True,
            "not_qs_certified_takeoff": True,
            "not_approved_certified_validated_published": True,
            "not_cost_estimate": True,
            "not_5d_readiness": True,
            "not_evm": True,
            "not_ifc_writeback": True,
            "not_modify_proposal": True,
            "session_annotations_only": True,
            "config_drafts_settings_only": True,
            "package_boq_mapping_is_schema_field_name_only": True,
            "quantities_are_ifc_reported_model_units": True,
        },
        "settings": {
            "basis_rules": dict(basis_overrides),
            "schema_includes": dict(schema_includes),
            "source_mappings": dict(source_mappings),
            "prep_config": _prep_config_ref(loaded_config),
            "prep_row_grain": str(qty_prep.get("prep_row_grain") or "ifc_class"),
        },
        "session_annotations": {
            "row_reviews": reviews_out,
            "manual_mappings": mappings_out,
        },
        "unresolved_register": register_export,
        "rows": rows_out,
    }


def csv_headers(show: Mapping[str, bool]) -> list[str]:
    """Stable CSV headers reflecting included schema fields."""
    headers = [
        "row_key",
        "model_group",
        "ifc_class",
    ]
    if show.get("type_name"):
        headers.append("type_name")
    headers.extend(
        [
            "quantity_basis",
            "quantity_source",
            "unit_basis",
            "total_quantity",
            "computed_status",
            "handoff_status",
            "session_review_status",
            "session_review_note",
        ]
    )
    if show.get("level_storey"):
        headers.append("level_storey")
    if show.get("zone"):
        headers.append("zone")
    for field in MAPPING_FIELD_KEYS:
        if show.get(field):
            headers.append(field)
            headers.append(f"{field}_origin")
    return headers


def build_rows_csv(document: Mapping[str, Any], *, show: Mapping[str, bool]) -> str:
    """Build UTF-8 CSV text for rows.csv."""
    headers = csv_headers(show)
    buf = io.StringIO()
    # UTF-8 BOM helps Excel; still not an XLSX export.
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in document.get("rows") or []:
        flat: dict[str, str] = {}
        for h in headers:
            if h == "total_quantity":
                raw = row.get("total_quantity_display")
                if raw in (None, ""):
                    raw = row.get("total_quantity")
                flat[h] = neutralize_csv_cell(raw)
            elif h == "session_review_note":
                flat[h] = neutralize_csv_note(row.get(h))
            elif h.endswith("_origin"):
                flat[h] = neutralize_csv_cell(row.get(h))
            else:
                flat[h] = neutralize_csv_cell(row.get(h))
        writer.writerow(flat)
    return buf.getvalue()


def build_export_zip_bytes(
    *,
    project,
    qty_prep: Mapping[str, Any],
    basis_overrides: Mapping[str, str],
    schema_includes: Mapping[str, bool],
    source_mappings: Mapping[str, str],
    loaded_config: Any,
    review_annotations: Mapping[str, Mapping[str, Any]],
    mapping_annotations: Mapping[str, Mapping[str, Any]],
    generated_at: datetime | None = None,
) -> tuple[bytes, str, dict[str, Any]]:
    """Return (zip_bytes, filename, json_document)."""
    when = generated_at or datetime.now(UTC)
    document = build_export_document(
        project=project,
        qty_prep=qty_prep,
        basis_overrides=basis_overrides,
        schema_includes=schema_includes,
        source_mappings=source_mappings,
        loaded_config=loaded_config,
        review_annotations=review_annotations,
        mapping_annotations=mapping_annotations,
        generated_at=when,
    )
    show = dict(qty_prep.get("show") or {})
    csv_text = build_rows_csv(document, show=show)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("BOUNDARY.txt", boundary_text())
        zf.writestr(
            "preparation_export.json",
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        )
        zf.writestr("rows.csv", csv_text)
    filename = export_filename(project_name=str(getattr(project, "name", "") or ""), when=when)
    return mem.getvalue(), filename, document


class QuantityPrepExportService:
    """Build a preparation-model ZIP from a shared qty_prep runtime payload."""

    def __init__(self, project, user) -> None:
        self.project = project
        self.user = user

    def build_zip_from_runtime(self, runtime: Mapping[str, Any]) -> dict[str, Any]:
        """Create ZIP bytes from ``build_qty_prep_session_ui`` output."""
        try:
            blob, filename, document = build_export_zip_bytes(
                project=self.project,
                qty_prep=runtime["qty_prep"],
                basis_overrides=runtime["basis_overrides"],
                schema_includes=runtime["schema_includes"],
                source_mappings=runtime["source_mappings"],
                loaded_config=runtime.get("loaded_config"),
                review_annotations=runtime.get("review_annotations") or {},
                mapping_annotations=runtime.get("mapping_annotations") or {},
            )
            return {
                "result": {
                    "content": blob,
                    "filename": filename,
                    "document": document,
                },
                "error": None,
            }
        except Exception as exc:
            logger.exception("qty prep export failed for project %s", self.project.pk)
            return {"result": None, "error": str(exc)}
