# ifc_processor/services/schedule_service.py
"""IFC schedule builder — generic dynamic schedule and legacy door/window schedule."""

from __future__ import annotations

import logging
import re
import statistics
from typing import TYPE_CHECKING, Any, NamedTuple

from django.db.models import Count

from ifc_processor.models import IFCEntity, IFCFile, IFCSpatialElement

if TYPE_CHECKING:
    from ifc_processor.models import IFCElementType
from ifc_processor.templatetags.ifc_filters import get_unit_type_for_quantity, smart_round

logger = logging.getLogger(__name__)


def _entity_storey_name(entity: IFCEntity) -> str:
    """Walk the spatial_container chain to find the storey name."""
    node = getattr(entity, "spatial_container", None)
    while node:
        if node.spatial_type == "building_storey":
            return node.entity.name if hasattr(node, "entity") and node.entity else ""
        node = node.parent
    return ""


def _entity_space_name(entity: IFCEntity) -> str:
    """Walk the spatial_container chain to find the space name."""
    node = getattr(entity, "spatial_container", None)
    while node:
        if node.spatial_type == "space":
            return node.entity.name if hasattr(node, "entity") and node.entity else ""
        node = node.parent
    return ""


def _get_storey_descendant_ids(storey_name: str, ifc_file: IFCFile) -> list:
    """Return IFCSpatialElement IDs for the named storey and all its descendants."""
    from collections import defaultdict

    try:
        storey_node = IFCSpatialElement.objects.get(
            ifc_file=ifc_file,
            spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
            entity__name=storey_name,
        )
    except (IFCSpatialElement.DoesNotExist, IFCSpatialElement.MultipleObjectsReturned):
        return []

    all_spatial = IFCSpatialElement.objects.filter(ifc_file=ifc_file).values("id", "parent_id")
    children_map = defaultdict(list)
    for s in all_spatial:
        if s["parent_id"]:
            children_map[s["parent_id"]].append(s["id"])

    result = []
    stack = [storey_node.pk]
    while stack:
        current = stack.pop()
        result.append(current)
        stack.extend(children_map.get(current, []))
    return result


# ---------------------------------------------------------------------------
# IFC type → category mapping (mirrors parser.py RELEVANT_TYPES groupings)
# ---------------------------------------------------------------------------

IFC_TYPE_CATEGORIES: dict[str, list[str]] = {
    "Structural": [
        "IfcWall",
        "IfcWallStandardCase",
        "IfcColumn",
        "IfcBeam",
        "IfcSlab",
        "IfcFooting",
        "IfcPile",
        "IfcRoof",
    ],
    "Architectural": [
        "IfcDoor",
        "IfcWindow",
        "IfcStair",
        "IfcStairFlight",
        "IfcRamp",
        "IfcCurtainWall",
        "IfcRailing",
    ],
    "MEP": [
        "IfcFlowTerminal",
        "IfcFlowSegment",
        "IfcDistributionElement",
        "IfcSanitaryTerminal",
        "IfcFireSuppressionTerminal",
    ],
    "Spaces": ["IfcSpace", "IfcZone"],
    "Furnishing": ["IfcFurniture", "IfcFurnishingElement"],
    "Generic": ["IfcBuildingElementProxy", "IfcCovering", "IfcPlate"],
    "Infrastructure": [
        "IfcBridge",
        "IfcBridgePart",
        "IfcRoad",
        "IfcRoadPart",
        "IfcRailway",
        "IfcRailwayPart",
        "IfcTunnel",
        "IfcTunnelPart",
        "IfcMarineFacility",
        "IfcMarineFacilityPart",
        "IfcCourse",
        "IfcPavement",
        "IfcKerb",
        "IfcSign",
        "IfcSignal",
        "IfcTrackElement",
        "IfcMarking",
        "IfcEarthworksCut",
        "IfcEarthworksFill",
        "IfcBearing",
        "IfcMooringDevice",
        "IfcNavigationElement",
        "IfcConveyorSegment",
        "IfcLiquidTerminal",
        "IfcTendonConduit",
        "IfcVibrationDamper",
    ],
}

_TYPE_TO_CATEGORY: dict[str, str] = {
    t: cat for cat, types in IFC_TYPE_CATEGORIES.items() for t in types
}

