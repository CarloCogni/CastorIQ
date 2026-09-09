# takeoff/services/ifc_semantic_fields.py
"""IFC-SEM-1 — read-only semantic field discovery and prep-row enrichment.

Exposes preparation-native fields plus high-coverage entity property hints
(Other.Category / Other.Family) for Quantities filtering before batch mapping.

Not writeback, not Modify, not BOQ/cost, no migrations, no S2 contract change.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

SAMPLE_VALUE_CAP = 20
PREP_NATIVE_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "key": "ifc_class",
        "label": "IFC Class",
        "source": "prep_row",
        "source_property": None,
        "is_filterable": True,
        "is_sortable": True,
    },
    {
        "key": "type_name",
        "label": "Type Name",
        "source": "prep_row",
        "source_property": None,
        "is_filterable": True,
        "is_sortable": True,
    },
    {
        "key": "quantity_basis",
        "label": "Quantity Basis",
        "source": "prep_row",
        "source_property": None,
        "is_filterable": True,
        "is_sortable": True,
    },
    {
        "key": "quantity_source",
        "label": "Quantity Source",
        "source": "prep_row",
        "source_property": None,
        "is_filterable": True,
        "is_sortable": True,
    },
    {
        "key": "element_count",
        "label": "Element Count",
        "source": "prep_row",
        "source_property": None,
        "is_filterable": False,
        "is_sortable": True,
    },
)

# High-coverage flat keys already present on IFCEntity.properties for the pilot.
ENTITY_HINT_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "key": "semantic_category",
        "label": "Category",
        "source": "ifc_property",
        "source_property": "Other.Category",
        "is_filterable": True,
        "is_sortable": True,
    },
    {
        "key": "semantic_family",
        "label": "Family",
        "source": "ifc_property",
        "source_property": "Other.Family",
        "is_filterable": True,
        "is_sortable": True,
    },
)

SEMANTIC_QUERY_FIELD = "semantic_field"
SEMANTIC_QUERY_VALUE = "semantic_value"
SEMANTIC_QUERY_SORT = "semantic_sort"
SEMANTIC_QUERY_SORT_DIR = "semantic_sort_dir"


def _str_val(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _grain_key(ifc_class: str, type_name: str) -> tuple[str, str]:
    return (_str_val(ifc_class), _str_val(type_name))


def _majority(counter: Counter[str]) -> str:
    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def _latest_completed_ifc(project: Any) -> Any | None:
    from ifc_processor.models import IFCFile

    return (
        IFCFile.objects.filter(project=project, status="completed").order_by("-created_at").first()
    )


def enrich_prep_rows_with_entity_semantics(
    project: Any,
    prep_rows: Sequence[MutableMapping[str, Any]],
) -> dict[str, Any]:
    """Attach majority Category/Family hints onto prep rows (read-only).

    Returns enrichment meta for discovery UI. Never writes the database.
    """
    rows = list(prep_rows)
    meta: dict[str, Any] = {
        "enriched": False,
        "entity_count_scanned": 0,
        "hint_coverage": {f["key"]: 0 for f in ENTITY_HINT_FIELDS},
        "property_sets_indexed_on_prep": False,
        "helper": (
            "No indexed IFC property-set columns are available on preparation rows yet. "
            "Current filters use preparation fields (class, type, basis, source)."
        ),
    }
    if not rows:
        return meta

    for row in rows:
        row.setdefault("semantic_category", "")
        row.setdefault("semantic_family", "")

    ifc = _latest_completed_ifc(project)
    if ifc is None:
        return meta

    from ifc_processor.models import IFCEntity

    # Counters keyed by (ifc_class, type_name) and by class-only fallback.
    by_grain_cat: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    by_grain_fam: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    by_class_cat: dict[str, Counter[str]] = defaultdict(Counter)
    by_class_fam: dict[str, Counter[str]] = defaultdict(Counter)

    scanned = 0
    qs = (
        IFCEntity.objects.filter(ifc_file=ifc)
        .select_related("element_type")
        .only("ifc_type", "properties", "element_type__name")
        .iterator(chunk_size=1000)
    )
    for entity in qs:
        scanned += 1
        props = entity.properties if isinstance(entity.properties, dict) else {}
        cat = _str_val(props.get("Other.Category"))
        fam = _str_val(props.get("Other.Family"))
        ifc_class = _str_val(entity.ifc_type)
        type_name = ""
        if getattr(entity, "element_type", None) is not None:
            type_name = _str_val(entity.element_type.name)
        grain = _grain_key(ifc_class, type_name)
        if cat:
            by_grain_cat[grain][cat] += 1
            by_class_cat[ifc_class][cat] += 1
        if fam:
            by_grain_fam[grain][fam] += 1
            by_class_fam[ifc_class][fam] += 1

    cat_cov = 0
    fam_cov = 0
    for row in rows:
        ifc_class = _str_val(row.get("ifc_class"))
        type_name = _str_val(row.get("type_name"))
        grain = _grain_key(ifc_class, type_name)
        cat = _majority(by_grain_cat.get(grain) or Counter())
        fam = _majority(by_grain_fam.get(grain) or Counter())
        if not cat:
            cat = _majority(by_class_cat.get(ifc_class) or Counter())
        if not fam:
            fam = _majority(by_class_fam.get(ifc_class) or Counter())
        row["semantic_category"] = cat
        row["semantic_family"] = fam
        if cat:
            cat_cov += 1
        if fam:
            fam_cov += 1

    meta["enriched"] = cat_cov > 0 or fam_cov > 0
    meta["entity_count_scanned"] = scanned
    meta["hint_coverage"] = {
        "semantic_category": cat_cov,
        "semantic_family": fam_cov,
    }
    if meta["enriched"]:
        meta["helper"] = (
            "Category and Family come from indexed IFC entity properties "
            "(Other.Category / Other.Family), aggregated to preparation rows. "
            "Arbitrary property-set columns are not selectable yet."
        )
        meta["property_sets_indexed_on_prep"] = False
    logger.debug(
        "ifc semantic enrich project=%s scanned=%s cat_rows=%s fam_rows=%s",
        getattr(project, "pk", None),
        scanned,
        cat_cov,
        fam_cov,
    )
    return meta


def _distinct_samples(rows: Sequence[Mapping[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for row in rows:
        raw = row.get(key)
        if key == "element_count":
            text = _str_val(raw)
        else:
            text = _str_val(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
        if len(values) >= SAMPLE_VALUE_CAP:
            break
    return values


def discover_semantic_fields(
    project: Any,
    prep_rows: Sequence[Mapping[str, Any]],
    *,
    enrichment_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return safe field descriptors for Quantities semantic filters."""
    rows = list(prep_rows)
    total = len(rows)
    fields: list[dict[str, Any]] = []

    for spec in PREP_NATIVE_FIELDS:
        key = str(spec["key"])
        samples = _distinct_samples(rows, key)
        coverage = sum(1 for r in rows if _str_val(r.get(key)))
        if key == "element_count":
            coverage = sum(1 for r in rows if r.get(key) is not None)
        if coverage <= 0 and key != "ifc_class":
            # Still expose empty type_name etc. only when useful samples exist.
            if not samples and key in {"type_name", "quantity_source"}:
                continue
        fields.append(
            {
                "key": key,
                "label": spec["label"],
                "source": spec["source"],
                "source_property": spec["source_property"],
                "coverage": coverage if key != "ifc_class" else total,
                "sample_values": samples,
                "is_filterable": bool(spec["is_filterable"]),
                "is_sortable": bool(spec["is_sortable"]),
            }
        )

    meta = dict(enrichment_meta or {})
    for spec in ENTITY_HINT_FIELDS:
        key = str(spec["key"])
        coverage = int((meta.get("hint_coverage") or {}).get(key) or 0)
        if coverage <= 0:
            continue
        samples = _distinct_samples(rows, key)
        fields.append(
            {
                "key": key,
                "label": spec["label"],
                "source": spec["source"],
                "source_property": spec["source_property"],
                "coverage": coverage,
                "sample_values": samples,
                "is_filterable": True,
                "is_sortable": True,
            }
        )

    filterable = [f for f in fields if f.get("is_filterable")]
    return {
        "fields": fields,
        "filterable_fields": filterable,
        "prep_row_total": total,
        "helper": meta.get("helper")
        or (
            "No indexed IFC property-set columns are available on preparation rows yet. "
            "Current filters use preparation fields (class, type, basis, source)."
        ),
        "property_sets_available_on_prep": False,
        "entity_enrichment": {
            "enriched": bool(meta.get("enriched")),
            "entity_count_scanned": int(meta.get("entity_count_scanned") or 0),
            "hint_coverage": dict(meta.get("hint_coverage") or {}),
        },
    }


