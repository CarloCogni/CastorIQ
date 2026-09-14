# takeoff/services/quantity_measurement_settings.py
"""QTO-REVIEW-08 / MEASUREMENT-11 — combined Measurement settings by IFC class.

Groups prep rows by ``ifc_class`` and exposes one surface for:
measurement type + compatible IFC source + source-unit provenance + output unit.

MEASUREMENT-11: class source options come from indexed ``measure_inventory``
across the full working filter scope (including unloaded hierarchy
descendants). Discovery uses the resolver compatibility table — never nested
``inv.get(measurement_type)`` (that key does not exist on flat inventories).

Per-target choices remain authoritative until the user explicitly Applies a
class setting. Does not invent units or rewrite frozen versions.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from takeoff.services.measurement_resolver import (
    COMPATIBLE_SOURCES,
    MEASUREMENT_TYPE_LABELS,
    MEASUREMENT_TYPES,
    inventory_from_aggregate_row,
    inventory_has_source,
    normalize_measurement_type,
)
from takeoff.services.quantity_output_units import QuantityOutputUnitsService
from takeoff.services.quantity_prep_row_measurement import QuantityPrepRowMeasurementService
from takeoff.services.quantity_unit_conversion import (
    FAMILY_COUNT,
    OUTPUT_CHOICES,
    TOKEN_COUNT,
    canonicalize_unit_token,
    family_for_measurement_type,
    output_choices_for_family,
)

logger = logging.getLogger(__name__)


def _unique_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _iter_scope_rows(
    *,
    prep_rows: Sequence[Mapping[str, Any]],
    inventory_rows: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Prefer full inventory/export rows; fall back to visible prep rows."""
    primary = list(inventory_rows or [])
    if primary:
        return [r for r in primary if isinstance(r, dict) and not r.get("is_load_more")]
    return [r for r in prep_rows if isinstance(r, dict) and not r.get("is_load_more")]