# Fixed columns always prepended to every schedule, in this order.
_FIXED_COLUMNS = ["GlobalID", "Name", "Description", "Tag", "Level", "Room"]

# Property key ordering: Pset_ < Qto_ < Type. < other
_PSET_PATTERN = re.compile(r"^Pset_", re.IGNORECASE)
_QTO_PATTERN = re.compile(r"^Qto_", re.IGNORECASE)
_TYPE_PATTERN = re.compile(r"^Type\.", re.IGNORECASE)


def _key_sort_order(key: str) -> tuple[int, str]:
    """Return a sort tuple that groups Pset_ → Qto_ → Type.* → other."""
    if _PSET_PATTERN.match(key):
        return (0, key)
    if _QTO_PATTERN.match(key):
        return (1, key)
    if _TYPE_PATTERN.match(key):
        return (3, key)
    return (2, key)


def _make_label(key: str) -> str:
    """
    Create a human-readable column label from a raw property key.

    Examples:
      "Pset_DoorCommon.FireRating"      → "FireRating"
      "Qto_DoorQuantities.Height"       → "Height"
      "Type.Pset_DoorCommon.Reference"  → "Type.Reference"
      "OverallWidth"                    → "OverallWidth"
    """
    if _TYPE_PATTERN.match(key):
        # Strip the "Type.Pset_XxxCommon." or "Type.Qto_Xxx." prefix
        remainder = key[len("Type.") :]
        dot = remainder.find(".")
        return f"Type.{remainder[dot + 1 :]}" if dot != -1 else remainder

    dot = key.find(".")
    if dot != -1:
        return key[dot + 1 :]
    return key


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class ScheduleColumn(NamedTuple):
    """Metadata for a single schedule column."""

    key: str  # Raw property key (used for row lookup)
    label: str  # Display label (shown in table header / Excel column)
    unit: str | None  # Display label (e.g. "mm", "m²") or None for non-dimension columns


class ScheduleResult(NamedTuple):
    """Result of a schedule query for one IFC type."""

    ifc_type: str
    columns: list[ScheduleColumn]
    rows: list[list]  # Each row is a list of values in column order
    total: int


class TypeGroupRow(NamedTuple):
    """One row in a type-grouped schedule — represents one IFCElementType."""

    element_type_name: str  # Type object name (e.g. "Door-Single-Panel")
    element_type_id: str | None  # UUID PK for linking (None for untyped group)
    count: int  # Number of element occurrences
    values: list  # Property values in column order
    type_description: str  # IFC Description on the IfcTypeProduct (may be empty)
    type_tag: str  # IFC Tag on the IfcTypeProduct (may be empty)


class TypeGroupedResult(NamedTuple):
    """Result of a type-grouped schedule for one IFC class."""

    ifc_type: str  # e.g. "IfcDoor"
    columns: list[ScheduleColumn]
    groups: list[TypeGroupRow]
    total: int  # Sum of all group counts


# ---------------------------------------------------------------------------
# GenericScheduleService
# ---------------------------------------------------------------------------


