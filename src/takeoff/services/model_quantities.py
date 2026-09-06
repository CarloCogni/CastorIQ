# takeoff/services/model_quantities.py
"""Package B3 — read-only IFC model quantity aggregates for the Quantities page.

Summary-first. No QTOCache writes, no costs, no GlobalId/property dumps.
"""

from __future__ import annotations

import logging
from typing import Any

from ifc_processor.models import IFCEntity, IFCFile
from takeoff.services.ifc_qto_flags import (
    entity_has_ifc_quantity,
    iter_ifc_quantity_measures,
)

logger = logging.getLogger(__name__)

MAX_CLASS_ROWS = 100
MAX_TYPE_ROWS = 50
MAX_MISSING_CLASS_ROWS = 50

# Named measures surfaced as column totals (explicit sums, not mixed primary qty).
SUM_NET_VOLUME = "NetVolume"
SUM_GROSS_VOLUME = "GrossVolume"
SUM_NET_AREA = "NetArea"
SUM_NET_SIDE_AREA = "NetSideArea"
SUM_LENGTH = "Length"


def _empty_measure_totals() -> dict[str, float]:
    return {
        SUM_NET_VOLUME: 0.0,
        SUM_GROSS_VOLUME: 0.0,
        SUM_NET_AREA: 0.0,
        SUM_NET_SIDE_AREA: 0.0,
        SUM_LENGTH: 0.0,
    }


def _level_label(spatial_type: str | None, entity_name: str | None) -> str:
    if spatial_type is None:
        return "Unassigned"
    name = (entity_name or "").strip()
    return name or "(unnamed)"


def _round_totals(totals: dict[str, float]) -> dict[str, float | None]:
    """Round measure totals; use None when zero so templates can hide empty cols."""
    out: dict[str, float | None] = {}
    for key, val in totals.items():
        out[key] = round(val, 2) if val else None
    return out


