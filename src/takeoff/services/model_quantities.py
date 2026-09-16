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
# SCALE-1A: raise type aggregates so pilot grains (~309) fit; UI paginates display.
MAX_TYPE_ROWS = 500
MAX_MISSING_CLASS_ROWS = 50

# Named measures surfaced as column totals (explicit sums, not mixed primary qty).
SUM_NET_VOLUME = "NetVolume"
SUM_GROSS_VOLUME = "GrossVolume"
SUM_NET_AREA = "NetArea"
SUM_GROSS_AREA = "GrossArea"
SUM_NET_SIDE_AREA = "NetSideArea"
SUM_LENGTH = "Length"

# Inventory keys for measurement resolver (excludes SideArea — not Area-compatible).
RESOLVER_INVENTORY_KEYS: tuple[str, ...] = (
    SUM_NET_VOLUME,
    SUM_GROSS_VOLUME,
    SUM_NET_AREA,
    SUM_GROSS_AREA,
    SUM_LENGTH,
)


def _empty_measure_totals() -> dict[str, float]:
    return {
        SUM_NET_VOLUME: 0.0,
        SUM_GROSS_VOLUME: 0.0,
        SUM_NET_AREA: 0.0,
        SUM_GROSS_AREA: 0.0,
        SUM_NET_SIDE_AREA: 0.0,
        SUM_LENGTH: 0.0,
    }


def _empty_measure_seen() -> dict[str, bool]:
    return {key: False for key in _empty_measure_totals()}


