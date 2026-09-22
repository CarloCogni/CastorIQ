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
        "measurement_type": str(row.get("measurement_type") or ""),
        "quantity_source": str(row.get("ifc_quantity_source") or row.get("quantity_source") or ""),
        "quantity_basis": str(row.get("quantity_basis") or ""),
        "model_total": row.get("model_total"),
        "model_unit": str(row.get("model_unit") or ""),
        "model_unit_label": str(row.get("model_unit_label") or ""),
        "output_total": row.get("output_total")
        if row.get("output_total") is not None
        else row.get("total"),
        "output_unit": str(row.get("output_unit") or row.get("unit_basis") or ""),
        "output_unit_label": str(
            row.get("output_unit_label") or row.get("unit_basis_display") or ""
        ),
        "conversion_factor": row.get("conversion_factor"),
        "conversion_version": str(
            (row.get("unit_conversion") or {}).get("version")
            if isinstance(row.get("unit_conversion"), Mapping)
            else ""
        ),
        "unit_basis": str(row.get("output_unit") or row.get("unit_basis") or ""),
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
    # HIERARCHY-09: export each matching instance once (not parent+child doubles).
    rows_in = list(qty_prep.get("prep_rows_export") or qty_prep.get("prep_rows") or [])
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
            "quantities_include_model_and_output_units": True,
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
    """Legacy CSV headers reflecting included schema fields (pre-layout)."""
    headers = [
        "row_key",
        "model_group",
        "ifc_class",
    ]
    if show.get("type_name"):
        headers.append("type_name")
    headers.extend(
        [
            "measurement_type",
            "quantity_basis",
            "quantity_source",
            "model_total",
            "model_unit",
            "output_total",
            "output_unit",
            "conversion_factor",
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


# Visible table column key → CSV field name (Actions omitted).
_LAYOUT_TO_CSV: dict[str, str] = {
    "ifc_class": "ifc_class",
    "name": "type_name",
    "quantity": "total_quantity",
    "measurement": "measurement_type",
    "ifc_source": "quantity_source",
    "unit": "output_unit",
    "status": "computed_status",
    "classification_code": "classification_code",
    "package_boq_mapping": "package_boq_mapping",
    "work_package": "work_package",
}


def csv_headers_from_layout(
    table_columns: list[Mapping[str, Any]] | None,
    *,
    show: Mapping[str, bool],
) -> list[str]:
    """CSV headers following visible table layout, plus schema-included mapping fields.

    Layout columns mirror the working table. Mapping fields that remain included
    via preparation schema (``show``) but are not yet added as columns still
    appear in CSV so session Assign values / origins are not silently dropped.
    Full nested provenance remains in preparation_export.json.
    """
    cols = [c for c in (table_columns or []) if isinstance(c, Mapping)]
    if not cols:
        return csv_headers(show)

    headers: list[str] = []
    seen: set[str] = set()
    for col in cols:
        key = str(col.get("key") or "").strip()
        if not key or key == "actions":
            continue
        if key in _LAYOUT_TO_CSV:
            header = _LAYOUT_TO_CSV[key]
        elif key.startswith(("prop:", "spatial:", "classref:")):
            header = key
        else:
            header = key
        if header in seen:
            continue
        seen.add(header)
        headers.append(header)
        if key in MAPPING_FIELD_KEYS:
            origin = f"{key}_origin"
            if origin not in seen:
                seen.add(origin)
                headers.append(origin)
    # Schema-included mapping fields omitted from the current layout.
    for field in MAPPING_FIELD_KEYS:
        if not show.get(field):
            continue
        if field in seen:
            continue
        seen.add(field)
        headers.append(field)
        origin = f"{field}_origin"
        if origin not in seen:
            seen.add(origin)
            headers.append(origin)
    if "row_key" not in seen:
        headers.insert(0, "row_key")
    return headers


def _prop_cell_display(row: Mapping[str, Any], col_key: str) -> str:
    by_key = row.get("prop_column_by_key")
    if isinstance(by_key, Mapping) and col_key in by_key:
        cell = by_key.get(col_key)
        if isinstance(cell, Mapping):
            return str(cell.get("display") or "")
    for cell in row.get("prop_column_cells") or []:
        if isinstance(cell, Mapping) and str(cell.get("key") or "") == col_key:
            return str(cell.get("display") or "")
    props = row.get("prop_columns") or {}
    if isinstance(props, Mapping) and col_key in props:
        agg = props.get(col_key)
        if isinstance(agg, Mapping):
            return str(agg.get("display") or "")
    return ""


def build_rows_csv(
    document: Mapping[str, Any],
    *,
    show: Mapping[str, bool],
    table_columns: list[Mapping[str, Any]] | None = None,
    prep_rows: list[Mapping[str, Any]] | None = None,
) -> str:
    """Build UTF-8 CSV text for rows.csv (layout-aware when columns provided)."""
    headers = csv_headers_from_layout(table_columns, show=show)
    buf = io.StringIO()
    # UTF-8 BOM helps Excel; still not an XLSX export.
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    doc_rows = list(document.get("rows") or [])
    prep = list(prep_rows or [])
    for idx, row in enumerate(doc_rows):
        flat: dict[str, str] = {}
        prep_row: Mapping[str, Any] = (
            prep[idx] if idx < len(prep) and isinstance(prep[idx], Mapping) else {}
        )
        for h in headers:
            if h.startswith(("prop:", "spatial:", "classref:")):
                flat[h] = neutralize_csv_cell(_prop_cell_display(prep_row, h))
            elif h == "type_name" and h not in row:
                flat[h] = neutralize_csv_cell(prep_row.get("type_name") or row.get("type_name"))
            elif h == "total_quantity":
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
    table_columns = list(qty_prep.get("table_columns") or [])
    # Ensure Name is present in JSON rows when the Name column is visible.
    qty_prep_for_doc: dict[str, Any] = dict(qty_prep)
    show_for_doc = dict(qty_prep.get("show") or {})
    if any(str(c.get("key")) == "name" for c in table_columns):
        show_for_doc["type_name"] = True
        qty_prep_for_doc["show"] = show_for_doc
    document = build_export_document(
        project=project,
        qty_prep=qty_prep_for_doc,
        basis_overrides=basis_overrides,
        schema_includes=schema_includes,
        source_mappings=source_mappings,
        loaded_config=loaded_config,
        review_annotations=review_annotations,
        mapping_annotations=mapping_annotations,
        generated_at=when,
    )
    layout = qty_prep.get("table_layout") or {}
    document["visible_layout"] = {
        "col_order": list(
            layout.get("order") or [c.get("key") for c in table_columns if c.get("key")]
        ),
        "csv_follows_visible_layout": True,
        "provenance_in_json": True,
        "column_calculations": dict(qty_prep.get("column_calculations") or {}),
        "col_calc": str(qty_prep.get("col_calc_param") or ""),
        "hierarchy_sort": dict(qty_prep.get("hierarchy_sort") or {}),
    }
    show = show_for_doc
    csv_text = build_rows_csv(
        document,
        show=show,
        table_columns=table_columns,
        prep_rows=list(qty_prep.get("prep_rows") or []),
    )

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
