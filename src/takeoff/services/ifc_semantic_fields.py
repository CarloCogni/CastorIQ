# takeoff/services/ifc_semantic_fields.py
"""IFC-SEM-1/2 — semantic field discovery, property columns, prep enrichment.

SEM-1: prep-native filters + Category/Family hints.
SEM-2: discover indexed IFC property keys, add selected columns, aggregate
single/Mixed/— values onto grouped prep rows, filter before batch mapping.

Read-only. Not writeback, Modify, BOQ/cost. No migrations. No S2 contract change.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

SAMPLE_VALUE_CAP = 20
MAX_DISCOVERED_PROP_KEYS = 25
MAX_SELECTED_PROP_COLS = 5
MIN_PROP_NONEMPTY = 50
MAX_PROP_DISTINCT_SAMPLE = 80
PROP_KEY_PREFIX = "prop:"
MIXED_VALUES_LABEL = "Mixed values"
MISSING_DISPLAY = "—"

SEMANTIC_QUERY_FIELD = "semantic_field"
SEMANTIC_QUERY_VALUE = "semantic_value"
SEMANTIC_QUERY_SORT = "semantic_sort"
SEMANTIC_QUERY_SORT_DIR = "semantic_sort_dir"
SEM_COLS_PARAM = "sem_cols"

_NOISY_PROP_RE = re.compile(
    r"(^|\.)(id|type id|uniqueid|guid|ifcguid)(\.|$)",
    re.IGNORECASE,
)

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


def _is_noisy_property_key(key: str) -> bool:
    k = _str_val(key)
    if not k or "." not in k:
        return True
    if _NOISY_PROP_RE.search(k):
        return True
    lower = k.lower()
    return lower.endswith(".id") or "guid" in lower


def prop_column_key(source_property: str) -> str:
    """Return canonical prep column key for an IFC property path."""
    return f"{PROP_KEY_PREFIX}{_str_val(source_property)}"


def source_property_from_column_key(column_key: str) -> str:
    """Strip ``prop:`` prefix; return empty if not a property column key."""
    key = _str_val(column_key)
    if not key.startswith(PROP_KEY_PREFIX):
        return ""
    return key[len(PROP_KEY_PREFIX) :]


def _property_label(source_property: str) -> str:
    raw = _str_val(source_property)
    if "." in raw:
        return raw.split(".", 1)[-1]
    return raw or "Property"


def _property_group(source_property: str) -> str:
    raw = _str_val(source_property)
    if "." in raw:
        return raw.split(".", 1)[0]
    return "Other"


def _latest_completed_ifc(project: Any) -> Any | None:
    from ifc_processor.models import IFCFile

    return (
        IFCFile.objects.filter(project=project, status="completed").order_by("-created_at").first()
    )


def parse_sem_cols(query: Mapping[str, Any]) -> list[str]:
    """Parse ``sem_cols`` into unique validated ``prop:`` keys (capped)."""
    raw = query.get(SEM_COLS_PARAM)
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts: list[str] = []
        for item in raw:
            parts.extend(str(item).split(","))
    else:
        parts = str(raw).split(",")
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = _str_val(part)
        src = source_property_from_column_key(key)
        if not src or _is_noisy_property_key(src):
            continue
        canon = prop_column_key(src)
        if canon in seen:
            continue
        seen.add(canon)
        out.append(canon)
        if len(out) >= MAX_SELECTED_PROP_COLS:
            break
    return out


def aggregate_property_values(values: Sequence[str]) -> dict[str, Any]:
    """Aggregate entity values for one prep grain into display status."""
    nonempty = [_str_val(v) for v in values if _str_val(v)]
    if not nonempty:
        return {
            "status": "missing",
            "value": "",
            "display": MISSING_DISPLAY,
            "distinct_count": 0,
        }
    distinct = sorted(set(nonempty))
    if len(distinct) == 1:
        text = distinct[0]
        display = text if len(text) <= 80 else text[:77] + "…"
        return {
            "status": "single",
            "value": text,
            "display": display,
            "distinct_count": 1,
        }
    return {
        "status": "mixed",
        "value": MIXED_VALUES_LABEL,
        "display": MIXED_VALUES_LABEL,
        "distinct_count": len(distinct),
    }


def _scan_entities(
    project: Any,
    *,
    selected_source_props: Sequence[str],
) -> dict[str, Any]:
    """One read-only entity pass: discovery stats + grain counters."""
    empty = {
        "entity_count_scanned": 0,
        "key_nonempty": Counter(),
        "key_samples": defaultdict(Counter),
        "grain_counters": defaultdict(lambda: defaultdict(Counter)),
        "class_counters": defaultdict(lambda: defaultdict(Counter)),
    }
    ifc = _latest_completed_ifc(project)
    if ifc is None:
        return empty

    from ifc_processor.models import IFCEntity

    selected = [_str_val(p) for p in selected_source_props if _str_val(p)]
    always = ["Other.Category", "Other.Family"]
    tracked = list(dict.fromkeys([*always, *selected]))

    key_nonempty: Counter[str] = Counter()
    key_samples: dict[str, Counter[str]] = defaultdict(Counter)
    grain_counters: dict[str, dict[tuple[str, str], Counter[str]]] = {
        p: defaultdict(Counter) for p in tracked
    }
    class_counters: dict[str, dict[str, Counter[str]]] = {p: defaultdict(Counter) for p in tracked}

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
        ifc_class = _str_val(entity.ifc_type)
        type_name = ""
        if getattr(entity, "element_type", None) is not None:
            type_name = _str_val(entity.element_type.name)
        grain = _grain_key(ifc_class, type_name)

        for pk, pv in props.items():
            text = _str_val(pv)
            if not text:
                continue
            key_nonempty[pk] += 1
            samples = key_samples[pk]
            if len(samples) < MAX_PROP_DISTINCT_SAMPLE:
                samples[text[:120]] += 1

        for prop_name in tracked:
            text = _str_val(props.get(prop_name))
            if not text:
                continue
            grain_counters[prop_name][grain][text] += 1
            class_counters[prop_name][ifc_class][text] += 1

    return {
        "entity_count_scanned": scanned,
        "key_nonempty": key_nonempty,
        "key_samples": key_samples,
        "grain_counters": grain_counters,
        "class_counters": class_counters,
    }


def discover_indexed_property_columns(
    scan: Mapping[str, Any] | None = None,
    *,
    project: Any | None = None,
    max_keys: int = MAX_DISCOVERED_PROP_KEYS,
) -> list[dict[str, Any]]:
    """Return ranked ``prop:`` column descriptors from indexed entity properties."""
    if scan is not None:
        data: Mapping[str, Any] = scan
    elif project is not None:
        data = _scan_entities(project, selected_source_props=[])
    else:
        data = {}
    key_nonempty: Counter[str] = Counter(data.get("key_nonempty") or {})
    key_samples: Mapping[str, Counter[str]] = data.get("key_samples") or {}
    entity_count = int(data.get("entity_count_scanned") or 0)
    min_nonempty = MIN_PROP_NONEMPTY
    if entity_count and entity_count < MIN_PROP_NONEMPTY:
        min_nonempty = max(1, entity_count // 3)
    rows: list[dict[str, Any]] = []
    for source_key, nonempty in key_nonempty.most_common():
        if _is_noisy_property_key(source_key):
            continue
        if nonempty < min_nonempty:
            continue
        samples_counter = key_samples.get(source_key) or Counter()
        distinct = len(samples_counter)
        if distinct < 1 or distinct > MAX_PROP_DISTINCT_SAMPLE:
            continue
        sample_values = [s for s, _ in samples_counter.most_common(SAMPLE_VALUE_CAP)]
        rows.append(
            {
                "key": prop_column_key(source_key),
                "label": _property_label(source_key),
                "group": _property_group(source_key),
                "source": "ifc_entity_properties",
                "source_property": source_key,
                "coverage": int(nonempty),
                "sample_values": sample_values,
                "is_filterable": True,
                "is_sortable": False,
            }
        )
        if len(rows) >= max_keys:
            break
    return rows


def _resolve_grain_counter(
    grain_map: Mapping[tuple[str, str], Counter[str]],
    class_map: Mapping[str, Counter[str]],
    ifc_class: str,
    type_name: str,
) -> Counter[str]:
    grain = _grain_key(ifc_class, type_name)
    counter = grain_map.get(grain)
    if counter:
        return counter
    if type_name:
        # Fall back to class-level when type grain has no values.
        return class_map.get(ifc_class) or Counter()
    return class_map.get(ifc_class) or Counter()


def enrich_prep_rows_with_entity_semantics(
    project: Any,
    prep_rows: Sequence[MutableMapping[str, Any]],
    *,
    selected_prop_columns: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Attach Category/Family and selected property columns onto prep rows."""
    rows = list(prep_rows)
    selected_cols = list(selected_prop_columns or [])
    selected_sources = [
        source_property_from_column_key(k)
        for k in selected_cols
        if source_property_from_column_key(k)
    ]
    meta: dict[str, Any] = {
        "enriched": False,
        "entity_count_scanned": 0,
        "hint_coverage": {f["key"]: 0 for f in ENTITY_HINT_FIELDS},
        "property_column_coverage": {},
        "property_columns_available": [],
        "selected_prop_columns": selected_cols,
        "property_sets_indexed_on_prep": False,
        "helper": (
            "No indexed IFC property columns are available yet. "
            "Current filters use preparation fields (class, type, basis, source)."
        ),
    }
    if not rows:
        return meta

    for row in rows:
        row.setdefault("semantic_category", "")
        row.setdefault("semantic_family", "")
        row.setdefault("prop_columns", {})
        row.setdefault("prop_column_cells", [])

    scan = _scan_entities(project, selected_source_props=selected_sources)
    meta["entity_count_scanned"] = int(scan.get("entity_count_scanned") or 0)
    available = discover_indexed_property_columns(scan)
    meta["property_columns_available"] = available
    allowed_prop_keys = {d["key"] for d in available}
    # Always allow explicitly selected keys that passed parse_sem_cols even if
    # slightly below discovery threshold (still present on entities).
    selected_valid = [
        k for k in selected_cols if k in allowed_prop_keys or source_property_from_column_key(k)
    ]
    selected_valid = selected_valid[:MAX_SELECTED_PROP_COLS]
    meta["selected_prop_columns"] = selected_valid

    grain_counters = scan.get("grain_counters") or {}
    class_counters = scan.get("class_counters") or {}

    cat_cov = 0
    fam_cov = 0
    prop_cov: dict[str, int] = {k: 0 for k in selected_valid}

    for row in rows:
        ifc_class = _str_val(row.get("ifc_class"))
        type_name = _str_val(row.get("type_name"))
        cat_counter = _resolve_grain_counter(
            grain_counters.get("Other.Category") or {},
            class_counters.get("Other.Category") or {},
            ifc_class,
            type_name,
        )
        fam_counter = _resolve_grain_counter(
            grain_counters.get("Other.Family") or {},
            class_counters.get("Other.Family") or {},
            ifc_class,
            type_name,
        )
        cat = _majority(cat_counter)
        fam = _majority(fam_counter)
        # Prefer majority when mixed for SEM-1 hint compatibility; also expose
        # aggregated display for Category/Family via prop columns when selected.
        row["semantic_category"] = cat
        row["semantic_family"] = fam
        if cat:
            cat_cov += 1
        if fam:
            fam_cov += 1

        prop_map: dict[str, Any] = {}
        cells: list[dict[str, Any]] = []
        for col_key in selected_valid:
            src = source_property_from_column_key(col_key)
            counter = _resolve_grain_counter(
                grain_counters.get(src) or {},
                class_counters.get(src) or {},
                ifc_class,
                type_name,
            )
            expanded: list[str] = []
            for val, count in counter.items():
                expanded.extend([val] * int(count))
            agg = aggregate_property_values(expanded)
            prop_map[col_key] = agg
            row[col_key] = agg["display"]
            cells.append(
                {
                    "key": col_key,
                    "display": agg["display"],
                    "status": agg["status"],
                    "distinct_count": agg["distinct_count"],
                }
            )
            if agg["status"] != "missing":
                prop_cov[col_key] = prop_cov.get(col_key, 0) + 1
        row["prop_columns"] = prop_map
        row["prop_column_cells"] = cells

    meta["enriched"] = cat_cov > 0 or fam_cov > 0 or any(prop_cov.values())
    meta["hint_coverage"] = {
        "semantic_category": cat_cov,
        "semantic_family": fam_cov,
    }
    meta["property_column_coverage"] = prop_cov
    meta["property_sets_indexed_on_prep"] = bool(available)
    if available:
        meta["helper"] = (
            "Indexed IFC entity properties can be added as columns. "
            "Grouped preparation rows show a single value, Mixed values, or —."
        )
    elif meta["enriched"]:
        meta["helper"] = (
            "Category and Family come from indexed IFC entity properties "
            "(Other.Category / Other.Family), aggregated to preparation rows."
        )
    logger.debug(
        "ifc semantic enrich project=%s scanned=%s props=%s",
        getattr(project, "pk", None),
        meta["entity_count_scanned"],
        len(selected_valid),
    )
    return meta