def _inventory_from_totals(
    totals: dict[str, float],
    seen: dict[str, bool],
) -> dict[str, float | None]:
    """Build resolver inventory: missing → None, numeric 0 preserved."""
    out: dict[str, float | None] = {}
    for key in RESOLVER_INVENTORY_KEYS:
        if not seen.get(key):
            out[key] = None
        else:
            out[key] = round(float(totals.get(key, 0.0)), 2)
    return out


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

    def build(
        self,
        *,
        ifc_file: IFCFile | None = None,
        entity_predicate=None,
    ) -> dict[str, Any]:
        """Return readiness + by-class + by-level + missing + optional by-type.

        When ``ifc_file`` is provided, aggregates that pinned source only.
        Otherwise uses the latest completed IFC for the project.

        ``entity_predicate`` (DYNAMIC-07): optional
        ``(props, ifc_class, type_name, storey_name, container_name) -> bool``.
        When set, only matching entities contribute to counts and measure totals.
        """
        if ifc_file is None:
            ifc_file = (
                IFCFile.objects.filter(project=self.project, status=IFCFile.Status.COMPLETED)
                .order_by("-created_at")
                .first()
            )
        elif getattr(ifc_file, "project_id", None) != getattr(self.project, "pk", None):
            logger.warning(
                "refusing ifc_file from another project project=%s ifc=%s",
                self.project_id,
                getattr(ifc_file, "pk", None),
            )
            return self._empty(has_ifc=False)

        if ifc_file is None:
            return self._empty(has_ifc=False)

        entities_qs = IFCEntity.objects.filter(ifc_file=ifc_file)
        total_indexed = entities_qs.count()
        if total_indexed == 0:
            return self._empty(
                has_ifc=True,
                ifc_file_name=ifc_file.name,
                ifc_file_id=str(ifc_file.pk),
                ifc_file_hash=str(ifc_file.file_hash or ""),
            )

        by_class: dict[str, dict[str, Any]] = {}
        by_level: dict[str, dict[str, Any]] = {}
        by_type: dict[str, dict[str, Any]] = {}
        with_qto = 0
        type_populated = 0
        matched_total = 0

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
            type_name = (et_name or "").strip() if et_id is not None else ""
            storey_name = level if sc_type else ""
            container_name = level if sc_type else ""
            if entity_predicate is not None:
                try:
                    if not entity_predicate(props, cls, type_name, storey_name, container_name):
                        continue
                except Exception:
                    logger.debug("entity_predicate failed; skipping entity", exc_info=True)
                    continue
            matched_total += 1
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
                    "totals_seen": _empty_measure_seen(),
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
                    "totals_seen": _empty_measure_seen(),
                },
            )
            level_b["element_count"] += 1
            level_b["has_ifc_qto" if has else "missing_qto"] += 1

            if et_id is not None:
                type_populated += 1
                tname = (et_name or "").strip() or "(unnamed type)"
                # Include IFC class so identical type names across classes stay distinct.
                tkey = (cls, tname)
                type_b = by_type.setdefault(
                    tkey,
                    {
                        "type_name": tname,
                        "ifc_class": cls,
                        "element_count": 0,
                        "has_ifc_qto": 0,
                        "missing_qto": 0,
                        "totals": _empty_measure_totals(),
                        "totals_seen": _empty_measure_seen(),
                        "element_type_ids": set(),
                    },
                )
                type_b["element_count"] += 1
                type_b["has_ifc_qto" if has else "missing_qto"] += 1
                type_b["element_type_ids"].add(str(et_id))
            else:
                type_b = None

            for _pset, pname, num in iter_ifc_quantity_measures(props):
                if pname not in class_b["totals"]:
                    continue
                class_b["totals"][pname] += num
                class_b["totals_seen"][pname] = True
                level_b["totals"][pname] += num
                level_b["totals_seen"][pname] = True
                if type_b is not None:
                    type_b["totals"][pname] += num
                    type_b["totals_seen"][pname] = True

        missing = max(0, matched_total - with_qto)
        coverage = round(with_qto / matched_total * 100, 1) if matched_total else None

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

        show_by_type = (
            type_populated >= max(1, int(matched_total * 0.5)) if matched_total else False
        )
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
            "ifc_file_id": str(ifc_file.pk),
            "ifc_file_hash": str(ifc_file.file_hash or ""),
            "readiness": {
                "total_entities": matched_total,
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
                "indexed_entity_total": total_indexed,
                "entity_filter_active": entity_predicate is not None,
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
                "GrossArea": "GrossArea (model area units)",
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
            seen = row.pop("totals_seen", _empty_measure_seen())
            type_ids = row.pop("element_type_ids", None)
            # Legacy display fields keep prior zero→None collapse for templates.
            rounded = _round_totals(totals)
            row["net_volume"] = rounded[SUM_NET_VOLUME]
            row["gross_volume"] = rounded[SUM_GROSS_VOLUME]
            row["net_area"] = rounded[SUM_NET_AREA]
            row["gross_area"] = rounded.get(SUM_GROSS_AREA)
            row["net_side_area"] = rounded[SUM_NET_SIDE_AREA]
            row["length"] = rounded[SUM_LENGTH]
            row["has_linear_totals"] = rounded[SUM_LENGTH] is not None
            # New engine path: 0 preserved, missing is None (does not affect freeze).
            row["measure_inventory"] = _inventory_from_totals(totals, seen)
            if isinstance(type_ids, set):
                if len(type_ids) == 1:
                    row["element_type_id"] = next(iter(type_ids))
                else:
                    # Mixed type PKs under same class+name — name fallback only.
                    row["element_type_id"] = None
                    row["element_type_id_mixed"] = True
            else:
                row.setdefault("element_type_id", None)
            finalized.append(row)
        finalized.sort(key=sort_key)
        if limit is not None:
            return finalized[:limit]
        return finalized

    def _empty(
        self,
        *,
        has_ifc: bool,
        ifc_file_name: str | None = None,
        ifc_file_id: str | None = None,
        ifc_file_hash: str | None = None,
    ) -> dict[str, Any]:
        return {
            "has_ifc": has_ifc,
            "ifc_file_name": ifc_file_name,
            "ifc_file_id": ifc_file_id,
            "ifc_file_hash": ifc_file_hash or "",
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
                "GrossArea": "GrossArea (model area units)",
                "NetSideArea": "NetSideArea (model area units)",
                "Length": "Length (model length units)",
            },
        }