class ModelQuantitiesService:
    """Build summary-first Quantities payload for one project (read-only)."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def build(self) -> dict[str, Any]:
        """Return readiness + by-class + by-level + missing + optional by-type."""
        ifc_file = (
            IFCFile.objects.filter(project=self.project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if ifc_file is None:
            return self._empty(has_ifc=False)

        entities_qs = IFCEntity.objects.filter(ifc_file=ifc_file)
        total = entities_qs.count()
        if total == 0:
            return self._empty(has_ifc=True, ifc_file_name=ifc_file.name)

        by_class: dict[str, dict[str, Any]] = {}
        by_level: dict[str, dict[str, Any]] = {}
        by_type: dict[str, dict[str, Any]] = {}
        with_qto = 0
        type_populated = 0

        for (
            ifc_type,
            props,
            sc_type,
            sc_name,
            et_name,
            et_id,
        ) in entities_qs.values_list(
            "ifc_type",
            "properties",
            "spatial_container__spatial_type",
            "spatial_container__entity__name",
            "element_type__name",
            "element_type_id",
        ).iterator(chunk_size=1000):
            props = props if isinstance(props, dict) else {}
            cls = ifc_type or "Unknown"
            level = _level_label(sc_type, sc_name)
            has = entity_has_ifc_quantity(props)
            if has:
                with_qto += 1

            class_b = by_class.setdefault(
                cls,
                {
                    "ifc_class": cls,
                    "element_count": 0,
                    "has_ifc_qto": 0,
                    "missing_qto": 0,
                    "totals": _empty_measure_totals(),
                },
            )
            class_b["element_count"] += 1
            class_b["has_ifc_qto" if has else "missing_qto"] += 1

            level_b = by_level.setdefault(
                level,
                {
                    "level_label": level,
                    "element_count": 0,
                    "has_ifc_qto": 0,
                    "missing_qto": 0,
                    "totals": _empty_measure_totals(),
                },
            )
            level_b["element_count"] += 1
            level_b["has_ifc_qto" if has else "missing_qto"] += 1

            if et_id is not None:
                type_populated += 1
                tkey = (et_name or "").strip() or "(unnamed type)"
                type_b = by_type.setdefault(
                    tkey,
                    {
                        "type_name": tkey,
                        "ifc_class": cls,
                        "element_count": 0,
                        "has_ifc_qto": 0,
                        "missing_qto": 0,
                        "totals": _empty_measure_totals(),
                    },
                )
                type_b["element_count"] += 1
                type_b["has_ifc_qto" if has else "missing_qto"] += 1
            else:
                type_b = None

            for _pset, pname, num in iter_ifc_quantity_measures(props):
                if pname not in class_b["totals"]:
                    continue
                class_b["totals"][pname] += num
                level_b["totals"][pname] += num
                if type_b is not None:
                    type_b["totals"][pname] += num

        missing = max(0, total - with_qto)
        coverage = round(with_qto / total * 100, 1) if total else None

        class_rows = self._finalize_rows(
            by_class.values(),
            sort_key=lambda r: (-r["element_count"], r["ifc_class"]),
            limit=MAX_CLASS_ROWS,
        )
        level_rows = self._finalize_rows(
            by_level.values(),
            sort_key=lambda r: (
                1 if r["level_label"] == "Unassigned" else 0,
                -r["element_count"],
                r["level_label"],
            ),
            limit=None,
        )

        show_by_type = type_populated >= max(1, int(total * 0.5))
        type_rows: list[dict[str, Any]] = []
        if show_by_type and by_type:
            type_rows = self._finalize_rows(
                by_type.values(),
                sort_key=lambda r: (-r["element_count"], r["type_name"]),
                limit=MAX_TYPE_ROWS,
            )

        missing_by_class = [
            {
                "ifc_class": b["ifc_class"],
                "missing_qto": b["missing_qto"],
                "element_count": b["element_count"],
            }
            for b in sorted(
                (x for x in by_class.values() if x["missing_qto"] > 0),
                key=lambda r: (-r["missing_qto"], r["ifc_class"]),
            )[:MAX_MISSING_CLASS_ROWS]
        ]
        missing_by_level = [
            {
                "level_label": b["level_label"],
                "missing_qto": b["missing_qto"],
                "element_count": b["element_count"],
            }
            for b in sorted(
                (x for x in by_level.values() if x["missing_qto"] > 0),
                key=lambda r: (-r["missing_qto"], r["level_label"]),
            )
        ]

        return {
            "has_ifc": True,
            "ifc_file_name": ifc_file.name,
            "readiness": {
                "total_entities": total,
                "entities_with_quantity": with_qto,
                "missing_quantity_count": missing,
                "quantity_coverage_pct": coverage,
                "classification_coverage": "unavailable",
                "classification_message": "Classification Coverage: Unavailable",
                "source_caveat": (
                    "Read-only IFC quantity availability and model quantity "
                    "breakdowns from indexed IFC properties. This is not BOQ, "
                    "cost, or verified QS measurement."
                ),
                "linear_unit_caveat": (
                    "Volume, area, and length totals use IFC-reported model "
                    "units as stored in the index — they are not normalized "
                    "to SI units."
                ),
                "volume_unit": "model volume units",
                "area_unit": "model area units",
                "linear_unit": "model length units",
            },
            "by_ifc_class": class_rows,
            "by_ifc_class_truncated": len(by_class) > MAX_CLASS_ROWS,
            "by_ifc_class_total": len(by_class),
            "by_level": level_rows,
            "by_type": type_rows,
            "by_type_shown": bool(type_rows),
            "by_type_capped": len(by_type) > MAX_TYPE_ROWS,
            "by_type_total": len(by_type),
            "missing_quantities": {
                "by_ifc_class": missing_by_class,
                "by_level": missing_by_level,
                "total_missing": missing,
            },
            "measure_labels": {
                "NetVolume": "NetVolume (model volume units)",
                "GrossVolume": "GrossVolume (model volume units)",
                "NetArea": "NetArea (model area units)",
                "NetSideArea": "NetSideArea (model area units)",
                "Length": "Length (model length units)",
            },
        }

    @staticmethod
    def _finalize_rows(
        rows,
        *,
        sort_key,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        finalized: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            totals = row.pop("totals")
            rounded = _round_totals(totals)
            row["net_volume"] = rounded[SUM_NET_VOLUME]
            row["gross_volume"] = rounded[SUM_GROSS_VOLUME]
            row["net_area"] = rounded[SUM_NET_AREA]
            row["net_side_area"] = rounded[SUM_NET_SIDE_AREA]
            row["length"] = rounded[SUM_LENGTH]
            row["has_linear_totals"] = rounded[SUM_LENGTH] is not None
            finalized.append(row)
        finalized.sort(key=sort_key)
        if limit is not None:
            return finalized[:limit]
        return finalized

    def _empty(self, *, has_ifc: bool, ifc_file_name: str | None = None) -> dict[str, Any]:
        return {
            "has_ifc": has_ifc,
            "ifc_file_name": ifc_file_name,
            "readiness": {
                "total_entities": 0,
                "entities_with_quantity": 0,
                "missing_quantity_count": 0,
                "quantity_coverage_pct": None,
                "classification_coverage": "unavailable",
                "classification_message": "Classification Coverage: Unavailable",
                "source_caveat": (
                    "Read-only IFC quantity availability and model quantity "
                    "breakdowns from indexed IFC properties. This is not BOQ, "
                    "cost, or verified QS measurement."
                ),
                "linear_unit_caveat": (
                    "Volume, area, and length totals use IFC-reported model "
                    "units as stored in the index — they are not normalized "
                    "to SI units."
                ),
                "volume_unit": "model volume units",
                "area_unit": "model area units",
                "linear_unit": "model length units",
            },
            "by_ifc_class": [],
            "by_ifc_class_truncated": False,
            "by_ifc_class_total": 0,
            "by_level": [],
            "by_type": [],
            "by_type_shown": False,
            "by_type_capped": False,
            "by_type_total": 0,
            "missing_quantities": {
                "by_ifc_class": [],
                "by_level": [],
                "total_missing": 0,
            },
            "measure_labels": {
                "NetVolume": "NetVolume (model volume units)",
                "GrossVolume": "GrossVolume (model volume units)",
                "NetArea": "NetArea (model area units)",
                "NetSideArea": "NetSideArea (model area units)",
                "Length": "Length (model length units)",
            },
        }