def _distinct_samples(rows: Sequence[Mapping[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for row in rows:
        text = _str_val(row.get(key))
        if not text or text in seen or text == MISSING_DISPLAY:
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
    meta = dict(enrichment_meta or {})

    for spec in PREP_NATIVE_FIELDS:
        key = str(spec["key"])
        samples = _distinct_samples(rows, key)
        coverage = sum(1 for r in rows if _str_val(r.get(key)))
        if key == "element_count":
            coverage = sum(1 for r in rows if r.get(key) is not None)
        if coverage <= 0 and key != "ifc_class":
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

    for spec in ENTITY_HINT_FIELDS:
        key = str(spec["key"])
        coverage = int((meta.get("hint_coverage") or {}).get(key) or 0)
        if coverage <= 0:
            continue
        fields.append(
            {
                "key": key,
                "label": spec["label"],
                "source": spec["source"],
                "source_property": spec["source_property"],
                "coverage": coverage,
                "sample_values": _distinct_samples(rows, key),
                "is_filterable": True,
                "is_sortable": True,
            }
        )

    property_columns = list(meta.get("property_columns_available") or [])
    selected = list(meta.get("selected_prop_columns") or [])
    selected_seen: set[str] = set()
    for desc in property_columns:
        key = str(desc.get("key") or "")
        if key not in selected:
            continue
        selected_seen.add(key)
        # Prefer samples from current prep rows when column is active.
        samples = (
            _distinct_samples(rows, key) or list(desc.get("sample_values") or [])[:SAMPLE_VALUE_CAP]
        )
        coverage = int((meta.get("property_column_coverage") or {}).get(key) or 0)
        fields.append(
            {
                **desc,
                "coverage": coverage or int(desc.get("coverage") or 0),
                "sample_values": samples,
                "is_filterable": True,
                "is_sortable": False,
            }
        )
    for key in selected:
        if key in selected_seen:
            continue
        src = source_property_from_column_key(key)
        if not src:
            continue
        fields.append(
            {
                "key": key,
                "label": _property_label(src),
                "group": _property_group(src),
                "source": "ifc_entity_properties",
                "source_property": src,
                "coverage": int((meta.get("property_column_coverage") or {}).get(key) or 0),
                "sample_values": _distinct_samples(rows, key),
                "is_filterable": True,
                "is_sortable": False,
            }
        )

    filterable = [f for f in fields if f.get("is_filterable")]
    return {
        "fields": fields,
        "filterable_fields": filterable,
        "property_columns_available": property_columns,
        "selected_prop_columns": selected,
        "prep_row_total": total,
        "helper": meta.get("helper")
        or (
            "No indexed IFC property columns are available yet. "
            "Current filters use preparation fields (class, type, basis, source)."
        ),
        "property_sets_available_on_prep": bool(property_columns),
        "entity_enrichment": {
            "enriched": bool(meta.get("enriched")),
            "entity_count_scanned": int(meta.get("entity_count_scanned") or 0),
            "hint_coverage": dict(meta.get("hint_coverage") or {}),
            "property_column_coverage": dict(meta.get("property_column_coverage") or {}),
        },
    }


def filter_prep_rows_by_semantic(
    prep_rows: Sequence[Mapping[str, Any]],
    *,
    field_key: str,
    value: str,
    allowed_extra_keys: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return prep rows where ``field_key`` equals ``value`` (string compare)."""
    key = _str_val(field_key)
    target = _str_val(value)
    if not key or not target:
        return [dict(r) for r in prep_rows]
    allowed = {f["key"] for f in PREP_NATIVE_FIELDS if f["is_filterable"]} | {
        f["key"] for f in ENTITY_HINT_FIELDS
    }
    for extra in allowed_extra_keys or []:
        if _str_val(extra):
            allowed.add(_str_val(extra))
    if key.startswith(PROP_KEY_PREFIX):
        allowed.add(key)
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


def parse_semantic_query(query: Mapping[str, Any]) -> dict[str, Any]:
    """Extract semantic filter/sort/column params from a GET-like mapping."""
    cols = parse_sem_cols(query)
    add = _str_val(query.get("sem_cols_add"))
    if add.startswith(PROP_KEY_PREFIX) and add not in cols:
        src = source_property_from_column_key(add)
        if src and not _is_noisy_property_key(src) and len(cols) < MAX_SELECTED_PROP_COLS:
            cols = [*cols, prop_column_key(src)]
    return {
        "field": _str_val(query.get(SEMANTIC_QUERY_FIELD)),
        "value": _str_val(query.get(SEMANTIC_QUERY_VALUE)),
        "sort": _str_val(query.get(SEMANTIC_QUERY_SORT)),
        "sort_dir": _str_val(query.get(SEMANTIC_QUERY_SORT_DIR)) or "asc",
        "sem_cols": cols,
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
    params = parse_semantic_query(query)
    enrichment = enrich_prep_rows_with_entity_semantics(
        project, rows, selected_prop_columns=params["sem_cols"]
    )
    discovery = discover_semantic_fields(project, rows, enrichment_meta=enrichment)

    filtered = rows
    active_filter = False
    allowed_extra = list(enrichment.get("selected_prop_columns") or []) + [
        d["key"] for d in (enrichment.get("property_columns_available") or [])
    ]
    if params["field"] and params["value"]:
        filtered = filter_prep_rows_by_semantic(
            rows,
            field_key=params["field"],
            value=params["value"],
            allowed_extra_keys=allowed_extra,
        )
        active_filter = True
    if params["sort"]:
        filtered = sort_prep_rows_by_semantic(
            filtered, sort_key=params["sort"], direction=params["sort_dir"]
        )

    qty_prep["prep_rows"] = filtered
    qty_prep["prep_row_total_unfiltered"] = total_before
    qty_prep["semantic_filter_active"] = active_filter
    selected_cols = list(enrichment.get("selected_prop_columns") or [])
    selected_meta = []
    avail_by_key = {
        str(d.get("key")): d for d in (enrichment.get("property_columns_available") or [])
    }
    for key in selected_cols:
        desc = avail_by_key.get(key) or {
            "key": key,
            "label": _property_label(source_property_from_column_key(key)),
            "source_property": source_property_from_column_key(key),
        }
        selected_meta.append(desc)

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
        "selected_prop_column_meta": selected_meta,
        "sem_cols_param": ",".join(selected_cols),
    }
    qty_prep["semantic_filters"] = panel
    return panel


class IfcSemanticFieldService:
    """Project-scoped façade for IFC-SEM-1/2 discovery/enrichment."""

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
                    "property_columns_available": [],
                    "selected_prop_columns": [],
                    "helper": "Semantic filters temporarily unavailable.",
                    "showing_count": len(qty_prep.get("prep_rows") or []),
                    "total_count": len(qty_prep.get("prep_rows") or []),
                    "filter_active": False,
                    "property_sets_available_on_prep": False,
                },
            )
            return qty_prep["semantic_filters"]
