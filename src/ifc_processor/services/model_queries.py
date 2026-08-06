# ifc_processor/services/model_queries.py
"""Deterministic model queries — code computes, the LLM only narrates.

Executor library behind the Ask deterministic answer layer. Each method
answers one quantitative question class directly from the DB index and
returns a uniform dict::

    {"value": ..., "unit": "m²", "method": "...", "provenance": "...", "rows": [...]}

``value=None`` means the model genuinely lacks the data — callers fall back
to RAG. Recipe pattern and quantity fallback chains ported from ifc-lite
(ifc-lite/packages/cli/src/commands/ask.ts).
"""

from __future__ import annotations

import logging
from typing import Any

from django.db.models import Count

from ifc_processor.models import IFCDataIssue, IFCEntity, IFCFile, IFCSpatialElement
from ifc_processor.schema_data.lookup import expand_type
from ifc_processor.services.property_access import get_prop

logger = logging.getLogger(__name__)

# Quantity fallback chains (pset, [property, ...]) — first numeric hit wins.
_WALL_AREA_CHAIN = ("Qto_WallBaseQuantities", ("NetSideArea", "GrossSideArea"))
_SLAB_AREA_CHAIN = ("Qto_SlabBaseQuantities", ("NetArea", "GrossArea"))
_SPACE_AREA_CHAIN = ("Qto_SpaceBaseQuantities", ("NetFloorArea", "GrossFloorArea"))
_VOLUME_CHAINS = (
    ("Qto_WallBaseQuantities", ("NetVolume", "GrossVolume")),
    ("Qto_SlabBaseQuantities", ("NetVolume", "GrossVolume")),
    ("Qto_ColumnBaseQuantities", ("NetVolume", "GrossVolume")),
    ("Qto_BeamBaseQuantities", ("NetVolume", "GrossVolume")),
    ("Qto_FootingBaseQuantities", ("NetVolume", "GrossVolume")),
)
_WINDOW_AREA_CHAIN = ("Qto_WindowBaseQuantities", ("Area",))


def _first_numeric(props: dict, pset: str, names: tuple[str, ...]) -> float | None:
    """Walk a fallback chain over the flat properties dict."""
    for name in names:
        value = get_prop(props, pset, name)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


