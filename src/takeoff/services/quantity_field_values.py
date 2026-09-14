# takeoff/services/quantity_field_values.py
"""QTO-REVIEW-08C — exact distinct field values for filter suggestions.

IFC-backed fields resolve against indexed entity properties before By Type
aggregation. Derived table fields use current prep-row (post-aggregation) values.
Does not use catalogue sample_values or inject the mixed-cell sentinel.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 40
MAX_PAGE_SIZE = 100
MAX_DISTINCT_COLLECT = 5000

# Generated mixed-cell display for grouped prep rows — not an IFC property value.
_GENERATED_MIXED_SENTINEL = "Multiple values"


def _str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def normalize_page(*, offset: int, limit: int) -> tuple[int, int]:
    """Clamp pagination args."""
    off = max(0, int(offset or 0))
    lim = int(limit or DEFAULT_PAGE_SIZE)
    if lim < 1:
        lim = DEFAULT_PAGE_SIZE
    lim = min(lim, MAX_PAGE_SIZE)
    return off, lim


def _resolve_ifc_file(project: Any, ifc_file: Any | None = None) -> Any | None:
    if ifc_file is not None:
        return ifc_file
    from takeoff.services.ifc_semantic_fields import _resolve_scan_ifc

    return _resolve_scan_ifc(project, None)


def list_entity_distinct_values(
    *,
    project: Any,
    field_key: str,
    selected_classes: Sequence[str] | None = None,
    ifc_file: Any | None = None,
    search: str = "",
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Distinct entity-level values for ``field_key`` before aggregation.

    Skips complex unsupported values. Preserves literal ``Multiple values`` only
    when that string is stored on an entity; does not inject the generated
    mixed-cell sentinel from prep aggregation.
    """
    from ifc_processor.models import IFCEntity
    from takeoff.services.ifc_semantic_fields import _resolve_spatial_names
    from takeoff.services.quantity_entity_filter import (
        coerce_property_text,
        is_entity_level_field,
        resolve_entity_field_value,
    )

    key = _str(field_key)
    off, lim = normalize_page(offset=offset, limit=limit)
    empty = {
        "field_key": key,
        "values": [],
        "total": 0,
        "offset": off,
        "limit": lim,
        "has_more": False,
        "complete": True,
        "source": "entity_index",
        "error": None,
    }
    if not key or not is_entity_level_field(key):
        return {**empty, "error": "Field is not an entity-level IFC field."}

    ifc = _resolve_ifc_file(project, ifc_file)
    if ifc is None:
        return {**empty, "error": "No IFC file available."}

    class_set = {_str(c) for c in (selected_classes or []) if _str(c)}
    q = _str(search).lower()

    qs = (
        IFCEntity.objects.filter(ifc_file=ifc)
        .select_related(
            "element_type",
            "spatial_container__entity",
            "spatial_container__parent__entity",
        )
        .only(
            "ifc_type",
            "properties",
            "element_type__name",
            "spatial_container_id",
            "spatial_container__spatial_type",
            "spatial_container__entity__name",
            "spatial_container__parent_id",
            "spatial_container__parent__spatial_type",
            "spatial_container__parent__entity__name",
        )
        .iterator(chunk_size=1000)
    )

    seen: set[str] = set()
    ordered: list[str] = []
    for entity in qs:
        ifc_class = _str(entity.ifc_type)
        if class_set and ifc_class not in class_set:
            continue
        props = entity.properties if isinstance(entity.properties, dict) else {}
        type_name = ""
        if getattr(entity, "element_type", None) is not None:
            type_name = _str(entity.element_type.name)
        storey_name, container_name = _resolve_spatial_names(entity)
        raw = resolve_entity_field_value(
            field_key=key,
            props=props,
            ifc_class=ifc_class,
            type_name=type_name,
            storey_name=storey_name,
            container_name=container_name,
        )
        text, is_complex = coerce_property_text(raw)
        if is_complex or text == "":
            continue
        # Cap length for display; keep exact text for matching.
        display = text if len(text) <= 240 else text[:240]
        if display in seen:
            continue
        seen.add(display)
        ordered.append(display)
        if len(ordered) >= MAX_DISTINCT_COLLECT:
            break

    ordered.sort(key=lambda s: s.lower())
    if q:
        ordered = [v for v in ordered if q in v.lower()]
    total = len(ordered)
    page = ordered[off : off + lim]
    complete = total < MAX_DISTINCT_COLLECT and not (len(ordered) >= MAX_DISTINCT_COLLECT and not q)
    # If we hit the collect cap before search, completeness is unknown.
    hit_cap = len(seen) >= MAX_DISTINCT_COLLECT
    return {
        "field_key": key,
        "values": page,
        "total": total,
        "offset": off,
        "limit": lim,
        "has_more": (off + lim) < total,
        "complete": bool(complete) and ((not hit_cap) if not q else True),
        "capped": hit_cap,
        "source": "entity_index",
        "error": None,
    }


def list_prep_row_distinct_values(
    *,
    prep_rows: Sequence[Mapping[str, Any]],
    field_key: str,
    search: str = "",
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Distinct values from current working-table (post-aggregation) rows."""
    from takeoff.services.quantity_entity_filter import coerce_property_text

    key = _str(field_key)
    off, lim = normalize_page(offset=offset, limit=limit)
    q = _str(search).lower()
    seen: set[str] = set()
    ordered: list[str] = []
    for row in prep_rows:
        if not isinstance(row, Mapping):
            continue
        raw = row.get(key)
        text, is_complex = coerce_property_text(raw)
        if is_complex or text == "":
            continue
        if text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    ordered.sort(key=lambda s: s.lower())
    if q:
        ordered = [v for v in ordered if q in v.lower()]
    total = len(ordered)
    page = ordered[off : off + lim]
    return {
        "field_key": key,
        "values": page,
        "total": total,
        "offset": off,
        "limit": lim,
        "has_more": (off + lim) < total,
        "complete": True,
        "capped": False,
        "source": "prep_rows",
        "error": None,
    }


def list_field_value_suggestions(
    *,
    project: Any,
    field_key: str,
    selected_classes: Sequence[str] | None = None,
    prep_rows: Sequence[Mapping[str, Any]] | None = None,
    ifc_file: Any | None = None,
    search: str = "",
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Route to entity-index or prep-row distinct values for the field key."""
    from takeoff.services.quantity_entity_filter import is_entity_level_field

    key = _str(field_key)
    if not key:
        off, lim = normalize_page(offset=offset, limit=limit)
        return {
            "field_key": "",
            "values": [],
            "total": 0,
            "offset": off,
            "limit": lim,
            "has_more": False,
            "complete": True,
            "capped": False,
            "source": "none",
            "error": "field_key required",
        }
    if is_entity_level_field(key):
        return list_entity_distinct_values(
            project=project,
            field_key=key,
            selected_classes=selected_classes,
            ifc_file=ifc_file,
            search=search,
            offset=offset,
            limit=limit,
        )
    return list_prep_row_distinct_values(
        prep_rows=prep_rows or [],
        field_key=key,
        search=search,
        offset=offset,
        limit=limit,
    )
