# takeoff/services/model_inventory.py
"""Model Inventory — summary-first IFC index + applied/confirmed link coverage.

Package B1: overview / by-class / link coverage (counts only).
Package B2: by-level, missing model data, lazy paginated IFC Elements list.
No GlobalId dumps, no property payloads, no BOQ/ERP claims.
"""

from __future__ import annotations

import logging
import math
from typing import Any
from uuid import UUID

from ifc_processor.models import IFCEntity, IFCFile, IFCSpatialElement
from scheduling.services.link_resolver import linked_entity_gids_for_project
from takeoff.services.quantities import entity_has_ifc_quantity

logger = logging.getLogger(__name__)

MAX_CLASS_ROWS = 200
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
UNASSIGNED_LEVEL_KEY = "__unassigned__"


def _level_label(spatial_type: str | None, entity_name: str | None) -> str:
    """Human label for a spatial container; Unassigned when missing."""
    if spatial_type is None:
        return "Unassigned"
    name = (entity_name or "").strip()
    return name or "(unnamed)"


def _level_key(spatial_container_id: UUID | None) -> str:
    if spatial_container_id is None:
        return UNASSIGNED_LEVEL_KEY
    return str(spatial_container_id)


def _normalize_page(page: int | str | None) -> int:
    try:
        n = int(page) if page is not None else 1
    except (TypeError, ValueError):
        return 1
    return max(1, n)


def _normalize_page_size(page_size: int | str | None) -> int:
    if page_size is None or page_size == "":
        return DEFAULT_PAGE_SIZE
    try:
        n = int(page_size)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    if n < 1:
        return DEFAULT_PAGE_SIZE
    return min(n, MAX_PAGE_SIZE)


def _normalize_linked_status(value: str | None) -> str:
    """Map query aliases to all | linked | unlinked."""
    raw = (value or "all").strip().lower().replace("-", "_")
    if raw in {"linked", "applied_confirmed", "applied", "confirmed"}:
        return "linked"
    if raw == "unlinked":
        return "unlinked"
    return "all"


def _normalize_has_qto(value: str | None) -> str:
    raw = (value or "all").strip().lower()
    if raw in {"yes", "true", "1"}:
        return "yes"
    if raw in {"no", "false", "0"}:
        return "no"
    return "all"


def _display_name(name: str | None, tag: str | None, ifc_type: str | None) -> str:
    for candidate in (name, tag, ifc_type):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return "(unnamed)"