class ModelQueryService:
    """Deterministic queries over one project's parsed IFC index."""

    def __init__(self, project):
        self.project = project

    # ── Base querysets / metadata ──────────────────────────────────────

    def _entities(self):
        return IFCEntity.objects.filter(
            ifc_file__project=self.project,
            ifc_file__status=IFCFile.Status.COMPLETED,
        )

    def _latest_file(self) -> IFCFile | None:
        return (
            IFCFile.objects.filter(project=self.project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )

    def _schema(self) -> str:
        latest = self._latest_file()
        return latest.schema_version if latest else "IFC4"

    def _unit(self, unit_key: str, default: str) -> str:
        latest = self._latest_file()
        units = (latest.project_units or {}) if latest else {}
        return units.get(unit_key, default)

    def has_model(self) -> bool:
        """Guard: deterministic answers need at least one completed file."""
        return self._latest_file() is not None

    # ── Executors ──────────────────────────────────────────────────────

    def count_entities(self, ifc_type: str) -> dict[str, Any]:
        """Count entities of a type, subtypes included via schema metadata."""
        expanded = expand_type(ifc_type, self._schema())
        count = self._entities().filter(ifc_type__in=expanded).count()
        subtype_note = (
            f" (includes subtypes: {', '.join(sorted(expanded - {ifc_type}))})"
            if len(expanded) > 1
            else ""
        )
        return {
            "value": count,
            "unit": "",
            "method": f"COUNT over parsed entities of type {ifc_type}{subtype_note}",
            "provenance": f"database index of {self._schema()} model",
            "rows": [],
        }

    def type_breakdown(self) -> dict[str, Any]:
        """Entity counts grouped by IFC type, descending."""
        rows = list(
            self._entities().values("ifc_type").annotate(count=Count("id")).order_by("-count")
        )
        return {
            "value": sum(r["count"] for r in rows),
            "unit": "",
            "method": "GROUP BY ifc_type over parsed entities",
            "provenance": f"{len(rows)} distinct types",
            "rows": rows,
        }

    def total_wall_area(self) -> dict[str, Any]:
        """Sum wall side areas via the Net→Gross fallback chain."""
        return self._sum_quantity(
            types=expand_type("IfcWall", self._schema()),
            chains=(_WALL_AREA_CHAIN,),
            unit=self._unit("AREAUNIT", "m²"),
            label="wall side area",
        )

    def total_floor_area(self) -> dict[str, Any]:
        """Sum slab areas (Net→Gross), the usual floor-area proxy."""
        return self._sum_quantity(
            types=expand_type("IfcSlab", self._schema()),
            chains=(_SLAB_AREA_CHAIN,),
            unit=self._unit("AREAUNIT", "m²"),
            label="slab area",
        )

    def total_space_area(self) -> dict[str, Any]:
        """Sum space floor areas (NetFloorArea→GrossFloorArea)."""
        return self._sum_quantity(
            types=frozenset({"IfcSpace"}),
            chains=(_SPACE_AREA_CHAIN,),
            unit=self._unit("AREAUNIT", "m²"),
            label="space floor area",
        )

    def total_volume(self) -> dict[str, Any]:
        """Sum structural element volumes across the standard Qto chains."""
        types: set[str] = set()
        for base in ("IfcWall", "IfcSlab", "IfcColumn", "IfcBeam", "IfcFooting"):
            types |= expand_type(base, self._schema())
        return self._sum_quantity(
            types=frozenset(types),
            chains=_VOLUME_CHAINS,
            unit=self._unit("VOLUMEUNIT", "m³"),
            label="structural volume",
        )

    def window_wall_ratio(self) -> dict[str, Any]:
        """Window-to-wall ratio per the ISO 13790 convention (ifc-lite recipe).

        Wall area prefers external walls (Pset_WallCommon.IsExternal); when no
        wall carries the flag, all walls are used and the provenance says so.
        """
        schema = self._schema()

        # Stream both scans (see _sum_quantity) — one pass over the walls
        # accumulates the external and the all-walls totals side by side.
        wall_count = 0
        external_count = 0
        wall_area_all = 0.0
        wall_area_external = 0.0
        pset, chain = _WALL_AREA_CHAIN
        wall_qs = self._entities().filter(ifc_type__in=expand_type("IfcWall", schema))
        for wall in wall_qs.only("properties").iterator(chunk_size=2000):
            wall_count += 1
            area = _first_numeric(wall.properties, pset, chain)
            if area:
                wall_area_all += area
            if bool(get_prop(wall.properties, "Pset_WallCommon", "IsExternal")):
                external_count += 1
                if area:
                    wall_area_external += area

        window_count = 0
        window_area = 0.0
        window_qs = self._entities().filter(ifc_type__in=expand_type("IfcWindow", schema))
        for window in window_qs.only("properties").iterator(chunk_size=2000):
            window_count += 1
            pset, chain = _WINDOW_AREA_CHAIN
            area = _first_numeric(window.properties, pset, chain)
            if area is None:
                width = (window.properties or {}).get("OverallWidth")
                height = (window.properties or {}).get("OverallHeight")
                if width and height:
                    # Overall dims are usually mm — plausible-scale guard.
                    area = float(width) * float(height)
                    if area > 100:  # almost certainly mm² → m²
                        area /= 1_000_000
            window_area += area or 0.0

        if not wall_count or not window_count:
            return self._empty("model has no walls or no windows")

        wall_area = wall_area_external if external_count else wall_area_all
        wall_source = (
            f"{external_count} external walls (Pset_WallCommon.IsExternal)"
            if external_count
            else f"all {wall_count} walls (no IsExternal flags in model)"
        )

        if not wall_area or not window_area:
            return self._empty("no usable area quantities on walls or windows")

        ratio = window_area / (window_area + wall_area)
        return {
            "value": round(ratio, 3),
            "unit": "",
            "method": "WWR = window area / (window area + wall area), ISO 13790 convention",
            "provenance": (
                f"window area {window_area:.1f} from {window_count} windows; "
                f"wall area {wall_area:.1f} from {wall_source}"
            ),
            "rows": [],
        }

    def list_storeys(self) -> dict[str, Any]:
        """Storeys with elevations, bottom-up."""
        storeys = list(
            IFCSpatialElement.objects.filter(
                ifc_file__project=self.project,
                ifc_file__status=IFCFile.Status.COMPLETED,
                spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
            )
            .select_related("entity")
            .order_by("elevation")
        )
        if not storeys:
            return self._empty("model defines no building storeys")
        rows = [
            {
                "name": (s.entity.name if s.entity else "") or "Unnamed",
                "elevation": float(s.elevation) if s.elevation is not None else None,
            }
            for s in storeys
        ]
        return {
            "value": len(rows),
            "unit": "storeys",
            "method": "spatial tree walk (IfcBuildingStorey nodes)",
            "provenance": f"elevations in {self._unit('LENGTHUNIT', 'm')}",
            "rows": rows,
        }

    def duplicate_global_ids(self) -> dict[str, Any]:
        """Duplicate-GUID data issues recorded at parse time."""
        rows = list(
            IFCDataIssue.objects.filter(
                ifc_file__project=self.project,
                issue_type=IFCDataIssue.IssueType.DUPLICATE_GUID,
            ).values("global_id", "ifc_type", "description")
        )
        return {
            "value": len(rows),
            "unit": "duplicates",
            "method": "parse-time GUID collision detection",
            "provenance": "IFCDataIssue records (Model Quality tab)",
            "rows": rows[:20],
        }

    def schema_version(self) -> dict[str, Any]:
        """The declared IFC schema of the latest completed file."""
        latest = self._latest_file()
        if latest is None or not latest.schema_version:
            return self._empty("no completed IFC file")
        return {
            "value": latest.schema_version,
            "unit": "",
            "method": "IFC file header",
            "provenance": latest.name,
            "rows": [],
        }

    # ── Internals ──────────────────────────────────────────────────────

    def _sum_quantity(
        self,
        *,
        types: frozenset[str],
        chains: tuple[tuple[str, tuple[str, ...]], ...],
        unit: str,
        label: str,
    ) -> dict[str, Any]:
        total = 0.0
        hits = 0
        candidates = 0
        # Stream — a federated model holds hundreds of thousands of rows, each
        # carrying a properties JSON blob; materializing them all at once can
        # OOM the worker before the first progress event fires.
        entity_qs = self._entities().filter(ifc_type__in=types).only("properties")
        for entity in entity_qs.iterator(chunk_size=2000):
            candidates += 1
            for pset, names in chains:
                value = _first_numeric(entity.properties, pset, names)
                if value is not None:
                    total += value
                    hits += 1
                    break

        if candidates == 0:
            return self._empty(f"model has no {label} candidates")
        if hits == 0:
            return self._empty(f"no Qto quantities present for {label}")

        return {
            "value": round(total, 2),
            "unit": unit,
            "method": f"sum of {label} over Qto fallback chains (Net preferred, Gross fallback)",
            "provenance": f"{hits}/{candidates} elements carried a usable quantity",
            "rows": [],
        }

    @staticmethod
    def _empty(reason: str) -> dict[str, Any]:
        return {"value": None, "unit": "", "method": "", "provenance": reason, "rows": []}