def filter_prep_rows_by_semantic(
    prep_rows: Sequence[Mapping[str, Any]],
    *,
    field_key: str,
    value: str,
) -> list[dict[str, Any]]:
    """Return prep rows where ``field_key`` equals ``value`` (string compare)."""
    key = _str_val(field_key)
    target = _str_val(value)
    if not key or not target:
        return [dict(r) for r in prep_rows]
    allowed = {f["key"] for f in PREP_NATIVE_FIELDS if f["is_filterable"]} | {
        f["key"] for f in ENTITY_HINT_FIELDS
    }
    if key not in allowed:
        return [dict(r) for r in prep_rows]
    out: list[dict[str, Any]] = []
    for row in prep_rows:
        if _str_val(row.get(key)) == target:
            out.append(dict(row))
    return out


def sort_prep_rows_by_semantic(
    prep_rows: Sequence[Mapping[str, Any]],
    *,
    sort_key: str,
    direction: str = "asc",
) -> list[dict[str, Any]]:
    """Sort prep rows by a discovered sortable key."""
    key = _str_val(sort_key)
    if not key:
        return [dict(r) for r in prep_rows]
    sortable = {f["key"] for f in PREP_NATIVE_FIELDS if f["is_sortable"]} | {
        f["key"] for f in ENTITY_HINT_FIELDS
    }
    if key not in sortable:
        return [dict(r) for r in prep_rows]
    reverse = _str_val(direction).lower() == "desc"

    def sort_val(row: Mapping[str, Any]) -> Any:
        raw = row.get(key)
        if key == "element_count":
            try:
                return int(raw or 0)
            except (TypeError, ValueError):
                return 0
        return _str_val(raw).lower()

    return sorted((dict(r) for r in prep_rows), key=sort_val, reverse=reverse)