class ModelInventoryService:
    """Build summary-first Model Inventory payload for one project."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def build(self) -> dict[str, Any]:
        """Return overview + by-class + by-level + missing data + link coverage."""
        ifc_file = self._completed_ifc_file()
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

        by_type: dict[str, dict[str, int]] = {}
        by_level: dict[str, dict[str, Any]] = {}
        with_qty = 0
        trusted_linked = 0
        missing_level = 0

        for (
            ifc_type,
            global_id,
            props,
            sc_id,
            sc_type,
            sc_name,
        ) in entities_qs.values_list(
            "ifc_type",
            "global_id",
            "properties",
            "spatial_container_id",
            "spatial_container__spatial_type",
            "spatial_container__entity__name",
        ).iterator(chunk_size=1000):
            key = ifc_type or "Unknown"
            bucket = by_type.setdefault(
                key, {"element_count": 0, "trusted_linked": 0, "quantity_available": 0}
            )
            bucket["element_count"] += 1

            is_linked = global_id in trusted_gids
            if is_linked:
                trusted_linked += 1
                bucket["trusted_linked"] += 1

            has_qto = entity_has_ifc_quantity(props if isinstance(props, dict) else None)
            if has_qto:
                bucket["quantity_available"] += 1
                with_qty += 1

            lvl_key = _level_key(sc_id)
            if lvl_key == UNASSIGNED_LEVEL_KEY:
                missing_level += 1
            level_bucket = by_level.setdefault(
                lvl_key,
                {
                    "level_key": lvl_key,
                    "level_label": _level_label(sc_type, sc_name),
                    "spatial_type": sc_type,
                    "entity_count": 0,
                    "linked_count": 0,
                    "unlinked_count": 0,
                    "has_ifc_qto_count": 0,
                    "ifc_class_count": 0,
                    "_classes": set(),
                },
            )
            level_bucket["entity_count"] += 1
            if is_linked:
                level_bucket["linked_count"] += 1
            else:
                level_bucket["unlinked_count"] += 1
            if has_qto:
                level_bucket["has_ifc_qto_count"] += 1
            level_bucket["_classes"].add(key)

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

        level_rows = []
        for b in by_level.values():
            classes = b.pop("_classes")
            b["ifc_class_count"] = len(classes)
            level_rows.append(b)
        # Storeys first by count, Unassigned last when present.
        level_rows.sort(
            key=lambda r: (
                1 if r["level_key"] == UNASSIGNED_LEVEL_KEY else 0,
                -r["entity_count"],
                r["level_label"],
            )
        )

        missing_qto = max(0, total_entities - with_qty)
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
            "by_level": level_rows,
            "missing_model_data": {
                "missing_level_count": missing_level,
                "missing_ifc_qto_count": missing_qto,
                "classification_coverage": "unavailable",
                "classification_message": "Classification Coverage: Unavailable",
            },
            "filter_options": {
                "ifc_classes": sorted({r["ifc_type"] for r in class_rows}),
                "levels": [{"key": r["level_key"], "label": r["level_label"]} for r in level_rows],
            },
            "link_coverage": {
                "total_entities": total_entities,
                "trusted_linked_entities": trusted_linked,
                "unlinked_entities": unlinked,
                "coverage_pct": coverage_pct,
                "trusted_only": True,
                "caveat": "Link coverage uses applied / confirmed schedule-model links only.",
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

    def list_entities(
        self,
        *,
        ifc_class: str | None = None,
        level: str | None = None,
        linked_status: str | None = "all",
        has_qto: str | None = "all",
        page: int | str | None = 1,
        page_size: int | str | None = None,
    ) -> dict[str, Any]:
        """Return one page of IFC Elements for the lazy list (no properties JSON)."""
        page_n = _normalize_page(page)
        size = _normalize_page_size(page_size)
        linked_f = _normalize_linked_status(linked_status)
        qto_f = _normalize_has_qto(has_qto)
        class_f = (ifc_class or "").strip() or None
        level_f = (level or "").strip() or None

        empty = {
            "has_ifc": False,
            "rows": [],
            "page": page_n,
            "page_size": size,
            "total_matched": 0,
            "total_pages": 0,
            "has_prev": False,
            "has_next": False,
            "prev_page": None,
            "next_page": None,
            "filters": {
                "ifc_class": class_f or "",
                "level": level_f or "",
                "linked_status": linked_f,
                "has_qto": qto_f,
            },
        }

        ifc_file = self._completed_ifc_file()
        if ifc_file is None:
            return empty

        empty["has_ifc"] = True
        qs = IFCEntity.objects.filter(ifc_file=ifc_file)
        if class_f:
            qs = qs.filter(ifc_type=class_f)
        if level_f == UNASSIGNED_LEVEL_KEY:
            qs = qs.filter(spatial_container__isnull=True)
        elif level_f:
            qs = qs.filter(spatial_container_id=level_f)

        trusted_gids = linked_entity_gids_for_project(self.project_id)
        if linked_f == "linked":
            if not trusted_gids:
                return {**empty, "has_ifc": True}
            qs = qs.filter(global_id__in=trusted_gids)
        elif linked_f == "unlinked":
            if trusted_gids:
                qs = qs.exclude(global_id__in=trusted_gids)

        qs = qs.order_by("ifc_type", "name", "pk")
        values = (
            "pk",
            "name",
            "tag",
            "ifc_type",
            "global_id",
            "properties",
            "spatial_container__spatial_type",
            "spatial_container__entity__name",
        )

        # When Has IFC Qto is unconstrained, paginate at the DB.
        if qto_f == "all":
            total = qs.count()
            if total == 0:
                return {**empty, "has_ifc": True}
            total_pages = max(1, math.ceil(total / size))
            if page_n > total_pages:
                page_n = total_pages
            offset = (page_n - 1) * size
            page_qs = qs.values_list(*values)[offset : offset + size]
            rows = [
                self._entity_row(
                    pk=pk,
                    name=name,
                    tag=tag,
                    ifc_type=ifc_type,
                    global_id=global_id,
                    props=props,
                    sc_type=sc_type,
                    sc_name=sc_name,
                    trusted_gids=trusted_gids,
                )
                for (
                    pk,
                    name,
                    tag,
                    ifc_type,
                    global_id,
                    props,
                    sc_type,
                    sc_name,
                ) in page_qs
            ]
            return self._page_payload(
                rows=rows,
                page=page_n,
                page_size=size,
                total_matched=total,
                filters={
                    "ifc_class": class_f or "",
                    "level": level_f or "",
                    "linked_status": linked_f,
                    "has_qto": qto_f,
                },
            )

        # has_qto yes/no requires evaluating properties — filter then page in memory.
        matched_rows: list[dict[str, Any]] = []
        for (
            pk,
            name,
            tag,
            ifc_type,
            global_id,
            props,
            sc_type,
            sc_name,
        ) in qs.values_list(*values).iterator(chunk_size=1000):
            has_qty = entity_has_ifc_quantity(props if isinstance(props, dict) else None)
            if qto_f == "yes" and not has_qty:
                continue
            if qto_f == "no" and has_qty:
                continue
            matched_rows.append(
                self._entity_row(
                    pk=pk,
                    name=name,
                    tag=tag,
                    ifc_type=ifc_type,
                    global_id=global_id,
                    props=props,
                    sc_type=sc_type,
                    sc_name=sc_name,
                    trusted_gids=trusted_gids,
                    has_qty=has_qty,
                )
            )

        total = len(matched_rows)
        if total == 0:
            return {**empty, "has_ifc": True}
        total_pages = max(1, math.ceil(total / size))
        if page_n > total_pages:
            page_n = total_pages
        offset = (page_n - 1) * size
        page_rows = matched_rows[offset : offset + size]
        return self._page_payload(
            rows=page_rows,
            page=page_n,
            page_size=size,
            total_matched=total,
            filters={
                "ifc_class": class_f or "",
                "level": level_f or "",
                "linked_status": linked_f,
                "has_qto": qto_f,
            },
        )

    def _completed_ifc_file(self) -> IFCFile | None:
        return (
            IFCFile.objects.filter(project=self.project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )

    @staticmethod
    def _entity_row(
        *,
        pk,
        name: str | None,
        tag: str | None,
        ifc_type: str | None,
        global_id: str,
        props: Any,
        sc_type: str | None,
        sc_name: str | None,
        trusted_gids: set[str],
        has_qty: bool | None = None,
    ) -> dict[str, Any]:
        if has_qty is None:
            has_qty = entity_has_ifc_quantity(props if isinstance(props, dict) else None)
        is_linked = global_id in trusted_gids
        return {
            "id": str(pk),
            "display_name": _display_name(name, tag, ifc_type),
            "ifc_class": ifc_type or "Unknown",
            "level_label": _level_label(sc_type, sc_name),
            "has_ifc_qto": has_qty,
            "has_ifc_qto_label": "Yes" if has_qty else "No",
            "link_status": "linked" if is_linked else "unlinked",
            "link_status_label": "Applied/Confirmed" if is_linked else "Unlinked",
        }

    @staticmethod
    def _page_payload(
        *,
        rows: list[dict[str, Any]],
        page: int,
        page_size: int,
        total_matched: int,
        filters: dict[str, str],
    ) -> dict[str, Any]:
        total_pages = max(1, math.ceil(total_matched / page_size)) if total_matched else 0
        return {
            "has_ifc": True,
            "rows": rows,
            "page": page,
            "page_size": page_size,
            "total_matched": total_matched,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": bool(total_pages and page < total_pages),
            "prev_page": page - 1 if page > 1 else None,
            "next_page": page + 1 if total_pages and page < total_pages else None,
            "filters": filters,
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
            "by_level": [],
            "missing_model_data": {
                "missing_level_count": 0,
                "missing_ifc_qto_count": 0,
                "classification_coverage": "unavailable",
                "classification_message": "Classification Coverage: Unavailable",
            },
            "filter_options": {"ifc_classes": [], "levels": []},
            "link_coverage": {
                "total_entities": 0,
                "trusted_linked_entities": 0,
                "unlinked_entities": 0,
                "coverage_pct": None,
                "trusted_only": True,
                "caveat": "Link coverage uses applied / confirmed schedule-model links only.",
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
