# takeoff/services/ifc_semantic_fields.py
"""IFC-SEM-1/2/3/4A — semantic field discovery, property columns, prep enrichment.

SEM-1: prep-native filters + Category/Family hints.
SEM-2: discover indexed IFC property keys, add selected columns, aggregate
single/Mixed/— values onto grouped prep rows, filter before batch mapping.
SEM-3: Level/Storey (spatial), Project Level, classification-like properties;
honest unavailable states for Zone.
SEM-4A: true IFC classification references via ClassRef.* keys; separate from
authoring properties and Castor schema mapping.

Read-only. Not writeback, Modify, BOQ/cost. No migrations. No S2 contract change.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

SAMPLE_VALUE_CAP = 40
MAX_DISCOVERED_PROP_KEYS = 500
MAX_SELECTED_PROP_COLS = 12
MIN_PROP_NONEMPTY = 1
MAX_PROP_DISTINCT_SAMPLE = 200
PROP_KEY_PREFIX = "prop:"
SPATIAL_KEY_PREFIX = "spatial:"
SPATIAL_STOREY_KEY = "spatial:storey"
SPATIAL_CONTAINER_KEY = "spatial:container"
MIXED_VALUES_LABEL = "Multiple values"
MISSING_DISPLAY = "—"

SEMANTIC_QUERY_FIELD = "semantic_field"
SEMANTIC_QUERY_VALUE = "semantic_value"
SEMANTIC_QUERY_OP = "semantic_op"
SEMANTIC_QUERY_SORT = "semantic_sort"
SEMANTIC_QUERY_SORT_DIR = "semantic_sort_dir"
SEM_COLS_PARAM = "sem_cols"

# Curated SEM-3 structure / classification-like property columns (first-class).
STRUCTURE_CURATED: tuple[dict[str, str], ...] = (
    {
        "key": "prop:Identity Data.Project Level",
        "label": "Project Level (authoring)",
        "group": "Model structure",
        "source_property": "Identity Data.Project Level",
    },
)
CLASSIFICATION_LIKE_CURATED: tuple[dict[str, str], ...] = (
    {
        "key": "prop:Identity Data.OmniClass Title",
        "label": "OmniClass Title",
        "group": "Authoring classification properties",
        "source_property": "Identity Data.OmniClass Title",
    },
    {
        "key": "prop:Identity Data.OmniClass Number",
        "label": "OmniClass Number",
        "group": "Authoring classification properties",
        "source_property": "Identity Data.OmniClass Number",
    },
    {
        "key": "prop:Identity Data.Assembly Code",
        "label": "Assembly Code",
        "group": "Authoring classification properties",
        "source_property": "Identity Data.Assembly Code",
    },
)

# SEM-4A true IFC classification reference columns (indexed ClassRef.*).
CLASSREF_CURATED: tuple[dict[str, str], ...] = (
    {
        "key": "classref:ifc",
        "label": "IFC Classification Reference",
        "group": "Existing IFC classification",
        "source_property": "ClassRef.Display",
    },
    {
        "key": "classref:system",
        "label": "Classification System",
        "group": "Existing IFC classification",
        "source_property": "ClassRef.System",
    },
    {
        "key": "classref:code",
        "label": "Classification Code",
        "group": "Existing IFC classification",
        "source_property": "ClassRef.Identification",
    },
)

CLASSREF_KEY_TO_PROP: dict[str, str] = {
    item["key"]: item["source_property"] for item in CLASSREF_CURATED
}

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
        # Class scope lives on the dedicated IFC Class selector — not Field picker.
        "is_filterable": False,
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
        "key": "measurement_type",
        "label": "Measurement",
        "source": "prep_row",
        "source_property": None,
        "is_filterable": True,
        "is_sortable": True,
    },
    {
        "key": "ifc_quantity_source",
        "label": "IFC Source",
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

# TABLE-04 primary filter surface — Category/Family stay out of primary UX.
# IFC Class is owned by the dedicated class selector (not the Field picker).
PRIMARY_FILTER_FIELD_KEYS: frozenset[str] = frozenset(
    {"type_name", "measurement_type", "ifc_quantity_source"}
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
    """Return True for keys unsuitable as catalogue columns/filters.

    Excludes ids/guids only. Qto_* quantity fields are included in the dynamic
    catalogue (DYNAMIC-07) with numeric typing — not a curated Identity-only list.
    """
    k = _str_val(key)
    if not k or "." not in k:
        return True
    if _NOISY_PROP_RE.search(k):
        return True
    lower = k.lower()
    if lower.endswith(".id") or "guid" in lower:
        return True
    return False


def prop_column_key(source_property: str) -> str:
    """Return canonical prep column key for an IFC property path."""
    return f"{PROP_KEY_PREFIX}{_str_val(source_property)}"


def source_property_from_column_key(column_key: str) -> str:
    """Strip ``prop:`` prefix; return empty if not a property column key."""
    key = _str_val(column_key)
    if not key.startswith(PROP_KEY_PREFIX):
        return ""
    return key[len(PROP_KEY_PREFIX) :]


def is_spatial_column_key(column_key: str) -> bool:
    """Return True for SEM-3 spatial column keys."""
    key = _str_val(column_key)
    return key in {SPATIAL_STOREY_KEY, SPATIAL_CONTAINER_KEY}


def is_classref_column_key(column_key: str) -> bool:
    """Return True for SEM-4A classref:* semantic column keys."""
    return _str_val(column_key) in CLASSREF_KEY_TO_PROP


def classref_source_property(column_key: str) -> str:
    """Map classref:* column key to ClassRef.* property path."""
    return CLASSREF_KEY_TO_PROP.get(_str_val(column_key), "")


def resolve_column_source_property(column_key: str) -> str:
    """Resolve prop:/classref: column keys to IFCEntity.properties paths."""
    key = _str_val(column_key)
    if is_classref_column_key(key):
        return classref_source_property(key)
    return source_property_from_column_key(key)


def _resolve_spatial_names(entity: Any) -> tuple[str, str]:
    """Return ``(storey_name, container_name)`` from spatial_container chain."""
    node = getattr(entity, "spatial_container", None)
    container_name = ""
    if node is not None:
        ent = getattr(node, "entity", None)
        container_name = _str_val(getattr(ent, "name", None)) if ent is not None else ""
    storey_name = ""
    walk = node
    depth = 0
    while walk is not None and depth < 8:
        if _str_val(getattr(walk, "spatial_type", None)) == "building_storey":
            ent = getattr(walk, "entity", None)
            storey_name = _str_val(getattr(ent, "name", None)) if ent is not None else ""
            break
        walk = getattr(walk, "parent", None)
        depth += 1
    if not storey_name:
        storey_name = container_name
    return storey_name, container_name


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


def label_for_stable_field_key(field_key: str) -> str:
    """Resolve a human label from a stable field key without a catalogue scan."""
    key = _str_val(field_key)
    if not key:
        return ""
    for spec in (*PREP_NATIVE_FIELDS, *ENTITY_HINT_FIELDS):
        if str(spec.get("key")) == key:
            return str(spec.get("label") or key)
    if key == SPATIAL_STOREY_KEY:
        return "Level / Storey (spatial)"
    if key == SPATIAL_CONTAINER_KEY:
        return "Spatial container"
    for curated in (*STRUCTURE_CURATED, *CLASSIFICATION_LIKE_CURATED, *CLASSREF_CURATED):
        if curated["key"] == key:
            return curated["label"]
    if is_classref_column_key(key):
        for curated in CLASSREF_CURATED:
            if curated["key"] == key:
                return curated["label"]
        return key
    src = source_property_from_column_key(key)
    if src:
        return _property_label(src)
    return key


def guess_value_type_for_field_key(field_key: str) -> str:
    """Best-effort value_type from a stable key (Qto → numeric; else text)."""
    key = _str_val(field_key)
    if key == "element_count":
        return "numeric"
    src = source_property_from_column_key(key) or ""
    if src.startswith("Qto_") or key.startswith("prop:Qto_"):
        return "numeric"
    return "text"


def static_zone_unavailable() -> dict[str, Any]:
    """Zone is never discovered as available from indexed spatial structure."""
    return {
        "key": "struct:zone",
        "label": "Zone",
        "group": "Model structure",
        "available": False,
        "message": (
            "No Zone evidence was found in this IFC export. If a suitable exported "
            "property exists, select it as the Zone source during preparation "
            "before freezing a new snapshot."
        ),
    }


def lightweight_structure_unavailable(
    project: Any,
    *,
    ifc_file: Any | None = None,
) -> dict[str, Any]:
    """Zone + classref unavailable helpers without a full entity catalogue scan."""
    unavailable: dict[str, Any] = {"zone": static_zone_unavailable()}
    ifc = _resolve_scan_ifc(project, ifc_file)
    classref_present = False
    if ifc is not None:
        from ifc_processor.models import IFCEntity

        qs = IFCEntity.objects.filter(ifc_file=ifc)
        for curated in CLASSREF_CURATED:
            src = curated["source_property"]
            if qs.filter(properties__has_key=src).exists():
                classref_present = True
                break
    if not classref_present:
        unavailable["ifc_classification_ref"] = {
            "key": "classref:ifc",
            "label": "IFC Classification Reference",
            "group": "Existing IFC classification",
            "available": False,
            "message": (
                "No indexed IFC classification references found in this IFC export "
                "(or not re-indexed yet after SEM-4A)."
            ),
        }
    return unavailable


def _latest_completed_ifc(project: Any) -> Any | None:
    from ifc_processor.models import IFCFile

    return (
        IFCFile.objects.filter(project=project, status="completed").order_by("-created_at").first()
    )


def _resolve_scan_ifc(project: Any, ifc_file: Any | None = None) -> Any | None:
    """Use a pinned IFC when provided and project-scoped; else latest completed."""
    if ifc_file is not None:
        if getattr(ifc_file, "project_id", None) == getattr(project, "pk", None):
            return ifc_file
        return None
    return _latest_completed_ifc(project)


def parse_sem_cols(query: Mapping[str, Any]) -> list[str]:
    """Parse ``sem_cols`` into unique validated column keys (capped).

    Accepts ``prop:…`` property columns, SEM-3 ``spatial:*`` keys, and
    SEM-4A ``classref:*`` keys.
    """
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
        if not key or key in seen:
            continue
        if is_spatial_column_key(key) or is_classref_column_key(key):
            seen.add(key)
            out.append(key)
        else:
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


def aggregate_property_values(values: Sequence[Any]) -> dict[str, Any]:
    """Aggregate entity values for one prep grain into display status.

    Uniform → value; mixed → Multiple values (+ detail samples); missing → —;
    numeric zero stays ``0`` (never majority/arbitrary pick).
    """
    from takeoff.services.quantity_entity_filter import coerce_property_text

    expanded: list[str] = []
    unsupported = 0
    for raw in values:
        text, is_complex = coerce_property_text(raw)
        if is_complex:
            unsupported += 1
            continue
        if text == "":
            continue
        expanded.append(text)
    if not expanded:
        return {
            "status": "missing" if unsupported == 0 else "unsupported",
            "value": "",
            "display": MISSING_DISPLAY if unsupported == 0 else "Unsupported value",
            "distinct_count": 0,
            "detail_values": [],
        }
    distinct = sorted(set(expanded))
    if len(distinct) == 1:
        text = distinct[0]
        display = text if len(text) <= 80 else text[:77] + "…"
        return {
            "status": "single",
            "value": text,
            "display": display,
            "distinct_count": 1,
            "detail_values": [text],
        }
    detail = distinct[:12]
    return {
        "status": "mixed",
        "value": MIXED_VALUES_LABEL,
        "display": MIXED_VALUES_LABEL,
        "distinct_count": len(distinct),
        "detail_values": detail,
    }


def _scan_entities(
    project: Any,
    *,
    selected_source_props: Sequence[str],
    selected_spatial_keys: Sequence[str] | None = None,
    ifc_file: Any | None = None,
    entity_predicate: Any | None = None,
    discover_catalogue: bool = True,
) -> dict[str, Any]:
    """One read-only entity pass: discovery stats + grain counters.

    When ``entity_predicate`` is set, only matching entities contribute to
    grain counters (DYNAMIC-07 filter-before-aggregation for columns).
    When ``discover_catalogue`` is True, ``key_nonempty`` still scans all
    entities so the catalogue stays complete. When False, only selected
    (and Category/Family) props are tracked — no full-property catalogue pass.
    """
    from takeoff.services.quantity_entity_filter import coerce_property_text

    empty = {
        "entity_count_scanned": 0,
        "entity_count_matched": 0,
        "key_nonempty": Counter(),
        "key_samples": defaultdict(Counter),
        "grain_counters": defaultdict(lambda: defaultdict(Counter)),
        "class_counters": defaultdict(lambda: defaultdict(Counter)),
        # COLUMNS-10: per-instance leaf values (global_id → text).
        "instance_values": defaultdict(dict),
        "instance_complex": defaultdict(set),
        "spatial_nonempty": Counter(),
        "spatial_samples": defaultdict(Counter),
    }
    ifc = _resolve_scan_ifc(project, ifc_file)
    if ifc is None:
        return empty

    from ifc_processor.models import IFCEntity

    selected = [_str_val(p) for p in selected_source_props if _str_val(p)]
    spatial_selected = [
        k for k in (selected_spatial_keys or []) if is_spatial_column_key(_str_val(k))
    ]
    # PERF-15C1: closed pickers + no visible prop columns → skip the iterator.
    if not discover_catalogue and not selected and not spatial_selected:
        return empty

    always = ["Other.Category", "Other.Family"] if discover_catalogue else []
    if not discover_catalogue and selected:
        # Enrichment still needs Category/Family grain when rows show them later;
        # keep them cheap to track alongside selected props.
        always = ["Other.Category", "Other.Family"]
    tracked = list(dict.fromkeys([*always, *selected]))

    key_nonempty: Counter[str] = Counter()
    key_samples: dict[str, Counter[str]] = defaultdict(Counter)
    grain_counters: dict[str, dict[tuple[str, str], Counter[str]]] = {
        p: defaultdict(Counter) for p in tracked
    }
    class_counters: dict[str, dict[str, Counter[str]]] = {p: defaultdict(Counter) for p in tracked}
    for sk in (SPATIAL_STOREY_KEY, SPATIAL_CONTAINER_KEY):
        grain_counters[sk] = defaultdict(Counter)
        class_counters[sk] = defaultdict(Counter)
    spatial_nonempty: Counter[str] = Counter()
    spatial_samples: dict[str, Counter[str]] = defaultdict(Counter)
    instance_values: dict[str, dict[str, str]] = defaultdict(dict)
    instance_complex: dict[str, set[str]] = defaultdict(set)

    scanned = 0
    matched = 0
    qs = (
        IFCEntity.objects.filter(ifc_file=ifc)
        .select_related(
            "element_type",
            "spatial_container__entity",
            "spatial_container__parent__entity",
        )
        .only(
            "ifc_type",
            "global_id",
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
    for entity in qs:
        scanned += 1
        props = entity.properties if isinstance(entity.properties, dict) else {}
        ifc_class = _str_val(entity.ifc_type)
        gid = _str_val(getattr(entity, "global_id", None))
        type_name = ""
        if getattr(entity, "element_type", None) is not None:
            type_name = _str_val(entity.element_type.name)
        grain = _grain_key(ifc_class, type_name)
        storey_name, container_name = _resolve_spatial_names(entity)

        for pk, pv in props.items():
            text, is_complex = coerce_property_text(pv)
            if is_complex or text == "":
                continue
            if discover_catalogue:
                key_nonempty[pk] += 1
                samples = key_samples[pk]
                if len(samples) < MAX_PROP_DISTINCT_SAMPLE:
                    samples[text[:120]] += 1

        include_grain = True
        if entity_predicate is not None:
            try:
                include_grain = bool(
                    entity_predicate(props, ifc_class, type_name, storey_name, container_name)
                )
            except Exception:
                include_grain = False
        if not include_grain:
            continue
        matched += 1

        for prop_name in tracked:
            text, is_complex = coerce_property_text(props.get(prop_name))
            if is_complex:
                if gid:
                    instance_complex[prop_name].add(gid)
                continue
            if text == "":
                continue
            grain_counters[prop_name][grain][text] += 1
            class_counters[prop_name][ifc_class][text] += 1
            if gid:
                instance_values[prop_name][gid] = text

        if storey_name:
            if discover_catalogue or SPATIAL_STOREY_KEY in spatial_selected:
                spatial_nonempty[SPATIAL_STOREY_KEY] += 1
                samples = spatial_samples[SPATIAL_STOREY_KEY]
                if len(samples) < MAX_PROP_DISTINCT_SAMPLE:
                    samples[storey_name[:120]] += 1
            if SPATIAL_STOREY_KEY in spatial_selected:
                grain_counters[SPATIAL_STOREY_KEY][grain][storey_name] += 1
                class_counters[SPATIAL_STOREY_KEY][ifc_class][storey_name] += 1
                if gid:
                    instance_values[SPATIAL_STOREY_KEY][gid] = storey_name
        if container_name:
            if discover_catalogue or SPATIAL_CONTAINER_KEY in spatial_selected:
                spatial_nonempty[SPATIAL_CONTAINER_KEY] += 1
                samples = spatial_samples[SPATIAL_CONTAINER_KEY]
                if len(samples) < MAX_PROP_DISTINCT_SAMPLE:
                    samples[container_name[:120]] += 1
            if SPATIAL_CONTAINER_KEY in spatial_selected:
                grain_counters[SPATIAL_CONTAINER_KEY][grain][container_name] += 1
                class_counters[SPATIAL_CONTAINER_KEY][ifc_class][container_name] += 1
                if gid:
                    instance_values[SPATIAL_CONTAINER_KEY][gid] = container_name

    return {
        "entity_count_scanned": scanned,
        "entity_count_matched": matched,
        "key_nonempty": key_nonempty,
        "key_samples": key_samples,
        "grain_counters": grain_counters,
        "class_counters": class_counters,
        "instance_values": instance_values,
        "instance_complex": instance_complex,
        "spatial_nonempty": spatial_nonempty,
        "spatial_samples": spatial_samples,
    }


def discover_indexed_property_columns(
    scan: Mapping[str, Any] | None = None,
    *,
    project: Any | None = None,
    max_keys: int = MAX_DISCOVERED_PROP_KEYS,
) -> list[dict[str, Any]]:
    """Return ranked ``prop:`` column descriptors from indexed entity properties.

    DYNAMIC-07: complete source-backed catalogue (not Identity-only curated list).
    Includes Qto_* quantity fields with numeric typing when present.
    """
    from takeoff.services.quantity_entity_filter import infer_value_type

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
    if entity_count and entity_count < 3:
        min_nonempty = 1
    rows: list[dict[str, Any]] = []
    for source_key, nonempty in key_nonempty.most_common():
        if _is_noisy_property_key(source_key):
            continue
        if nonempty < min_nonempty:
            continue
        samples_counter = key_samples.get(source_key) or Counter()
        sample_values = [s for s, _ in samples_counter.most_common(SAMPLE_VALUE_CAP)]
        value_type = infer_value_type(sample_values, source_property=source_key)
        group = _property_group(source_key)
        if source_key.startswith("Qto_"):
            group = "Quantity sets"
        class_map = (data.get("class_counters") or {}).get(source_key) or {}
        classes_present = sorted(str(c) for c in class_map.keys() if str(c).strip())
        rows.append(
            {
                "key": prop_column_key(source_key),
                "label": _property_label(source_key),
                "group": group,
                "source": "ifc_entity_properties",
                "source_property": source_key,
                "source_context": source_key.rsplit(".", 1)[0] if "." in source_key else source_key,
                "coverage": int(nonempty),
                "sample_values": sample_values,
                "value_type": value_type,
                "availability": "available",
                "classes_present": classes_present,
                "is_filterable": True,
                "is_sortable": False,
            }
        )
        if len(rows) >= max_keys:
            break
    return rows


def discover_sem3_structure_fields(scan: Mapping[str, Any]) -> dict[str, Any]:
    """Return SEM-3 model-structure and classification-like descriptors.

    Includes honest unavailable states for Zone and true IFC classification refs.
    """
    key_nonempty: Counter[str] = Counter(scan.get("key_nonempty") or {})
    key_samples: Mapping[str, Counter[str]] = scan.get("key_samples") or {}
    spatial_nonempty: Counter[str] = Counter(scan.get("spatial_nonempty") or {})
    spatial_samples: Mapping[str, Counter[str]] = scan.get("spatial_samples") or {}

    structure_available: list[dict[str, Any]] = []
    if int(spatial_nonempty.get(SPATIAL_STOREY_KEY) or 0) > 0:
        samples = [
            s
            for s, _ in (spatial_samples.get(SPATIAL_STOREY_KEY) or Counter()).most_common(
                SAMPLE_VALUE_CAP
            )
        ]
        structure_available.append(
            {
                "key": SPATIAL_STOREY_KEY,
                "label": "Level / Storey (spatial)",
                "group": "Model structure",
                "source": "ifc_spatial",
                "source_property": None,
                "coverage": int(spatial_nonempty[SPATIAL_STOREY_KEY]),
                "sample_values": samples,
                "is_filterable": True,
                "is_sortable": False,
                "available": True,
            }
        )
    if int(spatial_nonempty.get(SPATIAL_CONTAINER_KEY) or 0) > 0:
        samples = [
            s
            for s, _ in (spatial_samples.get(SPATIAL_CONTAINER_KEY) or Counter()).most_common(
                SAMPLE_VALUE_CAP
            )
        ]
        structure_available.append(
            {
                "key": SPATIAL_CONTAINER_KEY,
                "label": "Spatial container",
                "group": "Model structure",
                "source": "ifc_spatial",
                "source_property": None,
                "coverage": int(spatial_nonempty[SPATIAL_CONTAINER_KEY]),
                "sample_values": samples,
                "is_filterable": True,
                "is_sortable": False,
                "available": True,
            }
        )
    for curated in STRUCTURE_CURATED:
        src = curated["source_property"]
        cov = int(key_nonempty.get(src) or 0)
        if cov <= 0:
            continue
        samples = [s for s, _ in (key_samples.get(src) or Counter()).most_common(SAMPLE_VALUE_CAP)]
        structure_available.append(
            {
                "key": curated["key"],
                "label": curated["label"],
                "group": curated["group"],
                "source": "ifc_entity_properties",
                "source_property": src,
                "coverage": cov,
                "sample_values": samples,
                "is_filterable": True,
                "is_sortable": False,
                "available": True,
            }
        )

    classification_like: list[dict[str, Any]] = []
    for curated in CLASSIFICATION_LIKE_CURATED:
        src = curated["source_property"]
        cov = int(key_nonempty.get(src) or 0)
        if cov <= 0:
            continue
        samples = [s for s, _ in (key_samples.get(src) or Counter()).most_common(SAMPLE_VALUE_CAP)]
        classification_like.append(
            {
                "key": curated["key"],
                "label": curated["label"],
                "group": curated["group"],
                "source": "classification_like_property",
                "source_property": src,
                "coverage": cov,
                "sample_values": samples,
                "is_filterable": True,
                "is_sortable": False,
                "available": True,
            }
        )

    classref_available: list[dict[str, Any]] = []
    for curated in CLASSREF_CURATED:
        src = curated["source_property"]
        cov = int(key_nonempty.get(src) or 0)
        if cov <= 0:
            continue
        samples = [s for s, _ in (key_samples.get(src) or Counter()).most_common(SAMPLE_VALUE_CAP)]
        classref_available.append(
            {
                "key": curated["key"],
                "label": curated["label"],
                "group": curated["group"],
                "source": "ifc_classification_reference",
                "source_property": src,
                "coverage": cov,
                "sample_values": samples,
                "is_filterable": True,
                "is_sortable": False,
                "available": True,
            }
        )

    unavailable = {
        "zone": {
            "key": "struct:zone",
            "label": "Zone",
            "group": "Model structure",
            "available": False,
            "message": (
                "No Zone evidence was found in this IFC export. If a suitable exported "
                "property exists, select it as the Zone source during preparation "
                "before freezing a new snapshot."
            ),
        },
    }
    if not classref_available:
        unavailable["ifc_classification_ref"] = {
            "key": "classref:ifc",
            "label": "IFC Classification Reference",
            "group": "Existing IFC classification",
            "available": False,
            "message": (
                "No indexed IFC classification references found in this IFC export "
                "(or not re-indexed yet after SEM-4A)."
            ),
        }
    return {
        "structure_columns_available": structure_available,
        "classification_like_available": classification_like,
        "classref_available": classref_available,
        "unavailable": unavailable,
    }


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
    ifc_file: Any | None = None,
    entity_predicate: Any | None = None,
    extra_rows: Sequence[MutableMapping[str, Any]] | None = None,
    discover_catalogue: bool = True,
) -> dict[str, Any]:
    """Attach Category/Family, selected property, and SEM-3 spatial columns onto prep rows.

    COLUMNS-10: hierarchy Instance rows resolve the exact entity GlobalId leaf value.
    Class/Type rows keep aggregate (single / Multiple values / —).

    PERF-15C1: when ``discover_catalogue`` is False and no selected property /
    spatial columns need enrichment, the full IFCEntity catalogue scan is skipped.
    """
    rows = list(prep_rows)
    extras = list(extra_rows or [])
    selected_cols = list(selected_prop_columns or [])
    selected_sources = [
        resolve_column_source_property(k)
        for k in selected_cols
        if resolve_column_source_property(k)
    ]
    selected_spatial = [k for k in selected_cols if is_spatial_column_key(k)]
    meta: dict[str, Any] = {
        "enriched": False,
        "entity_count_scanned": 0,
        "hint_coverage": {f["key"]: 0 for f in ENTITY_HINT_FIELDS},
        "property_column_coverage": {},
        "property_columns_available": [],
        "selected_prop_columns": selected_cols,
        "property_sets_indexed_on_prep": False,
        "structure_columns_available": [],
        "classification_like_available": [],
        "classref_available": [],
        "unavailable": {},
        "helper": (
            "No indexed IFC property columns are available yet. "
            "Current filters use preparation fields (class, type, basis, source)."
        ),
        "catalogue_discovered": False,
    }
    all_rows = [*rows, *extras]
    if not all_rows:
        if not discover_catalogue:
            meta["unavailable"] = lightweight_structure_unavailable(project, ifc_file=ifc_file)
        return meta

    need_enrichment = bool(selected_sources or selected_spatial)
    if not discover_catalogue and not need_enrichment:
        for row in all_rows:
            row.setdefault("semantic_category", "")
            row.setdefault("semantic_family", "")
            row.setdefault("prop_columns", {})
            row.setdefault("prop_column_cells", [])
        meta["unavailable"] = lightweight_structure_unavailable(project, ifc_file=ifc_file)
        meta["helper"] = (
            "Add indexed IFC properties, Qto measures, spatial or classification "
            "fields as columns. Field catalogues load when you open Add column "
            "or Filter Field."
        )
        meta["scan_summary"] = {"key_nonempty": {}, "spatial_nonempty": {}}
        return meta

    for row in all_rows:
        row.setdefault("semantic_category", "")
        row.setdefault("semantic_family", "")
        row.setdefault("prop_columns", {})
        row.setdefault("prop_column_cells", [])

    scan = _scan_entities(
        project,
        selected_source_props=selected_sources,
        selected_spatial_keys=selected_spatial,
        ifc_file=ifc_file,
        entity_predicate=entity_predicate,
        discover_catalogue=discover_catalogue,
    )
    meta["entity_count_scanned"] = int(scan.get("entity_count_scanned") or 0)
    meta["catalogue_discovered"] = bool(discover_catalogue)
    available: list[dict[str, Any]] = []
    if discover_catalogue:
        available = discover_indexed_property_columns(scan)
        from takeoff.services.quantity_editable_table import COLUMNS_UI_EXCLUDED_SOURCE_PROPS

        meta["property_columns_available"] = [
            d
            for d in available
            if str(d.get("source_property") or "") not in COLUMNS_UI_EXCLUDED_SOURCE_PROPS
        ]
        sem3 = discover_sem3_structure_fields(scan)
        meta["structure_columns_available"] = sem3["structure_columns_available"]
        meta["classification_like_available"] = sem3["classification_like_available"]
        meta["classref_available"] = sem3.get("classref_available") or []
        meta["unavailable"] = sem3["unavailable"]
    else:
        meta["unavailable"] = lightweight_structure_unavailable(project, ifc_file=ifc_file)

    curated_by_key = {
        str(d["key"]): d
        for d in (
            *meta["structure_columns_available"],
            *meta["classification_like_available"],
            *(meta.get("classref_available") or []),
        )
    }
    allowed_prop_keys = {d["key"] for d in available} | set(curated_by_key)
    selected_valid = [
        k
        for k in selected_cols
        if k in allowed_prop_keys
        or is_spatial_column_key(k)
        or is_classref_column_key(k)
        or source_property_from_column_key(k)
    ]
    selected_valid = selected_valid[:MAX_SELECTED_PROP_COLS]
    meta["selected_prop_columns"] = selected_valid

    grain_counters = scan.get("grain_counters") or {}
    class_counters = scan.get("class_counters") or {}
    instance_values = scan.get("instance_values") or {}
    instance_complex = scan.get("instance_complex") or {}

    # COLUMNS-10B: index instance GlobalIds by hierarchy parent/class identity.
    gids_by_type_parent: dict[str, list[str]] = defaultdict(list)
    gids_by_class_key: dict[str, list[str]] = defaultdict(list)
    for prow in all_rows:
        if _str_val(prow.get("level")).lower() != "instance":
            continue
        igid = _str_val(prow.get("global_id"))
        if not igid:
            continue
        parent_key = _str_val(prow.get("parent_key"))
        if parent_key:
            gids_by_type_parent[parent_key].append(igid)
        class_key = _str_val(prow.get("class_key"))
        if class_key:
            gids_by_class_key[class_key].append(igid)

    cat_cov = 0
    fam_cov = 0
    prop_cov: dict[str, int] = {k: 0 for k in selected_valid}

    for row in all_rows:
        ifc_class = _str_val(row.get("ifc_class"))
        type_name = _str_val(row.get("type_name"))
        level = _str_val(row.get("level")).lower()
        gid = _str_val(row.get("global_id"))
        node_key = _str_val(row.get("node_key"))
        member_gids: list[str] = []
        if level == "type" and node_key:
            member_gids = list(gids_by_type_parent.get(node_key) or [])
        elif level == "class" and node_key:
            member_gids = list(gids_by_class_key.get(node_key) or [])
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
        if level == "instance" and gid:
            cat = (instance_values.get("Other.Category") or {}).get(gid) or ""
            fam = (instance_values.get("Other.Family") or {}).get(gid) or ""
        elif member_gids:
            cat_texts = [
                (instance_values.get("Other.Category") or {}).get(g) or "" for g in member_gids
            ]
            fam_texts = [
                (instance_values.get("Other.Family") or {}).get(g) or "" for g in member_gids
            ]
            cat_agg = aggregate_property_values([t for t in cat_texts if t])
            fam_agg = aggregate_property_values([t for t in fam_texts if t])
            cat = (
                ""
                if cat_agg.get("status") == "missing"
                else _str_val(cat_agg.get("value") if cat_agg.get("status") == "single" else "")
            )
            if cat_agg.get("status") == "mixed":
                cat = MIXED_VALUES_LABEL
            fam = (
                ""
                if fam_agg.get("status") == "missing"
                else _str_val(fam_agg.get("value") if fam_agg.get("status") == "single" else "")
            )
            if fam_agg.get("status") == "mixed":
                fam = MIXED_VALUES_LABEL
        else:
            cat = _majority(cat_counter)
            fam = _majority(fam_counter)
        row["semantic_category"] = cat
        row["semantic_family"] = fam
        if cat:
            cat_cov += 1
        if fam:
            fam_cov += 1

        prop_map: dict[str, Any] = {}
        cells: list[dict[str, Any]] = []
        for col_key in selected_valid:
            src_or_key = (
                col_key
                if is_spatial_column_key(col_key)
                else resolve_column_source_property(col_key)
            )
            agg = _aggregate_column_for_row(
                level=level,
                global_id=gid,
                ifc_class=ifc_class,
                type_name=type_name,
                source_key=src_or_key,
                grain_counters=grain_counters,
                class_counters=class_counters,
                instance_values=instance_values,
                instance_complex=instance_complex,
                member_global_ids=member_gids or None,
            )
            prop_map[col_key] = agg
            row[col_key] = agg["display"]
            cells.append(
                {
                    "key": col_key,
                    "display": agg["display"],
                    "status": agg["status"],
                    "distinct_count": agg["distinct_count"],
                    "detail_values": list(agg.get("detail_values") or []),
                    "numeric_value": agg.get("numeric_value"),
                    "title": (
                        "; ".join(agg.get("detail_values") or [])
                        if agg["status"] == "mixed"
                        else agg["display"]
                    ),
                }
            )
            if agg["status"] not in {"missing", "unsupported"}:
                prop_cov[col_key] = prop_cov.get(col_key, 0) + 1
        row["prop_columns"] = prop_map
        row["prop_column_cells"] = cells

    meta["enriched"] = cat_cov > 0 or fam_cov > 0 or any(prop_cov.values())
    meta["hint_coverage"] = {
        "semantic_category": cat_cov,
        "semantic_family": fam_cov,
    }
    meta["property_column_coverage"] = prop_cov
    meta["property_sets_indexed_on_prep"] = (
        bool(available)
        or bool(meta["structure_columns_available"])
        or bool(meta.get("classref_available"))
        or bool(selected_valid)
    )
    meta["scan_summary"] = {
        "key_nonempty": dict(scan.get("key_nonempty") or {}) if discover_catalogue else {},
        "spatial_nonempty": dict(scan.get("spatial_nonempty") or {}),
    }
    if (
        meta["property_sets_indexed_on_prep"]
        or meta["structure_columns_available"]
        or selected_valid
    ):
        meta["helper"] = (
            "Add indexed IFC properties, Qto measures, spatial or classification "
            "fields as columns. Instance cells show the leaf value; Class/Type "
            "show aggregates unless a Calculation is configured."
        )
    logger.debug(
        "ifc semantic enrich project=%s scanned=%s props=%s catalogue=%s",
        getattr(project, "pk", None),
        meta["entity_count_scanned"],
        len(selected_valid),
        discover_catalogue,
    )
    return meta


def _aggregate_column_for_row(
    *,
    level: str,
    global_id: str,
    ifc_class: str,
    type_name: str,
    source_key: str,
    grain_counters: Mapping[str, Any],
    class_counters: Mapping[str, Any],
    instance_values: Mapping[str, Any],
    instance_complex: Mapping[str, Any],
    member_global_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Resolve one catalogue column for one hierarchy/legacy row.

    COLUMNS-10B: Class/Type parents prefer ``member_global_ids`` (hierarchy
    parent_key / class_key identity). Never regroup same-name types by display
    name when member identities are available.
    """
    from takeoff.services.quantity_entity_filter import try_parse_number

    key = _str_val(source_key)
    if not key:
        return aggregate_property_values([])

    if level == "instance" and global_id:
        complex_gids = instance_complex.get(key) or set()
        if global_id in complex_gids:
            return {
                "status": "unsupported",
                "value": "Complex value",
                "display": "Complex value",
                "distinct_count": 0,
                "detail_values": [],
                "numeric_value": None,
            }
        text = (instance_values.get(key) or {}).get(global_id)
        if text is None or text == "":
            agg = aggregate_property_values([])
        else:
            agg = aggregate_property_values([text])
        num = (
            try_parse_number(str(agg.get("value") or "")) if agg.get("status") == "single" else None
        )
        agg["numeric_value"] = num
        return agg

    # Type/Class: aggregate leaf instance texts by stable identities when known.
    members = [g for g in (member_global_ids or []) if g]
    if members:
        texts: list[str] = []
        complex_gids = instance_complex.get(key) or set()
        for gid in members:
            if gid in complex_gids:
                continue
            text = (instance_values.get(key) or {}).get(gid)
            if text is None or text == "":
                continue
            texts.append(str(text))
        agg = aggregate_property_values(texts)
        if agg.get("status") == "single":
            agg["numeric_value"] = try_parse_number(str(agg.get("value") or ""))
        else:
            agg["numeric_value"] = None
        return agg

    # Legacy / non-hierarchy fallback — class counters or name grain.
    if level == "class" or (level != "type" and not type_name):
        class_map = class_counters.get(key) or {}
        ctr = class_map.get(ifc_class) or Counter()
    else:
        ctr = _resolve_grain_counter(
            grain_counters.get(key) or {},
            class_counters.get(key) or {},
            ifc_class,
            type_name,
        )
    expanded: list[str] = []
    for val, count in ctr.items():
        expanded.extend([val] * int(count))
    agg = aggregate_property_values(expanded)
    if agg.get("status") == "single":
        agg["numeric_value"] = try_parse_number(str(agg.get("value") or ""))
    else:
        agg["numeric_value"] = None
    return agg


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
        # Keep TABLE-04 primary filter keys listed even with empty coverage/samples.
        if (
            coverage <= 0
            and key != "ifc_class"
            and key not in PRIMARY_FILTER_FIELD_KEYS
            and not samples
            and key in {"type_name", "quantity_source"}
        ):
            continue
        fields.append(
            {
                "key": key,
                "label": spec["label"],
                "source": spec["source"],
                "source_property": spec["source_property"],
                "source_context": "Preparation",
                "coverage": coverage if key != "ifc_class" else total,
                "sample_values": samples,
                "value_type": "numeric" if key == "element_count" else "text",
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
    structure_columns = list(meta.get("structure_columns_available") or [])
    classification_like = list(meta.get("classification_like_available") or [])
    classref_cols = list(meta.get("classref_available") or [])
    selected = list(meta.get("selected_prop_columns") or [])
    curated_all = {
        str(d.get("key")): d for d in (*structure_columns, *classification_like, *classref_cols)
    }
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
    for desc in (*structure_columns, *classification_like, *classref_cols):
        key = str(desc.get("key") or "")
        if key not in selected:
            continue
        selected_seen.add(key)
        coverage = int((meta.get("property_column_coverage") or {}).get(key) or 0)
        fields.append(
            {
                **desc,
                "coverage": coverage or int(desc.get("coverage") or 0),
                "sample_values": _distinct_samples(rows, key)
                or list(desc.get("sample_values") or [])[:SAMPLE_VALUE_CAP],
                "is_filterable": True,
                "is_sortable": False,
            }
        )
    for key in selected:
        if key in selected_seen:
            continue
        if is_spatial_column_key(key):
            fields.append(
                {
                    "key": key,
                    "label": (
                        "Level / Storey (spatial)"
                        if key == SPATIAL_STOREY_KEY
                        else "Spatial container"
                    ),
                    "group": "Model structure",
                    "source": "ifc_spatial",
                    "source_property": None,
                    "coverage": int((meta.get("property_column_coverage") or {}).get(key) or 0),
                    "sample_values": _distinct_samples(rows, key),
                    "is_filterable": True,
                    "is_sortable": False,
                }
            )
            continue
        src = source_property_from_column_key(key)
        if not src:
            continue
        curated = curated_all.get(key)
        fields.append(
            {
                "key": key,
                "label": (curated or {}).get("label") or _property_label(src),
                "group": (curated or {}).get("group") or _property_group(src),
                "source": (curated or {}).get("source") or "ifc_entity_properties",
                "source_property": src,
                "coverage": int((meta.get("property_column_coverage") or {}).get(key) or 0),
                "sample_values": _distinct_samples(rows, key),
                "is_filterable": True,
                "is_sortable": False,
            }
        )

    filterable = [f for f in fields if f.get("is_filterable")]
    # DYNAMIC-07: filter catalogue = prep-native + ALL discovered IFC fields
    # (not only selected columns / four primary keys).
    from takeoff.services.quantity_editable_table import COLUMNS_UI_EXCLUDED_SOURCE_PROPS
    from takeoff.services.quantity_entity_filter import (
        OP_LABELS,
        operators_for_value_type,
    )

    catalogue_by_key: dict[str, dict[str, Any]] = {}
    for f in filterable:
        catalogue_by_key[str(f.get("key"))] = dict(f)
    for desc in property_columns:
        key = str(desc.get("key") or "")
        if not key or key in catalogue_by_key:
            continue
        if str(desc.get("source_property") or "") in COLUMNS_UI_EXCLUDED_SOURCE_PROPS:
            continue
        entry = {
            **desc,
            "is_filterable": True,
            "is_sortable": False,
            "sample_values": list(desc.get("sample_values") or [])[:SAMPLE_VALUE_CAP],
        }
        catalogue_by_key[key] = entry
        filterable.append(entry)
    for desc in (*structure_columns, *classification_like, *classref_cols):
        key = str(desc.get("key") or "")
        if not key or key in catalogue_by_key:
            continue
        entry = {
            **desc,
            "value_type": desc.get("value_type") or "text",
            "is_filterable": True,
            "is_sortable": False,
            "sample_values": list(desc.get("sample_values") or [])[:SAMPLE_VALUE_CAP],
        }
        catalogue_by_key[key] = entry
        filterable.append(entry)

    for f in filterable:
        vt = str(f.get("value_type") or "text")
        f["value_type"] = vt
        f["operators"] = [
            {"key": op, "label": OP_LABELS.get(op, op)} for op in operators_for_value_type(vt)
        ]
        if not f.get("source_context"):
            f["source_context"] = (
                f.get("group") or f.get("source_property") or f.get("source") or ""
            )
        # Comparison unit for numeric Qto / measure fields (project-unit fallback).
        if vt == "numeric" and not f.get("unit_label"):
            src = str(f.get("source_property") or "")
            low = src.lower()
            if "volume" in low:
                f["unit_label"] = "project volume unit"
            elif "area" in low:
                f["unit_label"] = "project area unit"
            elif "length" in low or "width" in low or "height" in low:
                f["unit_label"] = "project length unit"

    primary_filter_fields = [
        f
        for f in filterable
        if str(f.get("key"))
        not in {
            "semantic_category",
            "semantic_family",
            "category",
            "family",
            "ifc_class",
        }
        and str(f.get("source_property") or "") not in COLUMNS_UI_EXCLUDED_SOURCE_PROPS
        and str(f.get("label") or "").lower() not in {"category", "family"}
    ]
    return {
        "fields": fields,
        "filterable_fields": filterable,
        "primary_filter_fields": primary_filter_fields,
        "filter_catalogue": primary_filter_fields,
        "property_columns_available": property_columns,
        "structure_columns_available": structure_columns,
        "classification_like_available": classification_like,
        "unavailable": dict(meta.get("unavailable") or {}),
        "selected_prop_columns": selected,
        "prep_row_total": total,
        "helper": meta.get("helper")
        or (
            "Choose any indexed IFC field for Columns or Filter. "
            "Category and Family stay out of user-facing choices."
        ),
        "property_sets_available_on_prep": bool(property_columns) or bool(structure_columns),
        "entity_enrichment": {
            "enriched": bool(meta.get("enriched")),
            "entity_count_scanned": int(meta.get("entity_count_scanned") or 0),
            "hint_coverage": dict(meta.get("hint_coverage") or {}),
            "property_column_coverage": dict(meta.get("property_column_coverage") or {}),
            "scan_summary": dict(meta.get("scan_summary") or {}),
        },
    }


def filter_prep_rows_by_semantic(
    prep_rows: Sequence[Mapping[str, Any]],
    *,
    field_key: str,
    value: str,
    op: str = "eq",
    value_type: str = "text",
    allowed_extra_keys: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return prep rows matching the semantic condition (row-level / post-agg)."""
    from takeoff.services.quantity_entity_filter import compare_values

    key = _str_val(field_key)
    target = _str_val(value)
    op_key = _str_val(op) or "eq"
    if not key:
        return [dict(r) for r in prep_rows]
    if op_key not in {"is_missing", "is_present"} and not target and value_type != "boolean":
        return [dict(r) for r in prep_rows]
    allowed = {f["key"] for f in PREP_NATIVE_FIELDS if f["is_filterable"]} | {
        f["key"] for f in ENTITY_HINT_FIELDS
    }
    for extra in allowed_extra_keys or []:
        if _str_val(extra):
            allowed.add(_str_val(extra))
    if key.startswith(PROP_KEY_PREFIX):
        allowed.add(key)
    if is_spatial_column_key(key):
        allowed.add(key)
    if is_classref_column_key(key):
        allowed.add(key)
    if key not in allowed:
        return [dict(r) for r in prep_rows]
    out: list[dict[str, Any]] = []
    for row in prep_rows:
        if compare_values(
            actual=row.get(key),
            op=op_key,
            expected=target,
            value_type=value_type,
        ):
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
    if add and add not in cols and len(cols) < MAX_SELECTED_PROP_COLS:
        if is_spatial_column_key(add) or is_classref_column_key(add):
            cols = [*cols, add]
        elif add.startswith(PROP_KEY_PREFIX):
            src = source_property_from_column_key(add)
            if src and not _is_noisy_property_key(src):
                cols = [*cols, prop_column_key(src)]
    return {
        "field": _str_val(query.get(SEMANTIC_QUERY_FIELD)),
        "value": _str_val(query.get(SEMANTIC_QUERY_VALUE)),
        "op": _str_val(query.get(SEMANTIC_QUERY_OP)) or "eq",
        "sort": _str_val(query.get(SEMANTIC_QUERY_SORT)),
        "sort_dir": _str_val(query.get(SEMANTIC_QUERY_SORT_DIR)) or "asc",
        "sem_cols": cols,
    }


def apply_semantic_filters_to_qty_prep(
    qty_prep: MutableMapping[str, Any],
    *,
    project: Any,
    query: Mapping[str, Any],
    ifc_file: Any | None = None,
    entity_predicate: Any | None = None,
    entity_filter_applied: bool = False,
    defer_field_catalogue: bool = True,
) -> dict[str, Any]:
    """Enrich, discover, filter, and sort prep rows in-place. Returns panel context.

    PERF-15C1: when ``defer_field_catalogue`` is True (default), skip full
    catalogue discovery on the page GET and leave picker hierarchies empty for
    lazy loading. Row enrichment for visible property columns still runs.
    """
    from takeoff.services.quantity_entity_filter import (
        condition_chip_label,
        is_entity_level_field,
        operators_for_value_type,
    )

    rows = list(qty_prep.get("prep_rows") or [])
    export_rows = [
        r
        for r in (qty_prep.get("prep_rows_export") or [])
        if isinstance(r, dict) and not r.get("is_load_more")
    ]
    total_before = len(rows)
    params = parse_semantic_query(query)
    enrichment = enrich_prep_rows_with_entity_semantics(
        project,
        rows,
        selected_prop_columns=params["sem_cols"],
        ifc_file=ifc_file,
        entity_predicate=entity_predicate,
        extra_rows=export_rows,
        discover_catalogue=not defer_field_catalogue,
    )
    discovery = discover_semantic_fields(project, rows, enrichment_meta=enrichment)
    selected_classes = [str(c) for c in (qty_prep.get("selected_classes") or []) if str(c).strip()]
    if selected_classes and not defer_field_catalogue:
        scoped_catalogue: list[dict[str, Any]] = []
        selected_set = set(selected_classes)
        for f in discovery.get("filter_catalogue") or []:
            entry = dict(f)
            present = {str(c) for c in (entry.get("classes_present") or []) if str(c)}
            # Prep-native / fields without class coverage stay available.
            if not present and not str(entry.get("key") or "").startswith("prop:"):
                entry["scope_note"] = ""
                scoped_catalogue.append(entry)
                continue
            if not present:
                # Unknown coverage — keep but mark.
                entry["scope_note"] = "Class coverage unknown"
                scoped_catalogue.append(entry)
                continue
            overlap = present & selected_set
            if not overlap:
                continue
            if selected_set - present:
                entry["scope_note"] = "Present in some selected classes only: " + ", ".join(
                    sorted(overlap)
                )
                entry["partial_in_scope"] = True
            else:
                entry["scope_note"] = ""
                entry["partial_in_scope"] = False
            scoped_catalogue.append(entry)
        discovery["filter_catalogue"] = scoped_catalogue
        discovery["primary_filter_fields"] = scoped_catalogue
        discovery["filterable_fields"] = scoped_catalogue

    # Class selector owns IFC Class scope — ignore redundant Field=ifc_class so
    # chips stay “IFC Class: IfcColumn” (not “… · IFC Class equals IfcColumn”).
    # Legacy Field-only URLs (no semantic_classes) still evaluate the field filter.
    filter_params = dict(params)
    if filter_params.get("field") == "ifc_class" and selected_classes:
        filter_params["field"] = ""
        filter_params["value"] = ""
        filter_params["op"] = "eq"

    field_meta = {str(f.get("key")): f for f in (discovery.get("filter_catalogue") or [])}
    active_field_meta = field_meta.get(filter_params["field"]) or {}
    query_vt = _str_val(query.get("semantic_value_type"))
    value_type = (
        query_vt
        or str(active_field_meta.get("value_type") or "")
        or guess_value_type_for_field_key(filter_params["field"])
        or "text"
    )
    unit_label = str(active_field_meta.get("unit_label") or "")
    active_field_label = (
        str(active_field_meta.get("label") or "")
        or label_for_stable_field_key(filter_params["field"])
        or filter_params["field"]
    )

    filtered = rows
    active_filter = False
    allowed_extra = (
        list(enrichment.get("selected_prop_columns") or [])
        + [d["key"] for d in (enrichment.get("property_columns_available") or [])]
        + [d["key"] for d in (enrichment.get("structure_columns_available") or [])]
        + [d["key"] for d in (enrichment.get("classification_like_available") or [])]
        + [d["key"] for d in (enrichment.get("classref_available") or [])]
        + [d["key"] for d in (discovery.get("filter_catalogue") or [])]
    )
    if filter_params["field"]:
        allowed_extra.append(filter_params["field"])
    op = filter_params.get("op") or "eq"
    has_condition = bool(filter_params["field"]) and (
        op in {"is_missing", "is_present"} or bool(filter_params["value"])
    )
    if has_condition:
        active_filter = True
        # Entity-level filters already reshaped aggregates; do not re-filter
        # on "Multiple values" display text.
        if not (entity_filter_applied and is_entity_level_field(filter_params["field"])):
            filtered = filter_prep_rows_by_semantic(
                rows,
                field_key=filter_params["field"],
                value=filter_params["value"],
                op=op,
                value_type=value_type,
                allowed_extra_keys=allowed_extra,
            )
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
        str(d.get("key")): d
        for d in (
            *(enrichment.get("property_columns_available") or []),
            *(enrichment.get("structure_columns_available") or []),
            *(enrichment.get("classification_like_available") or []),
            *(enrichment.get("classref_available") or []),
            *CLASSREF_CURATED,
        )
    }
    for key in selected_cols:
        desc = avail_by_key.get(key)
        if desc is None:
            if is_spatial_column_key(key):
                desc = {
                    "key": key,
                    "label": (
                        "Level / Storey (spatial)"
                        if key == SPATIAL_STOREY_KEY
                        else "Spatial container"
                    ),
                    "source_property": None,
                    "value_type": "text",
                }
            else:
                src = source_property_from_column_key(key)
                desc = {
                    "key": key,
                    "label": label_for_stable_field_key(key) or _property_label(src),
                    "source_property": src,
                }
        else:
            desc = dict(desc)
        # PERF-15C1: deferred catalogue may omit value_type — infer for calc/filter UX.
        src = str(desc.get("source_property") or source_property_from_column_key(key) or "")
        samples = _distinct_samples(rows, key)
        from takeoff.services.quantity_entity_filter import infer_value_type

        vt = str(desc.get("value_type") or "")
        if vt not in {"numeric", "number", "measure", "real", "integer", "float", "boolean"}:
            guessed = guess_value_type_for_field_key(key)
            if guessed == "numeric":
                vt = "numeric"
            else:
                vt = infer_value_type(samples, source_property=src)
            if vt not in {"numeric", "number", "measure", "real", "integer", "float"}:
                for r in [*rows, *export_rows]:
                    agg = (r.get("prop_columns") or {}).get(key)
                    if isinstance(agg, dict) and agg.get("numeric_value") is not None:
                        vt = "numeric"
                        break
        desc["value_type"] = vt or "text"
        selected_meta.append(desc)

    class_chip = ""
    if selected_classes:
        class_chip = "IFC Class: " + ", ".join(selected_classes)
    field_chip = (
        condition_chip_label(
            field_label=active_field_label or "Field",
            op=op,
            value=filter_params["value"],
            unit_label=unit_label,
        )
        if active_filter
        else ""
    )
    active_chip = " · ".join(p for p in (class_chip, field_chip) if p)

    from takeoff.services.quantity_field_catalogue import (
        build_picker_hierarchy,
        field_meta_json_by_key,
        samples_json_by_key,
    )

    catalogue_fields = list(discovery.get("filter_catalogue") or [])
    # Class-scope Add-column prop list the same way as the filter catalogue.
    if selected_classes and not defer_field_catalogue:
        selected_set = set(selected_classes)
        scoped_props: list[dict[str, Any]] = []
        for d in enrichment.get("property_columns_available") or []:
            entry = dict(d)
            present = {str(c) for c in (entry.get("classes_present") or []) if str(c)}
            if present and not (present & selected_set):
                continue
            if present and (selected_set - present):
                entry["partial_in_scope"] = True
                entry["scope_note"] = "Present in some selected classes only: " + ", ".join(
                    sorted(present & selected_set)
                )
            scoped_props.append(entry)
        discovery["property_columns_available"] = scoped_props

    picker_fields = list(catalogue_fields)
    seen_picker = {str(f.get("key")) for f in picker_fields if f.get("key")}
    for d in (
        *(discovery.get("property_columns_available") or []),
        *(enrichment.get("structure_columns_available") or []),
        *(enrichment.get("classref_available") or []),
    ):
        k = str(d.get("key") or "")
        if k and k not in seen_picker:
            picker_fields.append(dict(d))
            seen_picker.add(k)

    if defer_field_catalogue:
        picker_hierarchy: list[dict[str, Any]] = []
        column_picker_hierarchy: list[dict[str, Any]] = []
        # Bootstrap meta for prep-native + active field only (not full catalogue).
        bootstrap_fields: list[dict[str, Any]] = []
        for spec in PREP_NATIVE_FIELDS:
            if not spec.get("is_filterable"):
                continue
            vt = "numeric" if spec["key"] == "element_count" else "text"
            bootstrap_fields.append(
                {
                    "key": spec["key"],
                    "label": spec["label"],
                    "value_type": vt,
                    "operators": [{"key": op_k} for op_k in operators_for_value_type(vt)],
                }
            )
        if filter_params["field"] and filter_params["field"] not in {
            f["key"] for f in bootstrap_fields
        }:
            bootstrap_fields.append(
                {
                    "key": filter_params["field"],
                    "label": active_field_label,
                    "value_type": value_type,
                    "unit_label": unit_label,
                    "operators": [{"key": op_k} for op_k in operators_for_value_type(value_type)],
                }
            )
        field_meta_json = field_meta_json_by_key(bootstrap_fields)
        field_samples_json = "{}"
    else:
        picker_hierarchy = build_picker_hierarchy(picker_fields)
        from takeoff.services.quantity_table_layout import OPTIONAL_TABLE_COLUMNS

        column_picker_hierarchy = build_picker_hierarchy(
            picker_fields,
            include_table_fields=list(OPTIONAL_TABLE_COLUMNS),
        )
        field_meta_json = field_meta_json_by_key(catalogue_fields)
        field_samples_json = samples_json_by_key(picker_fields)

    unavailable = dict(enrichment.get("unavailable") or {})
    if "zone" not in unavailable:
        unavailable["zone"] = static_zone_unavailable()

    panel = {
        **discovery,
        "active_field": filter_params["field"],
        "active_field_label": active_field_label if filter_params["field"] else "",
        "active_value": filter_params["value"],
        "active_op": op if filter_params["field"] else "eq",
        "active_value_type": value_type if filter_params["field"] else "text",
        "active_unit_label": unit_label if filter_params["field"] else "",
        "active_chip": active_chip,
        "active_sort": params["sort"],
        "active_sort_dir": params["sort_dir"],
        "showing_count": len(filtered),
        "total_count": int(qty_prep.get("baseline_row_count") or total_before),
        "baseline_row_count": int(qty_prep.get("baseline_row_count") or total_before),
        "filter_active": bool(active_filter or selected_classes),
        "entity_filter_applied": bool(entity_filter_applied),
        "selected_classes": list(selected_classes),
        "selected_classes_display": (
            selected_classes[0]
            if len(selected_classes) == 1
            else (
                f"{len(selected_classes)} classes"
                if len(selected_classes) > 3
                else ", ".join(selected_classes)
            )
            if selected_classes
            else ""
        ),
        "selected_classes_label": (
            ", ".join(selected_classes) if selected_classes else "All classes"
        ),
        "available_classes": list(qty_prep.get("available_classes") or []),
        # TABLE-04: Category/Family stay out of the primary table UX.
        "show_category_column": False,
        "show_family_column": False,
        "selected_prop_column_meta": selected_meta,
        "sem_cols_param": ",".join(selected_cols),
        "structure_columns_available": list(enrichment.get("structure_columns_available") or []),
        "classification_like_available": list(
            enrichment.get("classification_like_available") or []
        ),
        "classref_available": list(enrichment.get("classref_available") or []),
        "unavailable": unavailable,
        "picker_hierarchy": picker_hierarchy,
        "column_picker_hierarchy": column_picker_hierarchy,
        "field_catalogue_lazy": bool(defer_field_catalogue),
        "field_samples_json": field_samples_json,
        "field_meta_json": field_meta_json,
        "sample_value_cap": SAMPLE_VALUE_CAP,
        "mapping_distinction_note": (
            "Three layers stay separate: (1) IFC classification references from this "
            "export, (2) authoring properties such as OmniClass / Assembly Code, "
            "(3) Castor schema mapping targets for this 5D model."
        ),
    }
    qty_prep["semantic_filters"] = panel
    return panel


class IfcSemanticFieldService:
    """Project-scoped façade for IFC-SEM-1/2/3 discovery/enrichment."""

    def __init__(self, project: Any, user: Any | None = None) -> None:
        self.project = project
        self.user = user

    def apply_to_qty_prep(
        self,
        qty_prep: MutableMapping[str, Any],
        query: Mapping[str, Any],
        *,
        ifc_file: Any | None = None,
        entity_predicate: Any | None = None,
        entity_filter_applied: bool = False,
    ) -> dict[str, Any]:
        """Enrich and filter ``qty_prep`` using query params."""
        try:
            return apply_semantic_filters_to_qty_prep(
                qty_prep,
                project=self.project,
                query=query,
                ifc_file=ifc_file,
                entity_predicate=entity_predicate,
                entity_filter_applied=entity_filter_applied,
            )
        except Exception:
            logger.exception(
                "ifc semantic filters failed project=%s", getattr(self.project, "pk", None)
            )
            qty_prep.setdefault(
                "semantic_filters",
                {
                    "fields": [],
                    "filterable_fields": [],
                    "primary_filter_fields": [],
                    "filter_catalogue": [],
                    "property_columns_available": [],
                    "selected_prop_columns": [],
                    "helper": "Semantic filters temporarily unavailable.",
                    "showing_count": len(qty_prep.get("prep_rows") or []),
                    "total_count": len(qty_prep.get("prep_rows") or []),
                    "filter_active": False,
                    "show_category_column": False,
                    "show_family_column": False,
                    "property_sets_available_on_prep": False,
                },
            )
            return qty_prep["semantic_filters"]
