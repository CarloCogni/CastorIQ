# takeoff/services/model_inventory.py
"""Model Inventory spine — summary-first IFC index + trusted link coverage.

Package B1: counts only. No GlobalId dumps, no property payloads, no BOQ/ERP claims.
"""

from __future__ import annotations

import logging
from typing import Any

from ifc_processor.models import IFCEntity, IFCFile, IFCSpatialElement
from scheduling.services.link_resolver import linked_entity_gids_for_project
from takeoff.services.quantities import entity_has_ifc_quantity

logger = logging.getLogger(__name__)

MAX_CLASS_ROWS = 200


class ModelInventoryService:
    """Build summary-first Model Inventory payload for one project."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def build(self) -> dict[str, Any]:
        """Return overview + by-class rows + link coverage (counts only)."""
        ifc_file = (
            IFCFile.objects.filter(project=self.project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if ifc_file is None:
            return self._empty(has_ifc=False)

        entities_qs = IFCEntity.objects.filter(ifc_file=ifc_file)
        total_entities = entities_qs.count()
        if total_entities == 0:
            return self._empty(has_ifc=True, ifc_file_name=ifc_file.name)

        class_count = entities_qs.values("ifc_type").distinct().count()
        storey_count = IFCSpatialElement.objects.filter(
            ifc_file=ifc_file, spatial_type="building_storey"
        ).count()

        trusted_gids = linked_entity_gids_for_project(self.project_id)

        # One pass for class buckets, trusted link hits, and quantity availability.
        # Counts only — never return GlobalIds or properties in the payload.
        by_type: dict[str, dict[str, int]] = {}
        with_qty = 0
        trusted_linked = 0
        for ifc_type, global_id, props in entities_qs.values_list(
            "ifc_type", "global_id", "properties"
        ).iterator(chunk_size=1000):
            key = ifc_type or "Unknown"
            bucket = by_type.setdefault(
                key, {"element_count": 0, "trusted_linked": 0, "quantity_available": 0}
            )
            bucket["element_count"] += 1
            if global_id in trusted_gids:
                trusted_linked += 1
                bucket["trusted_linked"] += 1
            if entity_has_ifc_quantity(props if isinstance(props, dict) else None):
                bucket["quantity_available"] += 1
                with_qty += 1

        unlinked = max(0, total_entities - trusted_linked)
        coverage_pct = round(trusted_linked / total_entities * 100, 1) if total_entities else None

        class_rows = []
        for ifc_type, b in by_type.items():
            linked = b["trusted_linked"]
            count = b["element_count"]
            class_rows.append(
                {
                    "ifc_type": ifc_type,
                    "element_count": count,
                    "trusted_linked": linked,
                    "unlinked": max(0, count - linked),
                    "quantity_available": b["quantity_available"],
                }
            )
        class_rows.sort(key=lambda r: (-r["element_count"], r["ifc_type"]))
        truncated = len(class_rows) > MAX_CLASS_ROWS
        class_rows = class_rows[:MAX_CLASS_ROWS]

        qty_pct = round(with_qty / total_entities * 100, 1) if total_entities else None

        return {
            "has_ifc": True,
            "ifc_file_name": ifc_file.name,
            "overview": {
                "total_entities": total_entities,
                "ifc_class_count": class_count,
                "storey_count": storey_count,
                "trusted_linked_entities": trusted_linked,
                "unlinked_entities": unlinked,
                "link_coverage_pct": coverage_pct,
                "entities_with_quantity": with_qty,
                "quantity_availability_pct": qty_pct,
                "has_quantities": with_qty > 0,
                "has_trusted_links": trusted_linked > 0,
                "has_storeys": storey_count > 0,
            },
            "by_class": class_rows,
            "by_class_truncated": truncated,
            "by_class_total_types": class_count,
            "link_coverage": {
                "total_entities": total_entities,
                "trusted_linked_entities": trusted_linked,
                "unlinked_entities": unlinked,
                "coverage_pct": coverage_pct,
                "trusted_only": True,
                "caveat": "Link coverage uses trusted schedule-model links only.",
            },
            "honesty": {
                "not_boq": True,
                "not_qs_valuation": True,
                "not_erp": True,
                "not_company_actual_cost": True,
                "not_commercial_5d": True,
                "quantities_label": "IFC model quantities",
            },
        }

    def _empty(self, *, has_ifc: bool, ifc_file_name: str | None = None) -> dict[str, Any]:
        return {
            "has_ifc": has_ifc,
            "ifc_file_name": ifc_file_name,
            "overview": {
                "total_entities": 0,
                "ifc_class_count": 0,
                "storey_count": 0,
                "trusted_linked_entities": 0,
                "unlinked_entities": 0,
                "link_coverage_pct": None,
                "entities_with_quantity": 0,
                "quantity_availability_pct": None,
                "has_quantities": False,
                "has_trusted_links": False,
                "has_storeys": False,
            },
            "by_class": [],
            "by_class_truncated": False,
            "by_class_total_types": 0,
            "link_coverage": {
                "total_entities": 0,
                "trusted_linked_entities": 0,
                "unlinked_entities": 0,
                "coverage_pct": None,
                "trusted_only": True,
                "caveat": "Link coverage uses trusted schedule-model links only.",
            },
            "honesty": {
                "not_boq": True,
                "not_qs_valuation": True,
                "not_erp": True,
                "not_company_actual_cost": True,
                "not_commercial_5d": True,
                "quantities_label": "IFC model quantities",
            },
        }