def parse_semantic_query(query: Mapping[str, Any]) -> dict[str, str]:
    """Extract semantic filter/sort params from a GET-like mapping."""
    return {
        "field": _str_val(query.get(SEMANTIC_QUERY_FIELD)),
        "value": _str_val(query.get(SEMANTIC_QUERY_VALUE)),
        "sort": _str_val(query.get(SEMANTIC_QUERY_SORT)),
        "sort_dir": _str_val(query.get(SEMANTIC_QUERY_SORT_DIR)) or "asc",
    }


def apply_semantic_filters_to_qty_prep(
    qty_prep: MutableMapping[str, Any],
    *,
    project: Any,
    query: Mapping[str, Any],
) -> dict[str, Any]:
    """Enrich, discover, filter, and sort prep rows in-place. Returns panel context."""
    rows = list(qty_prep.get("prep_rows") or [])
    total_before = len(rows)
    enrichment = enrich_prep_rows_with_entity_semantics(project, rows)
    discovery = discover_semantic_fields(project, rows, enrichment_meta=enrichment)
    params = parse_semantic_query(query)

    filtered = rows
    active_filter = False
    if params["field"] and params["value"]:
        filtered = filter_prep_rows_by_semantic(
            rows, field_key=params["field"], value=params["value"]
        )
        active_filter = True
    if params["sort"]:
        filtered = sort_prep_rows_by_semantic(
            filtered, sort_key=params["sort"], direction=params["sort_dir"]
        )

    qty_prep["prep_rows"] = filtered
    qty_prep["prep_row_total_unfiltered"] = total_before
    qty_prep["semantic_filter_active"] = active_filter
    panel = {
        **discovery,
        "active_field": params["field"],
        "active_value": params["value"],
        "active_sort": params["sort"],
        "active_sort_dir": params["sort_dir"],
        "showing_count": len(filtered),
        "total_count": total_before,
        "filter_active": active_filter,
        "show_category_column": any(_str_val(r.get("semantic_category")) for r in filtered),
        "show_family_column": any(_str_val(r.get("semantic_family")) for r in filtered),
    }
    qty_prep["semantic_filters"] = panel
    return panel


class IfcSemanticFieldService:
    """Project-scoped façade for IFC-SEM-1 discovery/enrichment."""

    def __init__(self, project: Any, user: Any | None = None) -> None:
        self.project = project
        self.user = user

    def apply_to_qty_prep(
        self, qty_prep: MutableMapping[str, Any], query: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Enrich and filter ``qty_prep`` using query params."""
        try:
            return apply_semantic_filters_to_qty_prep(qty_prep, project=self.project, query=query)
        except Exception:
            logger.exception(
                "ifc semantic filters failed project=%s", getattr(self.project, "pk", None)
            )
            qty_prep.setdefault(
                "semantic_filters",
                {
                    "fields": [],
                    "filterable_fields": [],
                    "helper": "Semantic filters temporarily unavailable.",
                    "showing_count": len(qty_prep.get("prep_rows") or []),
                    "total_count": len(qty_prep.get("prep_rows") or []),
                    "filter_active": False,
                    "property_sets_available_on_prep": False,
                },
            )
            return qty_prep["semantic_filters"]
