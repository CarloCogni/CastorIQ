# takeoff/services/quantity_prep_runtime.py
"""Shared Quantity Preparation runtime builder (page + export).

Builds the same Generated Preparation Data Model the Quantities page shows,
including GET/draft settings and session review/mapping overlays.
Not BOQ, not cost, not writeback.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

from classification.services.quantity_mapping_selectors import (
    get_mapping_selector_options,
)
from takeoff.services.ifc_semantic_fields import IfcSemanticFieldService
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_prep_config import (
    PREP_CONFIG_QUERY_PARAM,
    QuantityPrepConfigService,
)
from takeoff.services.quantity_prep_row_mapping import (
    QuantityPrepRowMappingService,
    apply_session_mapping_values_to_ui,
)
from takeoff.services.quantity_prep_row_review import (
    QuantityPrepRowReviewService,
    apply_session_reviews_to_ui,
)
from takeoff.services.quantity_preparation_ui import (
    build_preparation_ui,
    parse_basis_overrides_from_query,
    parse_schema_includes_from_query,
    parse_source_mappings_from_query,
)

logger = logging.getLogger(__name__)


def resolve_prep_settings_from_query(
    project,
    user,
    query: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve basis/schema/source (+ optional draft meta) from a GET-like query."""
    loaded_config = None
    load_error = None

    if QuantityPrepConfigService.query_has_session_overrides(query):
        basis_overrides = parse_basis_overrides_from_query(query)
        schema_includes = parse_schema_includes_from_query(query)
        source_mappings = parse_source_mappings_from_query(query)
    elif query.get(PREP_CONFIG_QUERY_PARAM):
        loaded = QuantityPrepConfigService(project, user).load_runtime(
            query.get(PREP_CONFIG_QUERY_PARAM)
        )
        if loaded.get("error"):
            load_error = loaded["error"]
            basis_overrides = parse_basis_overrides_from_query({})
            schema_includes = parse_schema_includes_from_query({})
            source_mappings = parse_source_mappings_from_query({})
        else:
            loaded_config = loaded.get("result")
            basis_overrides = loaded["basis_overrides"]
            schema_includes = loaded["schema_includes"]
            source_mappings = loaded["source_mappings"]
    else:
        basis_overrides = parse_basis_overrides_from_query(query)
        schema_includes = parse_schema_includes_from_query(query)
        source_mappings = parse_source_mappings_from_query(query)

    return {
        "basis_overrides": basis_overrides,
        "schema_includes": schema_includes,
        "source_mappings": source_mappings,
        "loaded_config": loaded_config,
        "load_error": load_error,
    }