class GenericScheduleService:
    """
    Build structured element schedules from IFCEntity records.

    Columns are discovered dynamically from stored properties JSONField.
    Dimension properties are unit-normalised using a median-based heuristic.
    No IFC file I/O is performed at runtime.
    """

    # Keywords that identify linear dimension properties (triggers unit detection)
    DIMENSION_KEYWORDS: frozenset[str] = frozenset(
        {"width", "height", "length", "depth", "thickness", "perimeter", "radius"}
    )

    def __init__(self, ifc_file: IFCFile) -> None:
        self.ifc_file = ifc_file

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_available_types(self) -> list[dict]:
        """
        Return all IFC types present in this file with counts and category.

        Each entry: {"ifc_type": str, "count": int, "category": str}
        Ordered by category (as defined in IFC_TYPE_CATEGORIES) then ifc_type.
        """
        rows = (
            IFCEntity.objects.filter(ifc_file=self.ifc_file)
            .values("ifc_type")
            .annotate(count=Count("id"))
            .order_by("ifc_type")
        )

        # Build category order index for stable sorting
        category_order = {cat: i for i, cat in enumerate(IFC_TYPE_CATEGORIES)}

        result = []
        for row in rows:
            ifc_type = row["ifc_type"]
            category = _TYPE_TO_CATEGORY.get(ifc_type, "Other")
            result.append({"ifc_type": ifc_type, "count": row["count"], "category": category})

        result.sort(key=lambda r: (category_order.get(r["category"], 99), r["ifc_type"]))
        return result

    def get_storeys(self, ifc_types: list[str] | None = None) -> list[str]:
        """
        Return distinct building storey names for this IFC file, ordered.

        Used to populate the storey filter dropdown.
        """
        return list(
            IFCSpatialElement.objects.filter(
                ifc_file=self.ifc_file,
                spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
            )
            .exclude(entity__name="")
            .values_list("entity__name", flat=True)
            .distinct()
            .order_by("entity__name")
        )

    def get_schedule(
        self,
        ifc_types: list[str],
        storey: str | None = None,
    ) -> dict[str, ScheduleResult]:
        """
        Build a schedule for each requested IFC type.

        Issues one DB query per ifc_type. Returns a dict keyed by ifc_type.
        """
        results: dict[str, ScheduleResult] = {}
        for ifc_type in ifc_types:
            results[ifc_type] = self._build_result(ifc_type, storey)
        return results

    def get_type_grouped_schedule(
        self,
        ifc_types: list[str],
        storey: str | None = None,
    ) -> dict[str, TypeGroupedResult]:
        """
        Build a type-grouped schedule for each requested IFC class.

        Groups element instances by their defining IFCElementType, showing
        one row per type with count and shared type-level properties.
        """
        results: dict[str, TypeGroupedResult] = {}
        for ifc_type in ifc_types:
            results[ifc_type] = self._build_type_grouped_result(ifc_type, storey)
        return results

    # ------------------------------------------------------------------
    # Internal: per-type schedule builder
    # ------------------------------------------------------------------

    def _build_result(self, ifc_type: str, storey: str | None) -> ScheduleResult:
        """Fetch entities, discover columns, resolve units, build rows."""
        qs = (
            IFCEntity.objects.filter(ifc_file=self.ifc_file, ifc_type=ifc_type)
            .select_related("spatial_container__entity", "spatial_container__parent__entity")
            .only(
                "global_id",
                "name",
                "ifc_description",
                "tag",
                "spatial_container",
                "properties",
            )
            .order_by("name")
        )
        if storey:
            descendant_ids = _get_storey_descendant_ids(storey, self.ifc_file)
            if descendant_ids:
                qs = qs.filter(spatial_container_id__in=descendant_ids)
            else:
                qs = qs.none()

        entities = list(qs)

        # Discover all distinct property keys across entities
        prop_keys = self._discover_keys(entities)

        # Resolve units: prefer declared project_units, fall back to heuristic
        project_units = self.ifc_file.project_units or {}
        if project_units:
            # Authoritative: use declared IFC units — no value conversion needed
            display_map = self._resolve_declared_units(prop_keys, project_units)
            convert_map: dict[str, str | None] = {}  # no conversions
        else:
            # Fallback: heuristic detection with conversion to metres
            heuristic_map = self._detect_units(prop_keys, entities)
            convert_map = heuristic_map
            display_map = {k: "m" for k, v in heuristic_map.items() if v in ("mm", "cm")}

        # Build column metadata (fixed columns first, then discovered)
        columns: list[ScheduleColumn] = [
            ScheduleColumn(key=k, label=k, unit=None) for k in _FIXED_COLUMNS
        ]
        for key in prop_keys:
            columns.append(
                ScheduleColumn(key=key, label=_make_label(key), unit=display_map.get(key))
            )

        # Build rows as ordered lists (parallel to columns)
        rows: list[list] = []
        for entity in entities:
            props = entity.properties or {}
            row: list[Any] = [
                entity.global_id,
                entity.name or "—",
                entity.ifc_description or "—",
                entity.tag or "—",
                _entity_storey_name(entity) or "—",
                _entity_space_name(entity) or "—",
            ]
            for key in prop_keys:
                raw = props.get(key)
                val = self._normalize(raw, convert_map.get(key))
                # Apply unit-aware rounding for declared units
                display_unit = display_map.get(key)
                if display_unit and isinstance(val, float):
                    val = smart_round(val, display_unit)
                row.append(val)
            rows.append(row)

        return ScheduleResult(
            ifc_type=ifc_type,
            columns=columns,
            rows=rows,
            total=len(rows),
        )

    def _build_type_grouped_result(self, ifc_type: str, storey: str | None) -> TypeGroupedResult:
        """Group entities by their IFCElementType and aggregate properties."""
        qs = (
            IFCEntity.objects.filter(ifc_file=self.ifc_file, ifc_type=ifc_type)
            .select_related("element_type")
            .only(
                "global_id",
                "name",
                "spatial_container",
                "properties",
                "element_type",
            )
            .order_by("element_type__name", "name")
        )
        if storey:
            descendant_ids = _get_storey_descendant_ids(storey, self.ifc_file)
            if descendant_ids:
                qs = qs.filter(spatial_container_id__in=descendant_ids)
            else:
                qs = qs.none()

        entities = list(qs)

        # Group entities by element_type
        groups_map: dict[str | None, list[IFCEntity]] = {}
        type_objects: dict[str | None, IFCElementType | None] = {}
        for entity in entities:
            et = entity.element_type
            key = str(et.pk) if et else None
            groups_map.setdefault(key, []).append(entity)
            if key not in type_objects:
                type_objects[key] = et

        # Discover columns from type-level properties across all types
        # Use the IFCElementType.properties (not the instance Type.* prefix)
        all_type_props: set[str] = set()
        for et in type_objects.values():
            if et and et.properties:
                all_type_props.update(et.properties.keys())

        # Sort property keys using the standard grouping
        prop_keys = sorted(
            (k for k in all_type_props if _make_label(k).lower() not in self._EXCLUDED_LABELS),
            key=_key_sort_order,
        )

        # Resolve units from type properties using the same logic
        project_units = self.ifc_file.project_units or {}
        if project_units:
            display_map = self._resolve_declared_units(prop_keys, project_units)
        else:
            display_map = {}

        # Build column metadata
        columns: list[ScheduleColumn] = []
        for key in prop_keys:
            columns.append(
                ScheduleColumn(key=key, label=_make_label(key), unit=display_map.get(key))
            )

        # Build group rows
        group_rows: list[TypeGroupRow] = []
        total = 0
        for key, group_entities in groups_map.items():
            et = type_objects[key]
            type_name = et.name if et else "(Untyped)"
            type_id = str(et.pk) if et else None
            count = len(group_entities)
            total += count

            # Values from the type object's properties
            type_props = et.properties if et else {}
            values: list[Any] = []
            for prop_key in prop_keys:
                raw = type_props.get(prop_key)
                val = self._normalize(raw, None)
                display_unit = display_map.get(prop_key)
                if display_unit and isinstance(val, float):
                    val = smart_round(val, display_unit)
                values.append(val)

            group_rows.append(
                TypeGroupRow(
                    element_type_name=type_name,
                    element_type_id=type_id,
                    count=count,
                    values=values,
                    type_description=(et.description if et else ""),
                    type_tag=(et.tag if et else ""),
                )
            )

        # Sort: untyped last, then alphabetically by type name
        group_rows.sort(key=lambda r: (r.element_type_id is None, r.element_type_name))

        return TypeGroupedResult(
            ifc_type=ifc_type,
            columns=columns,
            groups=group_rows,
            total=total,
        )

    # Labels (after stripping pset prefix) that are internal IFC/STEP artefacts.
    # These appear as e.g. "Pset_BeamCommon.id", "Qto_BeamBaseQuantities.id"
    # and have no meaning in an AEC schedule.
    _EXCLUDED_LABELS: frozenset[str] = frozenset({"id"})

    def _discover_keys(self, entities: list[IFCEntity]) -> list[str]:
        """
        Return the union of all property keys across entities, sorted by group.

        Grouping: Pset_* → other direct attrs → Qto_* → Type.*
        Keys whose display label matches _EXCLUDED_LABELS are omitted.
        """
        key_set: set[str] = set()
        for entity in entities:
            if entity.properties:
                key_set.update(entity.properties.keys())
        return sorted(
            (k for k in key_set if _make_label(k).lower() not in self._EXCLUDED_LABELS),
            key=_key_sort_order,
        )

    def _resolve_declared_units(
        self, keys: list[str], project_units: dict[str, str]
    ) -> dict[str, str]:
        """Map property keys to display unit labels using declared project units.

        Uses the quantity-name-to-unit-type mapping from ``ifc_filters`` to
        look up the authoritative unit label from ``IFCFile.project_units``.
        Returns only keys that have a matching declared unit.
        """
        display: dict[str, str] = {}
        for key in keys:
            label = _make_label(key)
            unit_type = get_unit_type_for_quantity(label)
            if unit_type and unit_type in project_units:
                display[key] = project_units[unit_type]
        return display

    def _detect_units(self, keys: list[str], entities: list[IFCEntity]) -> dict[str, str | None]:
        """
        Detect unit for each dimension key using a median-based heuristic.

        Returns a dict mapping key → detected unit string or None.
        Only keys whose name (after stripping pset prefix) contains a
        DIMENSION_KEYWORDS substring are analysed.
        """
        unit_map: dict[str, str | None] = {}
        for key in keys:
            label_lower = _make_label(key).lower()
            if not any(kw in label_lower for kw in self.DIMENSION_KEYWORDS):
                continue
            values = [
                (e.properties or {}).get(key)
                for e in entities
                if (e.properties or {}).get(key) is not None
            ]
            unit_map[key] = self._detect_unit(values)
        return unit_map

    def _detect_unit(self, values: list[Any]) -> str | None:
        """
        Infer the unit of a set of numeric dimension values.

        Heuristic (architectural element linear dimensions):
          median > 100  → stored in mm  (e.g. 900, 2100)
          median > 1.5  → stored in cm  (e.g. 90, 210)
          otherwise     → stored in m   (e.g. 0.9, 2.1)

        Returns "mm", "cm", "m", or None if no numeric values.
        """
        nums = [v for v in values if isinstance(v, (int, float))]
        if not nums:
            return None
        med = statistics.median(nums)
        if med > 100:
            return "mm"
        if med > 1.5:
            return "cm"
        return "m"

    def _normalize(self, value: Any, unit: str | None) -> Any:
        """
        Convert a raw value to a cell-safe scalar.

        - Numeric dimension values are converted to metres based on detected unit.
        - Lists and dicts (e.g. ['UNSET'], {'key': 'val'}) are coerced to strings
          so they are safe for both template rendering and Excel serialisation.
        """
        if isinstance(value, list):
            # Collapse sentinel lists like ['UNSET'], [''], [None] to None
            _sentinel = ("UNSET", "")
            flat = [v for v in value if v is not None and str(v).strip().upper() not in _sentinel]
            if not flat:
                return None
            return flat[0] if len(flat) == 1 else ", ".join(str(v) for v in flat)
        if isinstance(value, dict):
            return str(value)
        if not isinstance(value, (int, float)):
            return value
        if isinstance(value, bool):
            return value
        # Apply unit conversion for dimension columns
        if unit not in (None, "m"):
            divisor = 1000 if unit == "mm" else 100
            value = value / divisor
        # Round all non-integer floats to 2 decimal places
        if isinstance(value, float):
            return round(value, 2)
        return value