def discover_class_source_coverage(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Union compatible sources per measurement type with instance coverage.

    Scans instance-level rows when present; otherwise uses available aggregates.
    Presence includes numeric zero. Never invents sources outside COMPATIBLE_SOURCES.
    """
    instances = [r for r in rows if str(r.get("level") or "") == "instance"]
    scan = instances if instances else list(rows)
    total = len(scan)
    by_mt: dict[str, dict[str, Any]] = {
        mt: {"sources": {}, "total_targets": total} for mt in MEASUREMENT_TYPES
    }
    for row in scan:
        inv = inventory_from_aggregate_row(row)
        if str(row.get("level") or "") == "instance" or row.get("element_count") is not None:
            cov = by_mt["count"]["sources"].setdefault(
                "element_count", {"present": 0, "missing": 0}
            )
            if (
                inventory_has_source(inv, "element_count")
                or str(row.get("level") or "") == "instance"
            ):
                cov["present"] += 1
            else:
                cov["missing"] += 1
        for mt in MEASUREMENT_TYPES:
            if mt == "count":
                continue
            for src in COMPATIBLE_SOURCES.get(mt, ()):
                bucket = by_mt[mt]["sources"].setdefault(src, {"present": 0, "missing": 0})
                if inventory_has_source(inv, src):
                    bucket["present"] += 1
                else:
                    bucket["missing"] += 1
    for _mt, payload in by_mt.items():
        cleaned: dict[str, Any] = {}
        for src, stats in (payload.get("sources") or {}).items():
            if int(stats.get("present") or 0) <= 0:
                continue
            cleaned[src] = {
                "present": int(stats["present"]),
                "missing": int(stats.get("missing") or 0),
                "total": total,
                "partial": int(stats["present"]) < total if total else False,
            }
        payload["sources"] = cleaned
        payload["available"] = [s for s in COMPATIBLE_SOURCES.get(_mt, ()) if s in cleaned]
    return by_mt


def build_class_settings_rows(
    *,
    prep_rows: list[dict[str, Any]],
    inventory_rows: list[dict[str, Any]] | None = None,
    units_panel: dict[str, Any] | None = None,
    class_units: dict[str, dict[str, str]] | None = None,
    global_effective: dict[str, str] | None = None,
    project: Any | None = None,
) -> list[dict[str, Any]]:
    """One settings card per IFC class in the current working filter scope."""
    from takeoff.services.quantity_output_units import (
        UNKNOWN_SOURCE_UNIT_LABEL,
        resolve_original_unit,
    )
    from takeoff.services.quantity_unit_conversion import display_unit_label

    scope = _iter_scope_rows(prep_rows=prep_rows, inventory_rows=inventory_rows)
    visible_by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in prep_rows:
        if not isinstance(row, dict) or row.get("is_load_more"):
            continue
        cls = str(row.get("ifc_class") or "").strip() or "(unknown)"
        visible_by_class[cls].append(row)

    scope_by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scope:
        cls = str(row.get("ifc_class") or "").strip() or "(unknown)"
        scope_by_class[cls].append(row)

    all_classes = sorted(set(scope_by_class) | set(visible_by_class))

    units_by_family: dict[str, dict[str, Any]] = {}
    for urow in (units_panel or {}).get("rows") or []:
        if isinstance(urow, dict) and urow.get("family"):
            units_by_family[str(urow["family"])] = urow

    model_units_map = dict((units_panel or {}).get("model_units") or {})
    class_map = class_units if isinstance(class_units, dict) else {}
    global_eff = global_effective if isinstance(global_effective, dict) else {}

    # Per-measurement-type original unit from shared IFC project-unit path.
    source_units_by_mt: dict[str, dict[str, Any]] = {}
    for mt in MEASUREMENT_TYPES:
        fam = family_for_measurement_type(mt)
        resolved = resolve_original_unit(
            project=project,
            family=fam,
            model_units=model_units_map,
        )
        source_units_by_mt[mt] = {
            "token": resolved.get("token") or "",
            "label": resolved.get("label") or UNKNOWN_SOURCE_UNIT_LABEL,
            "known": bool(resolved.get("known")),
            "note": resolved.get("provenance_note") or "",
            "source": resolved.get("source") or "none",
        }

    out: list[dict[str, Any]] = []
    for cls in all_classes:
        rows_scope = scope_by_class.get(cls) or visible_by_class.get(cls) or []
        rows_visible = visible_by_class.get(cls) or []
        sample_pool = rows_visible or rows_scope
        sample = sample_pool[0] if sample_pool else {}

        instances = [r for r in rows_scope if str(r.get("level") or "") == "instance"]
        types = [r for r in rows_scope if str(r.get("level") or "") == "type"]
        class_nodes = [r for r in rows_scope if str(r.get("level") or "") == "class"]
        if class_nodes:
            element_count = int(class_nodes[0].get("element_count") or 0)
        elif instances:
            element_count = len(instances)
        else:
            element_count = sum(int(r.get("element_count") or 0) for r in rows_scope)
        type_count = len(types)
        if type_count == 0 and class_nodes:
            type_count = len(class_nodes[0].get("type_keys") or [])
        if type_count == 0:
            type_ids = {
                str(r.get("parent_key") or r.get("type_key") or r.get("element_type_id") or "")
                for r in instances
                if (r.get("parent_key") or r.get("type_key") or r.get("element_type_id"))
            }
            type_count = len(type_ids)

        coverage = discover_class_source_coverage(rows_scope)
        compatible: dict[str, list[str]] = {
            mt: list(coverage.get(mt, {}).get("available") or []) for mt in MEASUREMENT_TYPES
        }
        if element_count > 0 and "element_count" not in compatible["count"]:
            compatible["count"] = ["element_count"]

        # Also merge any already-resolved row compatible_sources (session overlays).
        for row in sample_pool:
            mt = normalize_measurement_type(row.get("measurement_type"))
            if not mt:
                continue
            for src in row.get("compatible_sources") or []:
                if src and src not in compatible[mt]:
                    # Only keep resolver-eligible keys.
                    if src in COMPATIBLE_SOURCES.get(mt, ()):
                        compatible[mt].append(str(src))

        source_coverage: dict[str, dict[str, Any]] = {
            mt: dict(coverage.get(mt, {}).get("sources") or {}) for mt in MEASUREMENT_TYPES
        }

        types_present = _unique_preserve(
            [str(r.get("measurement_type") or "") for r in sample_pool if r.get("measurement_type")]
        )
        sources_present = _unique_preserve(
            [
                str(r.get("selected_source") or r.get("ifc_quantity_source") or "")
                for r in sample_pool
                if (r.get("selected_source") or r.get("ifc_quantity_source"))
            ]
        )

        primary_mt = types_present[0] if len(types_present) == 1 else ""
        primary_src = sources_present[0] if len(sources_present) == 1 else ""
        family = family_for_measurement_type(primary_mt) if primary_mt else ""
        mt_unit = source_units_by_mt.get(primary_mt) or {}
        if primary_mt:
            src_known = bool(mt_unit.get("known"))
            src_unit_label = str(mt_unit.get("label") or UNKNOWN_SOURCE_UNIT_LABEL)
            src_unit_note = str(mt_unit.get("note") or "")
        else:
            # No measurement yet — idle; do not claim a sample row's invented label.
            src_known = False
            src_unit_label = "Select a source"
            src_unit_note = (
                "Original unit comes from IFC project unit declarations for the "
                "selected measure family (per-quantity units are not indexed)."
            )

        unit_row = units_by_family.get(family) if family else None
        class_override = (class_map.get(cls) or {}).get(family) if family else ""
        row_out = str(sample.get("output_unit") or "").strip()
        effective_token = (
            class_override
            or row_out
            or global_eff.get(family)
            or ((unit_row or {}).get("output_unit") or "")
        )

        output_by_mt: dict[str, list[dict[str, str]]] = {}
        preferred_by_mt: dict[str, str] = {}
        for mt in MEASUREMENT_TYPES:
            fam = family_for_measurement_type(mt)
            if not fam or fam == FAMILY_COUNT:
                output_by_mt[mt] = []
                preferred_by_mt[mt] = TOKEN_COUNT
                continue
            urow = units_by_family.get(fam) or {}
            output_by_mt[mt] = list(urow.get("choices") or output_choices_for_family(fam) or [])
            preferred_by_mt[mt] = (
                canonicalize_unit_token((class_map.get(cls) or {}).get(fam))
                or canonicalize_unit_token(global_eff.get(fam))
                or canonicalize_unit_token(urow.get("output_unit"))
                or ""
            )

        visible_row_count = len(rows_visible)
        out.append(
            {
                "ifc_class": cls,
                "row_count": element_count or visible_row_count,
                "visible_row_count": visible_row_count,
                "element_count": element_count,
                "type_count": type_count,
                "count_label": (
                    f"{element_count} element{'' if element_count == 1 else 's'}"
                    + (
                        f" · {type_count} type{'' if type_count == 1 else 's'}"
                        if type_count
                        else ""
                    )
                ),
                "measurement_types_present": types_present,
                "sources_present": sources_present,
                "mixed_measurement": len(types_present) > 1,
                "mixed_source": len(sources_present) > 1,
                "primary_measurement_type": primary_mt,
                "primary_selected_source": primary_src,
                "compatible_sources": compatible,
                "source_coverage": source_coverage,
                "source_coverage_json": json.dumps(source_coverage, separators=(",", ":")),
                "source_units_by_mt": source_units_by_mt,
                "source_units_by_mt_json": json.dumps(source_units_by_mt, separators=(",", ":")),
                "source_unit_label": src_unit_label,
                "source_unit_known": src_known,
                "source_unit_note": src_unit_note,
                "source_unit_idle_label": "Select a source",
                "output_unit": effective_token,
                "output_unit_label": (
                    "Count"
                    if family == FAMILY_COUNT
                    else (
                        display_unit_label(effective_token)
                        if effective_token
                        else ((unit_row or {}).get("output_unit_label") or "—")
                    )
                ),
                "output_choices": list((unit_row or {}).get("choices") or []),
                "output_choices_by_measurement": output_by_mt,
                "output_choices_by_mt_json": json.dumps(output_by_mt, separators=(",", ":")),
                "preferred_output_by_measurement": preferred_by_mt,
                "preferred_output_by_mt_json": json.dumps(preferred_by_mt, separators=(",", ":")),
                "output_locked": bool(family == FAMILY_COUNT or (unit_row or {}).get("locked")),
                "class_output_override": bool(class_override),
                "inherited_output": bool(not class_override and family and family != FAMILY_COUNT),
                "measurement_type_labels": dict(MEASUREMENT_TYPE_LABELS),
                "target_keys": _unique_preserve(
                    [
                        str(r.get("measurement_target_key") or "")
                        for r in rows_scope
                        if r.get("measurement_target_key")
                    ]
                ),
            }
        )
    return out


def attach_selected_source_coverage(
    prep_rows: list[dict[str, Any]],
    *,
    inventory_rows: list[dict[str, Any]] | None = None,
) -> None:
    """Persistent selected-source coverage on class/type parents.

    Counts filtered descendant instances with the selected source present.
    Missing is not treated as zero. Partial label e.g. ``649/657 · 8 missing``.
    Full coverage omits the repeated N/N line (empty label).
    """
    scope = _iter_scope_rows(prep_rows=prep_rows, inventory_rows=inventory_rows)
    instances = [r for r in scope if str(r.get("level") or "") == "instance"]
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for inst in instances:
        cls = str(inst.get("ifc_class") or "").strip()
        if cls:
            by_class[cls].append(inst)
        parent = str(inst.get("parent_key") or "").strip()
        if parent:
            by_parent[parent].append(inst)

    for row in prep_rows:
        if not isinstance(row, dict) or row.get("is_load_more"):
            continue
        level = str(row.get("level") or "")
        source = str(row.get("selected_source") or row.get("ifc_quantity_source") or "").strip()
        status = str(row.get("measurement_status") or "")
        for key in (
            "quantity_coverage_present",
            "quantity_coverage_missing",
            "quantity_coverage_total",
            "quantity_coverage_partial",
            "quantity_coverage_label",
        ):
            row.pop(key, None)
        if level not in {"class", "type"}:
            continue
        if not source or status in {"unresolved", "choice_required", "unavailable"}:
            continue
        if source == "element_count":
            total = int(row.get("element_count") or 0)
            row["quantity_coverage_present"] = total
            row["quantity_coverage_missing"] = 0
            row["quantity_coverage_total"] = total
            row["quantity_coverage_partial"] = False
            row["quantity_coverage_label"] = ""
            continue
        if level == "class":
            kids = by_class.get(str(row.get("ifc_class") or "").strip(), [])
        else:
            node = str(row.get("node_key") or "").strip()
            kids = by_parent.get(node, [])
        present = 0
        missing = 0
        for kid in kids:
            inv = inventory_from_aggregate_row(kid)
            if inventory_has_source(inv, source):
                present += 1
            else:
                missing += 1
        total = present + missing
        if total == 0:
            # Fall back to element_count when export instances not in scope yet.
            total = int(row.get("element_count") or 0)
            if total and inventory_has_source(inventory_from_aggregate_row(row), source):
                # Parent inventory present but no child scan — coverage unknown; skip.
                continue
        row["quantity_coverage_present"] = present
        row["quantity_coverage_missing"] = missing
        row["quantity_coverage_total"] = total
        row["quantity_coverage_partial"] = bool(missing and present)
        if missing and present:
            # Concise Status-only label; full coverage needs no repeated N/N line.
            row["quantity_coverage_label"] = f"{present}/{total} · {missing} missing"
            # Do not show unqualified green Resolved when quantities are partial.
            if str(row.get("review_status") or "").strip().lower() in ("", "resolved"):
                row["review_status"] = "Partial"
                row["review_status_display"] = "Partial"
            if str(row.get("computed_review_status") or "").strip().lower() in (
                "",
                "resolved",
            ):
                row["computed_review_status"] = "Partial"
        elif missing:
            row["quantity_coverage_label"] = f"0/{total} · {missing} missing"
        else:
            row["quantity_coverage_label"] = ""


def attach_measurement_settings_to_qty_prep(
    qty_prep: dict[str, Any],
    *,
    project: Any,
    user: Any,
    session: Any,
) -> None:
    """Attach combined settings panel context onto ``qty_prep``."""
    units_svc = QuantityOutputUnitsService(project, user, session)
    units_panel = qty_prep.get("output_units") or {}
    if not units_panel:
        units_panel = units_svc.build_panel()
        qty_prep["output_units"] = units_panel
    inventory_rows = [
        r
        for r in (qty_prep.get("prep_rows_export") or [])
        if isinstance(r, dict) and not r.get("is_load_more")
    ]
    if not inventory_rows:
        inventory_rows = [
            r
            for r in (qty_prep.get("prep_rows") or [])
            if isinstance(r, dict) and not r.get("is_load_more")
        ]
    # Coverage on parents (same filtered descendant scope as totals).
    for key in ("prep_rows", "prep_rows_export"):
        rows = qty_prep.get(key) or []
        if isinstance(rows, list):
            attach_selected_source_coverage(
                [r for r in rows if isinstance(r, dict)],
                inventory_rows=inventory_rows,
            )
    rows = build_class_settings_rows(
        prep_rows=list(qty_prep.get("prep_rows") or []),
        inventory_rows=inventory_rows,
        units_panel=units_panel if isinstance(units_panel, dict) else {},
        class_units=units_svc.get_class_units(),
        global_effective=units_svc.effective_output_units(),
        project=project,
    )
    qty_prep["measurement_settings"] = {
        "class_rows": rows,
        "measurement_types": list(MEASUREMENT_TYPES),
        "measurement_type_labels": dict(MEASUREMENT_TYPE_LABELS),
        "gap_note": (
            "Per-quantity/property units are not indexed separately; "
            "measure families use IFC project unit declarations when present."
        ),
        "helper": (
            "Configure measurement, IFC source, and output unit by IFC class. "
            "Compatible sources come from indexed instance inventories in the "
            "current filter scope (including unloaded hierarchy descendants). "
            "Optional Quantity / Measurement / IFC Source / Unit columns display "
            "the resulting settings — they are not per-row editors."
        ),
    }


def collect_class_apply_scope(
    *,
    prep_rows: Sequence[Mapping[str, Any]],
    inventory_rows: Sequence[Mapping[str, Any]] | None = None,
    hierarchy_tree: Mapping[str, Any] | None = None,
    ifc_class: str = "",
) -> list[dict[str, Any]]:
    """All class/type/instance rows for one IFC class in apply/discovery scope."""
    cls = str(ifc_class or "").strip()
    by_key: dict[str, dict[str, Any]] = {}

    def _add(row: Mapping[str, Any]) -> None:
        if not isinstance(row, dict) or row.get("is_load_more"):
            return
        if cls and str(row.get("ifc_class") or "").strip() != cls:
            return
        mt = str(row.get("measurement_target_key") or "")
        nk = str(row.get("node_key") or "")
        identity = mt or nk or str(row.get("global_id") or "")
        if not identity:
            return
        by_key[identity] = dict(row)

    for row in _iter_scope_rows(prep_rows=prep_rows, inventory_rows=inventory_rows):
        _add(row)
    for row in prep_rows:
        _add(row)

    tree = hierarchy_tree if isinstance(hierarchy_tree, Mapping) else {}
    type_by_key = tree.get("type_by_key") if isinstance(tree.get("type_by_key"), Mapping) else {}
    for class_node in tree.get("classes") or []:
        if not isinstance(class_node, dict):
            continue
        if cls and str(class_node.get("ifc_class") or "").strip() != cls:
            continue
        _add(class_node)
        for tk in class_node.get("type_keys") or []:
            tnode = type_by_key.get(tk) if isinstance(type_by_key, Mapping) else None
            if isinstance(tnode, dict):
                _add(tnode)
        # Instances under types (may duplicate export — keyed unique).
        inst_map = tree.get("instances_by_type_key") or {}
        if isinstance(inst_map, Mapping):
            for tk in class_node.get("type_keys") or []:
                for inst in inst_map.get(tk) or []:
                    if isinstance(inst, dict):
                        _add(inst)
    return list(by_key.values())


def apply_class_settings(
    *,
    project: Any,
    user: Any,
    session: Any,
    ifc_class: str,
    measurement_type: str,
    selected_source: str,
    output_unit: str,
    prep_rows: list[dict[str, Any]],
    known_target_keys: set[str] | None = None,
    inventory_rows: list[dict[str, Any]] | None = None,
    hierarchy_tree: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply measurement (+ optional output unit) to all targets of one IFC class.

    Uses full inventory/export + hierarchy tree so unloaded descendants receive
    the same class choice. Instance, type, and class grains are all written.
    """
    cls = str(ifc_class or "").strip()
    mt = normalize_measurement_type(measurement_type)
    if not cls or mt is None:
        return {"ok": False, "error": "IFC class and measurement type are required."}

    source = str(selected_source or "").strip()
    if mt == "count":
        source = source or "element_count"
    elif not source:
        return {
            "ok": False,
            "error": "Select an IFC source (Net/Gross are explicit choices).",
        }
    allowed = COMPATIBLE_SOURCES.get(mt, ())
    if source not in allowed:
        return {
            "ok": False,
            "error": f"Source {source!r} is not compatible with {mt}.",
        }

    # Validate output unit before mutating measurement choices (JS may fail).
    unit = str(output_unit or "").strip()
    family = family_for_measurement_type(mt)
    token = canonicalize_unit_token(unit) if unit else ""
    if token:
        unit_allowed = OUTPUT_CHOICES.get(family or "", ())
        if family == FAMILY_COUNT:
            if token != TOKEN_COUNT:
                return {
                    "ok": False,
                    "error": (
                        f"Output unit {token!r} is not valid for Count (no dimensional conversion)."
                    ),
                }
        elif token not in unit_allowed:
            return {
                "ok": False,
                "error": f"Output unit {token!r} is not allowed for {mt}.",
            }

    class_rows = collect_class_apply_scope(
        prep_rows=prep_rows,
        inventory_rows=inventory_rows,
        hierarchy_tree=hierarchy_tree,
        ifc_class=cls,
    )
    if not class_rows:
        return {"ok": False, "error": f"No measurement targets for class {cls}."}

    coverage = discover_class_source_coverage(class_rows)
    available = list(coverage.get(mt, {}).get("available") or [])
    if mt != "count" and source not in available:
        return {
            "ok": False,
            "error": (
                f"No compatible {mt} source {source!r} found on indexed "
                f"{cls} instances in the current filter scope."
            ),
        }

    keys = _unique_preserve(
        [
            str(r.get("measurement_target_key") or "")
            for r in class_rows
            if r.get("measurement_target_key")
        ]
    )
    if not keys:
        return {"ok": False, "error": f"No measurement targets for class {cls}."}

    known = set(known_target_keys or [])
    known |= set(keys)

    meas_svc = QuantityPrepRowMeasurementService(project, user, session)
    result = meas_svc.apply_batch(
        measurement_target_keys=keys,
        measurement_type=mt,
        selected_source=source,
        known_target_keys=known,
    )
    if result.get("error"):
        return {"ok": False, "error": result["error"]}

    if token and family and family != FAMILY_COUNT:
        units_svc = QuantityOutputUnitsService(project, user, session)
        uresult = units_svc.apply_class_output_unit(
            ifc_class=cls,
            family=family,
            output_unit=token,
        )
        if uresult.get("error"):
            return {
                "ok": False,
                "error": (
                    f"Measurement saved for {cls}, but output unit failed: {uresult['error']}"
                ),
            }

    src_stats = (coverage.get(mt, {}).get("sources") or {}).get(source) or {}
    applied = (result.get("result") or {}).get("applied") or len(keys)
    element_count = 0
    for r in class_rows:
        if str(r.get("level") or "") == "class" and r.get("element_count"):
            element_count = int(r.get("element_count") or 0)
            break
    if not element_count:
        element_count = len([r for r in class_rows if str(r.get("level") or "") == "instance"])
    if not element_count:
        element_count = sum(int(r.get("element_count") or 0) for r in class_rows)
    return {
        "ok": True,
        "ifc_class": cls,
        "measurement_type": mt,
        "selected_source": source,
        "output_unit": unit,
        "affected_targets": applied,
        "element_count": element_count,
        "source_coverage": {
            "present": int(src_stats.get("present") or 0),
            "missing": int(src_stats.get("missing") or 0),
            "total": int(src_stats.get("total") or 0),
            "partial": bool(src_stats.get("partial")),
        },
    }


def compatible_sources_json_for_template(class_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Serialize compatible sources for lightweight modal JS."""
    out: dict[str, Any] = {}
    for row in class_rows:
        cls = str(row.get("ifc_class") or "")
        out[cls] = {
            "compatible": dict(row.get("compatible_sources") or {}),
            "coverage": dict(row.get("source_coverage") or {}),
            "output_by_mt": dict(row.get("output_choices_by_measurement") or {}),
        }
    return out


def output_choices_for_measurement(measurement_type: str) -> list[dict[str, str]]:
    """Return output unit choice dicts for a measurement type family."""
    family = family_for_measurement_type(measurement_type)
    if not family:
        return []
    return list(output_choices_for_family(family) or [])