def build_qty_prep_session_ui(
    *,
    project,
    user,
    session: MutableMapping[str, Any],
    query: Mapping[str, Any],
    ifc_file=None,
) -> dict[str, Any]:
    """Build qty_prep matching QTOView (settings + mapping overlay + review overlay).

    When ``ifc_file`` is set, quantities and semantic enrichment use that pinned
    source only (editable-table reopen). Otherwise the latest completed IFC.
    """
    from takeoff.services.ifc_semantic_fields import parse_semantic_query
    from takeoff.services.quantity_entity_filter import (
        build_entity_predicate,
        compose_entity_predicate,
        infer_value_type,
        is_entity_level_field,
        parse_selected_classes,
    )
    from takeoff.services.quantity_table_layout import (
        apply_layout_to_qty_prep,
        build_layout_column_descriptors,
        inject_sem_cols_into_query,
        parse_table_layout,
    )

    # REVIEW-08: resolve visible column layout; drive sem_cols from col_order.
    layout_preview = parse_table_layout(query)
    query_for_sem = inject_sem_cols_into_query(query, layout_preview)
    selected_classes = parse_selected_classes(query)

    # DYNAMIC-07 / REVIEW-08: class scope ± property before By Type aggregation.
    sem_params = parse_semantic_query(query_for_sem)
    field_predicate = None
    field_key = str(sem_params.get("field") or "")
    op = str(sem_params.get("op") or "eq")
    value = str(sem_params.get("value") or "")
    if field_key and is_entity_level_field(field_key):
        src = ""
        if field_key.startswith("prop:"):
            from takeoff.services.ifc_semantic_fields import source_property_from_column_key

            src = source_property_from_column_key(field_key) or ""
        value_type = str(
            query_for_sem.get("semantic_value_type") or ""
        ).strip() or infer_value_type(
            [value] if value else [],
            source_property=src,
        )
        if field_key in {"ifc_class", "type_name"} or field_key.startswith("spatial:"):
            value_type = "text"
        field_predicate = build_entity_predicate(
            field_key=field_key,
            op=op,
            value=value,
            value_type=value_type,
        )
    entity_predicate = compose_entity_predicate(
        selected_classes=selected_classes,
        field_predicate=field_predicate,
    )
    entity_filter_applied = entity_predicate is not None

    settings = resolve_prep_settings_from_query(project, user, query)
    # PERF-15A: skip unrestricted ModelQuantities scan. Hierarchy/v2 needs only
    # available_classes (all classes on the IFC, including those outside the
    # active filter) and a class-grain baseline_row_count. Full unrestricted MQ
    # aggregates are not consumed after hierarchy attaches.
    from takeoff.services.quantity_hierarchy import (
        apply_hierarchy_offset_query,
        attach_hierarchy_to_qty_prep,
        build_quantity_hierarchy,
        count_unrestricted_hierarchy_elements,
        list_indexed_ifc_classes,
        load_expanded_keys,
        save_expanded_keys,
    )

    available_classes = list_indexed_ifc_classes(project=project, ifc_file=ifc_file)
    baseline_row_count = len(available_classes)

    quantities = ModelQuantitiesService(project).build(
        ifc_file=ifc_file,
        entity_predicate=entity_predicate,
    )

    qty_prep = build_preparation_ui(
        quantities,
        basis_overrides=settings["basis_overrides"],
        schema_includes=settings["schema_includes"],
        source_mappings=settings["source_mappings"],
    )
    qty_prep["baseline_row_count"] = baseline_row_count
    qty_prep["selected_classes"] = list(selected_classes)
    qty_prep["available_classes"] = available_classes
    qty_prep["baseline_mq_skipped"] = True

    # HIERARCHY-09: Class → Type → Instance presentation (filter already applied).
    from takeoff.services.quantity_prep_row_measurement import (
        attach_measurement_defaults_to_prep_rows,
    )

    hierarchy = build_quantity_hierarchy(
        project=project,
        ifc_file=ifc_file,
        entity_predicate=entity_predicate,
        basis_overrides=settings["basis_overrides"],
    )
    expanded = load_expanded_keys(session, project.pk)
    tech_to_group = hierarchy.get("technical_to_group_key") or {}
    if tech_to_group:
        from takeoff.services.quantity_engineering_groups import (
            remap_expanded_keys_for_engineering_groups,
        )

        expanded = remap_expanded_keys_for_engineering_groups(
            set(expanded), technical_to_group=tech_to_group
        )
    # Query can request expand-all / explicit keys without persisting until Save.
    expand_param = str(query.get("hierarchy_expand") or "").strip()
    if expand_param == "all":
        # Expand class rows only — type/instance children stay paged (no unbounded DOM).
        expanded = {
            str(c.get("node_key") or "")
            for c in hierarchy.get("classes") or []
            if c.get("node_key")
        }
        save_expanded_keys(session, project.pk, sorted(expanded))
    elif expand_param == "none":
        expanded = set()
        save_expanded_keys(session, project.pk, [])
    else:
        from takeoff.services.quantity_hierarchy import prune_expanded_on_collapse

        has_expanded_param = False
        if hasattr(query, "__contains__"):
            try:
                has_expanded_param = "hierarchy_expanded" in query
            except Exception:
                has_expanded_param = False
        if has_expanded_param:
            raw_exp = str(query.get("hierarchy_expanded") or "")
            expanded = {p.strip() for p in raw_exp.split(",") if p.strip().startswith("hn1|")}
        collapse_key = str(query.get("hierarchy_collapse") or "").strip()
        if collapse_key.startswith("hn1|"):
            expanded = prune_expanded_on_collapse(expanded, collapse_key)
        if has_expanded_param or collapse_key.startswith("hn1|"):
            save_expanded_keys(session, project.pk, sorted(expanded))

    type_loaded, instance_offsets = apply_hierarchy_offset_query(session, project.pk, query)
    if tech_to_group and instance_offsets:
        remapped: dict[str, int] = {}
        for key, val in instance_offsets.items():
            dest = str(tech_to_group.get(key) or key)
            remapped[dest] = max(int(remapped.get(dest) or 0), int(val))
        instance_offsets = remapped
    attach_hierarchy_to_qty_prep(
        qty_prep,
        hierarchy=hierarchy,
        expanded=expanded,
        type_loaded=type_loaded,
        instance_offsets=instance_offsets,
    )
    if isinstance(qty_prep.get("hierarchy"), dict):
        qty_prep["hierarchy"]["type_loaded"] = dict(type_loaded)
        qty_prep["hierarchy"]["instance_offsets"] = dict(instance_offsets)
    # PERF-15A: unrestricted baseline footnote without a second hierarchy scan.
    # Unrestricted matching_elements == indexed_entities on this IFC (every
    # indexed entity is hierarchy-eligible when predicate is None). Prefer the
    # count already computed on the filtered build; fall back to a scoped COUNT.
    if entity_predicate is not None:
        counts = hierarchy.get("counts") or {}
        if "indexed_entities" in counts:
            qty_prep["hierarchy_baseline_elements"] = int(counts.get("indexed_entities") or 0)
        else:
            qty_prep["hierarchy_baseline_elements"] = count_unrestricted_hierarchy_elements(
                project=project,
                ifc_file=ifc_file,
            )
    else:
        qty_prep["hierarchy_baseline_elements"] = int(
            (hierarchy.get("counts") or {}).get("matching_elements") or 0
        )
    # Re-attach measurement defaults per hierarchy level.
    for row in qty_prep.get("prep_rows") or []:
        if row.get("is_load_more"):
            continue
        level = str(row.get("level") or "")
        grain = "ifc_class" if level == "class" else ("type" if level == "type" else "instance")
        if level == "instance":
            row["measurement_target_key"] = row.get("measurement_target_key") or ""
            from takeoff.services.quantity_prep_row_measurement import (
                apply_resolution_fields,
                legacy_default_choice,
            )

            mtype, source = legacy_default_choice(row)
            apply_resolution_fields(
                row,
                measurement_type=mtype,
                selected_source=source,
                session_owned=False,
            )
        else:
            attach_measurement_defaults_to_prep_rows([row], grain=grain)

    apply_layout_to_qty_prep(qty_prep, query)

    mapping_svc = QuantityPrepRowMappingService(project, user, session)
    mapping_annotations = mapping_svc.get_annotations()
    apply_session_mapping_values_to_ui(qty_prep, mapping_annotations)
    if qty_prep.get("prep_rows_export"):
        # Must pass show/intents — bare {"prep_rows": …} skips every field (show={}).
        apply_session_mapping_values_to_ui(
            {
                "prep_rows": qty_prep["prep_rows_export"],
                "show": qty_prep.get("show") or {},
                "source_mapping_intents": qty_prep.get("source_mapping_intents") or {},
                "prep_row_grain": "instance",
            },
            mapping_annotations,
        )

    from takeoff.services.quantity_prep_row_measurement import (
        QuantityPrepRowMeasurementService,
        apply_session_measurements_to_ui,
    )

    measurement_svc = QuantityPrepRowMeasurementService(project, user, session)
    measurement_choices = measurement_svc.get_choices()
    apply_session_measurements_to_ui(qty_prep, measurement_choices)
    if qty_prep.get("prep_rows_export"):
        apply_session_measurements_to_ui(
            {"prep_rows": qty_prep["prep_rows_export"]}, measurement_choices
        )

    # C3a/C3b: selector packs for drawer UI + enrich eligible field descriptors.
    selectors = get_mapping_selector_options(project)
    qty_prep["mapping_selectors"] = selectors
    for item in qty_prep.get("manual_mapping_eligible_fields") or []:
        key = str(item.get("key") or "")
        pack = selectors.get(key) or {}
        item["selector"] = pack
        item["has_schema_nodes"] = bool(pack.get("schema_found") and pack.get("nodes"))

    review_svc = QuantityPrepRowReviewService(project, user, session)
    review_annotations = review_svc.get_annotations()
    apply_session_reviews_to_ui(qty_prep, review_annotations)
    if qty_prep.get("prep_rows_export"):
        apply_session_reviews_to_ui({"prep_rows": qty_prep["prep_rows_export"]}, review_annotations)

    # IFC-SEM-1 / DYNAMIC-07: enrichment + catalogue; entity filter already applied upstream.
    IfcSemanticFieldService(project, user).apply_to_qty_prep(
        qty_prep,
        query_for_sem,
        ifc_file=ifc_file,
        entity_predicate=entity_predicate,
        entity_filter_applied=entity_filter_applied,
    )

    # Refresh layout descriptors after property column meta is known.
    layout = apply_layout_to_qty_prep(qty_prep, query)
    sem = qty_prep.get("semantic_filters") or {}
    qty_prep["table_columns"] = build_layout_column_descriptors(
        layout,
        selected_prop_column_meta=sem.get("selected_prop_column_meta") or [],
    )
    # Keep sem_cols_param aligned with layout for URL echo.
    if isinstance(sem, dict):
        sem["sem_cols_param"] = ",".join(layout.get("sem_cols") or [])
        sem["selected_prop_columns"] = list(layout.get("sem_cols") or [])
    for bucket in ("prep_rows", "prep_rows_export"):
        for row in qty_prep.get(bucket) or []:
            if not isinstance(row, dict):
                continue
            by_key: dict[str, Any] = {}
            for cell in row.get("prop_column_cells") or []:
                if isinstance(cell, dict) and cell.get("key"):
                    by_key[str(cell["key"])] = cell
            for key, agg in (row.get("prop_columns") or {}).items():
                if key in by_key or not isinstance(agg, dict):
                    continue
                by_key[str(key)] = {
                    "key": key,
                    "display": agg.get("display") or "—",
                    "status": agg.get("status") or "missing",
                    "detail_values": list(agg.get("detail_values") or []),
                }
            for key in layout.get("sem_cols") or []:
                k = str(key)
                if k and k not in by_key:
                    by_key[k] = {"key": k, "display": "—", "status": "missing"}
            row["prop_column_by_key"] = by_key

    from takeoff.services.quantity_hierarchy import apply_additive_qto_hierarchy_cells

    apply_additive_qto_hierarchy_cells(qty_prep)

    # COLUMNS-10: parent calculations from matching leaf instances.
    from takeoff.services.quantity_column_calc import (
        apply_column_calculations,
        parse_col_calc,
    )

    explicit_calc = parse_col_calc(query)
    add_calc = str(query.get("col_calc_add") or "").strip()
    if add_calc and ":" in add_calc:
        ck, op = add_calc.rsplit(":", 1)
        ck, op = ck.strip(), op.strip().lower()
        if ck and op:
            explicit_calc[ck] = op
    apply_column_calculations(qty_prep, col_calc=explicit_calc)

    # COLUMNS-10: sibling-stable sort, then re-flatten visible rows only.
    from takeoff.services.quantity_hierarchy import flatten_visible_hierarchy_rows
    from takeoff.services.quantity_hierarchy_sort import (
        apply_hierarchy_sort_to_tree,
        parse_hierarchy_sort,
    )

    hsort = parse_hierarchy_sort(query)
    qty_prep["hierarchy_sort"] = hsort

    # COLUMNS-10B: header menus after calc+sort state resolved.
    from takeoff.services.quantity_column_calc import attach_column_header_menus

    attach_column_header_menus(qty_prep, query=query)

    tree = qty_prep.get("_hierarchy_tree")
    if (
        isinstance(tree, dict)
        and hsort.get("key")
        and (qty_prep.get("hierarchy") or {}).get("enabled")
    ):
        lookup: dict[str, dict] = {}
        for bucket in ("prep_rows", "prep_rows_export"):
            for r in qty_prep.get(bucket) or []:
                if isinstance(r, dict) and r.get("node_key"):
                    lookup[str(r["node_key"])] = r
        apply_hierarchy_sort_to_tree(
            tree,
            column_key=hsort["key"],
            direction=hsort["dir"],
            row_lookup=lookup,
        )
        hier_meta = qty_prep.get("hierarchy") or {}
        expanded = set(hier_meta.get("expanded_keys") or [])
        # Preserve prior load-more offsets from hierarchy presentation if stored
        type_loaded = hier_meta.get("type_loaded") or {}
        instance_offsets = hier_meta.get("instance_offsets") or {}
        visible = flatten_visible_hierarchy_rows(
            tree,
            expanded=expanded,
            type_loaded=type_loaded if isinstance(type_loaded, dict) else {},
            instance_offsets=instance_offsets if isinstance(instance_offsets, dict) else {},
        )
        # Re-attach enriched cells onto resorted visible rows
        for row in visible:
            if not isinstance(row, dict) or row.get("is_load_more"):
                continue
            prior = lookup.get(str(row.get("node_key") or ""))
            if not prior:
                continue
            for field in (
                "prop_column_by_key",
                "prop_columns",
                "prop_column_cells",
                "classification_code",
                "package_boq_mapping",
                "work_package",
                "review_status",
                "review_status_display",
                "computed_review_status",
                "handoff_status",
                "eligible_for_handoff",
                "ready_for_handoff",
                "total",
                "total_display",
                "output_unit_label",
                "model_unit_label",
                "measurement_status",
                "semantic_category",
                "semantic_family",
            ):
                if field in prior:
                    row[field] = prior[field]
            for k, cell in (prior.get("prop_column_by_key") or {}).items():
                row[k] = (cell or {}).get("display") if isinstance(cell, dict) else prior.get(k)
        qty_prep["prep_rows"] = visible
        # Sort re-flattens from tree nodes that lack session measurement overlays.
        # Re-apply choices so resolution → conversion → display (apply_output_units
        # below) matches Apply / unsorted Open. Without this, measurement_type is
        # dropped and Unit becomes "—" with an unformatted raw Quantity.
        if measurement_choices:
            apply_session_measurements_to_ui(qty_prep, measurement_choices)

    # Toast payload when a column was just added (consumed by template).
    added_key = str(query.get("col_order_add") or "").strip()
    if added_key:
        label = added_key
        for col in qty_prep.get("table_columns") or []:
            if isinstance(col, dict) and col.get("key") == added_key:
                label = str(col.get("label") or added_key)
                break
        qty_prep["column_just_added"] = {"key": added_key, "label": label}

    # UNIT-03: convert from raw model totals using session output units (before page slice).
    from takeoff.services.quantity_output_units import apply_output_units_to_qty_prep

    apply_output_units_to_qty_prep(qty_prep, project=project, user=user, session=session)

    # REVIEW-08 C: combined Measurement settings panel (class-scoped).
    from takeoff.services.quantity_measurement_settings import (
        attach_measurement_settings_to_qty_prep,
    )

    attach_measurement_settings_to_qty_prep(qty_prep, project=project, user=user, session=session)

    # SCALE-1A: paginate filtered prep rows for DOM; keep full prep_rows for freeze.
    from takeoff.services.quantity_prep_pagination import apply_prep_pagination_to_qty_prep

    apply_prep_pagination_to_qty_prep(qty_prep, query)

    # SEM-4A: 5D semantic source readiness (read-only; no profile persistence).
    from takeoff.services.quantity_semantic_profile import apply_semantic_profile_to_qty_prep

    apply_semantic_profile_to_qty_prep(
        qty_prep,
        project=project,
        unit_confirmation=qty_prep.get("unit_confirmation") or {},
    )

    return {
        "quantities": quantities,
        "qty_prep": qty_prep,
        "basis_overrides": settings["basis_overrides"],
        "schema_includes": settings["schema_includes"],
        "source_mappings": settings["source_mappings"],
        "loaded_config": settings["loaded_config"],
        "load_error": settings["load_error"],
        "mapping_annotations": mapping_annotations,
        "review_annotations": review_annotations,
        "measurement_choices": measurement_choices,
    }