# ---------------------------------------------------------------------------
# Legacy: DoorWindowScheduleService (kept — used by ScheduleExportView CSV)
# ---------------------------------------------------------------------------


def _prop(props: dict, *keys: str) -> Any:
    """Return the first non-None value found among the given property keys."""
    for k in keys:
        v = props.get(k)
        if v is not None:
            return v
    return None


def _fmt_bool(value: Any) -> str | None:
    """Normalise a boolean-ish property value to True/False/None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().upper() in ("TRUE", "YES", "1")
    return bool(value)


class DoorWindowScheduleService:
    """
    Build a structured door/window schedule from IFCEntity records.

    All data comes from the database (IFCEntity.properties JSON).
    No IFC file I/O is performed at runtime.
    """

    def __init__(self, ifc_file: IFCFile) -> None:
        self.ifc_file = ifc_file

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_storeys_with_counts(self) -> list[dict]:
        """
        Return storey names with door and window counts, ordered by storey.

        Each entry: {"storey": str, "door_count": int, "window_count": int}
        """
        entities = IFCEntity.objects.filter(
            ifc_file=self.ifc_file,
            ifc_type__in=["IfcDoor", "IfcWindow"],
        ).select_related("spatial_container__entity", "spatial_container__parent__entity")

        storeys: dict[str, dict] = {}
        for entity in entities:
            s = _entity_storey_name(entity)
            if s not in storeys:
                storeys[s] = {"storey": s, "door_count": 0, "window_count": 0}
            if entity.ifc_type == "IfcDoor":
                storeys[s]["door_count"] += 1
            else:
                storeys[s]["window_count"] += 1

        return sorted(storeys.values(), key=lambda r: r["storey"])

    def get_doors(self, storey: str = "") -> list[dict]:
        """Return schedule rows for all IfcDoor entities, optionally filtered by storey."""
        qs = (
            IFCEntity.objects.filter(ifc_file=self.ifc_file, ifc_type="IfcDoor")
            .select_related("spatial_container__entity", "spatial_container__parent__entity")
            .order_by("name")
        )
        if storey:
            descendant_ids = _get_storey_descendant_ids(storey, self.ifc_file)
            if descendant_ids:
                qs = qs.filter(spatial_container_id__in=descendant_ids)
            else:
                qs = qs.none()
        return [self._door_row(e) for e in qs]

    def get_windows(self, storey: str = "") -> list[dict]:
        """Return schedule rows for all IfcWindow entities, optionally filtered by storey."""
        qs = (
            IFCEntity.objects.filter(ifc_file=self.ifc_file, ifc_type="IfcWindow")
            .select_related("spatial_container__entity", "spatial_container__parent__entity")
            .order_by("name")
        )
        if storey:
            descendant_ids = _get_storey_descendant_ids(storey, self.ifc_file)
            if descendant_ids:
                qs = qs.filter(spatial_container_id__in=descendant_ids)
            else:
                qs = qs.none()
        return [self._window_row(e) for e in qs]

    # ------------------------------------------------------------------
    # Row builders
    # ------------------------------------------------------------------

    def _door_row(self, entity: IFCEntity) -> dict:
        """Extract schedule columns for a single IfcDoor entity."""
        p = entity.properties or {}
        return {
            "global_id": entity.global_id,
            "mark": entity.name or "—",
            "level": _entity_storey_name(entity) or "—",
            "room": _entity_space_name(entity) or "—",
            "width": _prop(p, "OverallWidth"),
            "height": _prop(p, "OverallHeight"),
            "fire_rating": _prop(
                p,
                "Pset_DoorCommon.FireRating",
                "Type.Pset_DoorCommon.FireRating",
            ),
            "is_external": _fmt_bool(
                _prop(p, "Pset_DoorCommon.IsExternal", "Type.Pset_DoorCommon.IsExternal")
            ),
            "acoustic_rating": _prop(
                p,
                "Pset_DoorCommon.AcousticRating",
                "Type.Pset_DoorCommon.AcousticRating",
            ),
            "security_rating": _prop(
                p,
                "Pset_DoorCommon.SecurityRating",
                "Type.Pset_DoorCommon.SecurityRating",
            ),
            "handicap_accessible": _fmt_bool(
                _prop(
                    p,
                    "Pset_DoorCommon.HandicapAccessible",
                    "Type.Pset_DoorCommon.HandicapAccessible",
                )
            ),
            "reference": _prop(p, "Pset_DoorCommon.Reference", "Type.Pset_DoorCommon.Reference"),
        }

    def _window_row(self, entity: IFCEntity) -> dict:
        """Extract schedule columns for a single IfcWindow entity."""
        p = entity.properties or {}
        return {
            "global_id": entity.global_id,
            "mark": entity.name or "—",
            "level": _entity_storey_name(entity) or "—",
            "room": _entity_space_name(entity) or "—",
            "width": _prop(p, "OverallWidth"),
            "height": _prop(p, "OverallHeight"),
            "fire_rating": _prop(
                p,
                "Pset_WindowCommon.FireRating",
                "Type.Pset_WindowCommon.FireRating",
            ),
            "is_external": _fmt_bool(
                _prop(
                    p,
                    "Pset_WindowCommon.IsExternal",
                    "Type.Pset_WindowCommon.IsExternal",
                )
            ),
            "acoustic_rating": _prop(
                p,
                "Pset_WindowCommon.AcousticRating",
                "Type.Pset_WindowCommon.AcousticRating",
            ),
            "thermal_transmittance": _prop(
                p,
                "Pset_WindowCommon.ThermalTransmittance",
                "Type.Pset_WindowCommon.ThermalTransmittance",
            ),
            "reference": _prop(
                p, "Pset_WindowCommon.Reference", "Type.Pset_WindowCommon.Reference"
            ),
        }
