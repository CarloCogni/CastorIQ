# takeoff/services/model_inventory.py
"""Model Readiness — semantic 4D/5D cards over the IFC inventory index.

Package B1/B2 inventory grids remain as drill-down evidence.
Phase 1 adds linkability, granularity, spatial, playback, QTO, and
classification cards. Playback uses cheap linked-task date aggregates —
never TimelinePayloadService.build_summary() on this path.
No GlobalId dumps, no property payloads, no BOQ/ERP claims.
"""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any
from uuid import UUID

from ifc_processor.models import IFCEntity, IFCFile, IFCSpatialElement
from takeoff.services.ifc_qto_flags import entity_has_ifc_quantity
from takeoff.services.trusted_links import (
    entities_with_multiple_trusted_tasks,
    linked_entity_gids_for_project,
    trusted_counts,
    trusted_fanout_sizes,
    trusted_task_ids,
)

logger = logging.getLogger(__name__)

MAX_CLASS_ROWS = 200
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
UNASSIGNED_LEVEL_KEY = "__unassigned__"

ACTIVITY_ID_KEYS = ("Identity Data.Activity ID", "Activity ID")
GENERIC_PROP_VALUES = frozenset({"", "none", "n/a", "-", "0", "unnamed", "generic"})
FANOUT_WARN_ELEMENTS = 100
DOMINANT_STOREY_WARN_PCT = 80.0
WEAK_QTO_MIN_ELEMENTS = 10
WEAK_QTO_PCT = 50.0
SPATIAL_IFC_TYPES = frozenset({"IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcSpace"})

STATUS_READY = "ready"
STATUS_WARNING = "warning"
STATUS_BLOCKED = "blocked"
STATUS_UNAVAILABLE = "unavailable"
STATUS_LABELS = {
    STATUS_READY: "Ready",
    STATUS_WARNING: "Warning",
    STATUS_BLOCKED: "Blocked",
    STATUS_UNAVAILABLE: "Unavailable",
}

ACTION_LINKS = "links"
ACTION_ELEMENTS = "elements"
ACTION_QUANTITIES = "quantities"
ACTION_TIME_VIEW = "time_view"
ACTION_AUTHORING = "authoring"
ACTION_SCHEDULE = "schedule"


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


def _is_filled_value(value: Any) -> bool:
    """True when a property/name value is present and not a generic placeholder."""
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in GENERIC_PROP_VALUES


def _activity_id_state(props: Any) -> str:
    """Return filled | empty | absent for Parameter Match Activity ID keys."""
    if not isinstance(props, dict):
        return "absent"
    for key in ACTIVITY_ID_KEYS:
        if key not in props:
            continue
        return "filled" if _is_filled_value(props.get(key)) else "empty"
    return "absent"


def _week_span(start: date | None, end: date | None) -> int:
    """Inclusive weekly interval count between two dates; 0 when unbounded."""
    if start is None or end is None or end < start:
        return 0
    return (end - start).days // 7 + 1


def _readiness_card(
    *,
    card_id: str,
    title: str,
    copy: str,
    status: str,
    metric: str,
    detail: str,
    next_step: str,
    action_id: str,
) -> dict[str, str]:
    """One semantic readiness card for the Model page."""
    return {
        "id": card_id,
        "title": title,
        "copy": copy,
        "status": status,
        "status_label": STATUS_LABELS.get(status, STATUS_LABELS[STATUS_UNAVAILABLE]),
        "metric": metric,
        "detail": detail,
        "next_step": next_step,
        "action_id": action_id,
    }


def _finding(
    *,
    finding_id: str,
    text: str,
    action_id: str,
    action_label: str,
    title: str = "",
    why: str = "",
    count: str = "",
    severity: str = STATUS_WARNING,
    issue_type: str = "linkability",
) -> dict[str, str]:
    return {
        "id": finding_id,
        "text": text,
        "title": title or text,
        "why": why,
        "count": count,
        "severity": severity,
        "severity_label": STATUS_LABELS.get(severity, STATUS_LABELS[STATUS_WARNING]),
        "issue_type": issue_type,
        "action_id": action_id,
        "action_label": action_label,
    }


def _status_rank(status: str) -> int:
    return {
        STATUS_BLOCKED: 0,
        STATUS_WARNING: 1,
        STATUS_UNAVAILABLE: 2,
        STATUS_READY: 3,
    }.get(status, 2)


def _worst_status(*statuses: str) -> str:
    return min(statuses, key=_status_rank)


def _share(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * float(part) / float(total), 1)


class ModelInventoryService:
    """Build Model Readiness payload: cards first, inventory grids as evidence."""

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
        space_count = IFCSpatialElement.objects.filter(
            ifc_file=ifc_file, spatial_type="space"
        ).count()

        by_type: dict[str, dict[str, int]] = {}
        by_level: dict[str, dict[str, Any]] = {}
        with_qty = 0
        trusted_linked = 0
        missing_level = 0
        missing_name = 0
        missing_type = 0
        activity_id_present = 0
        activity_id_filled = 0

        for (
            ifc_type,
            global_id,
            name,
            element_type_id,
            props,
            sc_id,
            sc_type,
            sc_name,
        ) in entities_qs.values_list(
            "ifc_type",
            "global_id",
            "name",
            "element_type_id",
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

            if not (name or "").strip():
                missing_name += 1
            if element_type_id is None:
                missing_type += 1
            aid_state = _activity_id_state(props)
            if aid_state != "absent":
                activity_id_present += 1
            if aid_state == "filled":
                activity_id_filled += 1

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
        overview = {
            "total_entities": total_entities,
            "ifc_class_count": class_count,
            "storey_count": storey_count,
            "space_count": space_count,
            "trusted_linked_entities": trusted_linked,
            "unlinked_entities": unlinked,
            "link_coverage_pct": coverage_pct,
            "entities_with_quantity": with_qty,
            "quantity_availability_pct": qty_pct,
            "has_quantities": with_qty > 0,
            "has_trusted_links": trusted_linked > 0,
            "has_storeys": storey_count > 0,
        }
        missing_model_data = {
            "missing_level_count": missing_level,
            "missing_ifc_qto_count": missing_qto,
            "classification_coverage": "unavailable",
            "classification_message": "Classification breakdown: Unavailable",
        }
        readiness = self._build_readiness(
            overview=overview,
            class_rows=class_rows,
            level_rows=level_rows,
            missing=missing_model_data,
            missing_name=missing_name,
            missing_type=missing_type,
            activity_id_present=activity_id_present,
            activity_id_filled=activity_id_filled,
        )

        return {
            "has_ifc": True,
            "ifc_file_name": ifc_file.name,
            "overview": overview,
            "by_class": class_rows,
            "by_class_truncated": truncated,
            "by_class_total_types": class_count,
            "by_level": level_rows,
            "missing_model_data": missing_model_data,
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
            "readiness": readiness,
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

    def _build_readiness(
        self,
        *,
        overview: dict[str, Any],
        class_rows: list[dict[str, Any]],
        level_rows: list[dict[str, Any]],
        missing: dict[str, Any],
        missing_name: int,
        missing_type: int,
        activity_id_present: int,
        activity_id_filled: int,
    ) -> dict[str, Any]:
        """Assemble six semantic cards and compact findings from indexed data.

        Playback uses dated-task aggregates only. Do not call
        TimelinePayloadService.build_summary() here — that walk is too expensive
        for Model GET.
        """
        from django.db.models import Max, Min

        from scheduling.models import Task

        total = int(overview.get("total_entities") or 0)
        linked_entities = int(overview.get("trusted_linked_entities") or 0)
        unlinked = int(overview.get("unlinked_entities") or 0)
        coverage = overview.get("link_coverage_pct")
        storey_count = int(overview.get("storey_count") or 0)
        space_count = int(overview.get("space_count") or 0)
        missing_level = int(missing.get("missing_level_count") or 0)
        missing_qto = int(missing.get("missing_ifc_qto_count") or 0)
        qto_pct = overview.get("quantity_availability_pct")

        counts = trusted_counts(self.project_id)
        linked_tasks = int(counts.get("trusted_tasks") or 0)
        tasks_total = Task.objects.filter(project=self.project).count()
        fan_sizes = trusted_fanout_sizes(self.project_id)
        max_fanout = fan_sizes[-1] if fan_sizes else 0
        median_fanout = fan_sizes[len(fan_sizes) // 2] if fan_sizes else 0
        p90_fanout = fan_sizes[int(len(fan_sizes) * 0.9)] if fan_sizes else 0
        multi_gid = len(entities_with_multiple_trusted_tasks(self.project_id))
        task_pct = round(100.0 * linked_tasks / tasks_total, 1) if tasks_total else None

        codes_n = Task.objects.filter(project=self.project).exclude(activity_code="").count()
        # Canonical WBS is a package-era field; origin/main may not have it yet.
        if hasattr(Task, "wbs_node_id"):
            wbs_n = Task.objects.filter(project=self.project).exclude(wbs_node_id=None).count()
        else:
            wbs_n = 0

        dated = (
            Task.objects.filter(project=self.project)
            .exclude(start_date=None)
            .exclude(end_date=None)
            .aggregate(min_start=Min("start_date"), max_end=Max("end_date"))
        )
        programme_start: date | None = dated.get("min_start")
        programme_end: date | None = dated.get("max_end")
        interval_count = _week_span(programme_start, programme_end)

        first_linked_start: date | None = None
        if linked_tasks:
            linked_ids = trusted_task_ids(self.project_id)
            first_linked_start = (
                Task.objects.filter(pk__in=linked_ids)
                .exclude(start_date=None)
                .aggregate(first=Min("start_date"))
                .get("first")
            )
        empty_colour_intervals = 0
        if programme_start and first_linked_start and first_linked_start > programme_start:
            empty_colour_intervals = max(0, _week_span(programme_start, first_linked_start) - 1)

        assigned_levels = [
            row
            for row in level_rows
            if row.get("level_key") != UNASSIGNED_LEVEL_KEY and int(row.get("entity_count") or 0)
        ]
        dominant_pct = None
        dominant_label = ""
        if assigned_levels and total:
            top = assigned_levels[0]
            dominant_label = str(top.get("level_label") or "")
            dominant_pct = round(100.0 * int(top["entity_count"]) / total, 1)

        weak_classes = [
            row["ifc_type"]
            for row in class_rows
            if row.get("ifc_type") not in SPATIAL_IFC_TYPES
            and int(row.get("element_count") or 0) >= WEAK_QTO_MIN_ELEMENTS
            and (
                100.0 * int(row.get("quantity_available") or 0) / int(row["element_count"])
                < WEAK_QTO_PCT
            )
        ]

        # --- Card 1: Linkability ---
        if total == 0:
            link_status = STATUS_UNAVAILABLE
            link_metric = "No indexed elements"
        else:
            if (
                linked_entities == 0
                or unlinked > 0
                or (activity_id_present and activity_id_filled < total * 0.5)
            ):
                link_status = STATUS_WARNING
            else:
                link_status = STATUS_READY
            cov_txt = f"{coverage}%" if coverage is not None else "—"
            link_metric = f"{cov_txt} element link coverage · {unlinked} unlinked"
        if activity_id_present:
            aid_pct = round(100.0 * activity_id_filled / total, 1) if total else 0
            aid_line = (
                f"Activity ID filled on {activity_id_filled} of {total} elements ({aid_pct}%)."
            )
        else:
            aid_line = (
                "Activity ID key not found on indexed properties — "
                "Parameter Match needs a named key under Links."
            )
            if link_status == STATUS_READY:
                link_status = STATUS_WARNING
        link_detail = (
            f"{aid_line} Missing name: {missing_name}. Missing type: {missing_type}. "
            f"Missing storey: {missing_level}."
        )

        # --- Card 2: Granularity ---
        if tasks_total == 0:
            gran_status = STATUS_UNAVAILABLE
            gran_metric = "No schedule tasks"
        else:
            gran_metric = f"{linked_tasks} linked tasks of {tasks_total}"
            if linked_tasks == 0 or max_fanout >= FANOUT_WARN_ELEMENTS:
                gran_status = STATUS_WARNING
            elif linked_tasks < tasks_total:
                gran_status = STATUS_WARNING
            else:
                gran_status = STATUS_READY
        gran_detail = (
            f"Largest applied/confirmed task has {max_fanout} linked elements"
            f"{' (high fan-out)' if max_fanout >= FANOUT_WARN_ELEMENTS else ''}. "
            f"Elements linked to more than one task: {multi_gid}. "
            "Mixed IFC class on one activity is expected and is not treated as a defect."
        )

        # --- Card 3: Spatial ---
        if storey_count == 0:
            spat_status = STATUS_BLOCKED
            spat_metric = "No storeys indexed"
        else:
            dom_txt = f"{dominant_pct}%" if dominant_pct is not None else "—"
            spat_metric = (
                f"{storey_count} storey · {dom_txt} on dominant level"
                if storey_count == 1
                else f"{storey_count} storeys · {dom_txt} on dominant level"
            )
            if storey_count == 1 or (
                dominant_pct is not None and dominant_pct >= DOMINANT_STOREY_WARN_PCT
            ):
                spat_status = STATUS_WARNING
            elif missing_level > 0:
                spat_status = STATUS_WARNING
            else:
                spat_status = STATUS_READY
        zone_line = (
            f"{space_count} spaces indexed."
            if space_count
            else "No spaces/zones indexed — zone sequencing is unavailable."
        )
        spat_detail = f"{missing_level} elements missing a storey. {zone_line}" + (
            f" Dominant storey: {dominant_label}." if dominant_label else ""
        )

        # --- Card 4: Playback (lightweight dates; not Time View embed) ---
        if linked_entities == 0:
            play_status = STATUS_WARNING
            play_metric = "No applied links to colour"
            play_detail = (
                "Time View hides unlinked and not-started elements. "
                "Open Time View after confirming links. "
                "This card does not run full playback."
            )
        elif programme_start is None:
            play_status = STATUS_UNAVAILABLE
            play_metric = "No dated schedule tasks"
            play_detail = "Playback needs dated tasks. Open Time View when dates exist."
        else:
            first_txt = first_linked_start.isoformat() if first_linked_start else "—"
            play_metric = f"{interval_count} weekly intervals · first linked start {first_txt}"
            play_detail = (
                f"About {empty_colour_intervals} intervals before the earliest linked "
                f"activity start may show no colour (not-started is hidden). "
                "Estimated from linked-task dates — open Time View for playback."
            )
            play_status = STATUS_WARNING if empty_colour_intervals > 0 else STATUS_READY

        # --- Card 5: QTO ---
        if qto_pct is None or total == 0:
            qto_status = STATUS_UNAVAILABLE
            qto_metric = "No IFC quantities scored"
        else:
            qto_metric = f"{qto_pct}% have IFC QTO · {missing_qto} missing"
            if missing_qto == 0 and not weak_classes:
                qto_status = STATUS_READY
            else:
                qto_status = STATUS_WARNING
        if weak_classes:
            qto_detail = (
                "Weak QTO coverage: "
                + ", ".join(weak_classes[:6])
                + ". Length/area linear totals stay in model units on Quantities."
            )
        else:
            qto_detail = (
                "IFC Qto_* measures only — not BOQ. Open Quantities for totals and unit caveats."
            )

        # --- Card 6: Classification ---
        class_status = STATUS_UNAVAILABLE
        class_metric = "Classification not indexed"
        class_detail = (
            f"Task activity codes: {codes_n} of {tasks_total}. "
            f"WBS nodes: {wbs_n} of {tasks_total}. "
            "Task Legend Groups need a later saved appearance profile — not in this package."
        )

        cards = [
            _readiness_card(
                card_id="linkability",
                title="Linkability Readiness",
                copy="Can rule-based linking find reliable model keys?",
                status=link_status,
                metric=link_metric,
                detail=link_detail,
                next_step="Open Links",
                action_id=ACTION_LINKS,
            ),
            _readiness_card(
                card_id="granularity",
                title="Schedule–Model Granularity Fit",
                copy="Is the schedule-to-model relationship balanced enough for playback?",
                status=gran_status,
                metric=gran_metric,
                detail=gran_detail,
                next_step="Review Links / Schedule",
                action_id=ACTION_SCHEDULE,
            ),
            _readiness_card(
                card_id="spatial",
                title="Spatial Readiness",
                copy="Can the model support floor/zone-based sequencing?",
                status=spat_status,
                metric=spat_metric,
                detail=spat_detail,
                next_step="Model authoring required",
                action_id=ACTION_AUTHORING,
            ),
            _readiness_card(
                card_id="playback",
                title="Playback Readiness",
                copy="Will Time View show meaningful model changes over the programme?",
                status=play_status,
                metric=play_metric,
                detail=play_detail,
                next_step="Open Time View",
                action_id=ACTION_TIME_VIEW,
            ),
            _readiness_card(
                card_id="qto",
                title="QTO / 5D Readiness",
                copy="Do indexed elements have IFC quantities for 5D foundation?",
                status=qto_status,
                metric=qto_metric,
                detail=qto_detail,
                next_step="Open Quantities",
                action_id=ACTION_QUANTITIES,
            ),
            _readiness_card(
                card_id="classification",
                title="Classification / Breakdown Readiness",
                copy="Are classification or breakdown axes available for advanced 4D/5D?",
                status=class_status,
                metric=class_metric,
                detail=class_detail,
                next_step="Classification stays unavailable until indexed",
                action_id=ACTION_AUTHORING,
            ),
        ]

        elem_high = coverage is not None and coverage >= 80
        task_low = bool(tasks_total) and (linked_tasks / tasks_total) < 0.25

        findings: list[dict[str, str]] = []
        if unlinked > 0:
            findings.append(
                _finding(
                    finding_id="unlinked",
                    text=f"{unlinked} unlinked model elements",
                    title="Unlinked model elements",
                    count=str(unlinked),
                    why="These elements will not appear in applied/confirmed 4D playback.",
                    severity=STATUS_WARNING,
                    issue_type="linkability",
                    action_id=ACTION_LINKS,
                    action_label="Open Links",
                )
            )
        if task_low:
            findings.append(
                _finding(
                    finding_id="task-coverage",
                    text=f"{linked_tasks} of {tasks_total} tasks have model links",
                    title="Low task-link coverage",
                    count=f"{linked_tasks}/{tasks_total}",
                    why="Most programme activities have no model relationship yet.",
                    severity=STATUS_WARNING,
                    issue_type="linkability",
                    action_id=ACTION_SCHEDULE,
                    action_label="Open Schedule",
                )
            )
        if storey_count <= 1 or (
            dominant_pct is not None and dominant_pct >= DOMINANT_STOREY_WARN_PCT
        ):
            findings.append(
                _finding(
                    finding_id="dominant-storey",
                    text="One storey dominates the model",
                    title="One storey dominates the model",
                    count=f"{dominant_pct}%" if dominant_pct is not None else str(storey_count),
                    why="Floor/zone sequencing cannot be inferred from this IFC structure.",
                    severity=STATUS_WARNING if storey_count else STATUS_BLOCKED,
                    issue_type="spatial",
                    action_id=ACTION_AUTHORING,
                    action_label="Model authoring required",
                )
            )
        if missing_qto > 0:
            findings.append(
                _finding(
                    finding_id="missing-qto",
                    text=f"{missing_qto} elements missing IFC QTO",
                    title="Elements missing IFC QTO",
                    count=str(missing_qto),
                    why="These elements are weak for 5D foundation.",
                    severity=STATUS_WARNING,
                    issue_type="qto",
                    action_id=ACTION_QUANTITIES,
                    action_label="Open Quantities",
                )
            )
        if max_fanout >= FANOUT_WARN_ELEMENTS:
            findings.append(
                _finding(
                    finding_id="fanout",
                    text=f"One linked task is tied to {max_fanout} model elements",
                    title="High schedule–model fan-out",
                    count=str(max_fanout),
                    why="A single activity painting this many elements can overwhelm playback.",
                    severity=STATUS_WARNING,
                    issue_type="granularity",
                    action_id=ACTION_LINKS,
                    action_label="Open Links",
                )
            )
        if empty_colour_intervals > 0:
            findings.append(
                _finding(
                    finding_id="playback-empty",
                    text=f"{empty_colour_intervals} early intervals may show no colour",
                    title="Empty lead-in before colour",
                    count=str(empty_colour_intervals),
                    why="Time View hides not-started elements, so early dates can look empty.",
                    severity=STATUS_WARNING,
                    issue_type="playback",
                    action_id=ACTION_TIME_VIEW,
                    action_label="Open Time View",
                )
            )
        findings.append(
            _finding(
                finding_id="classification",
                text="Classification not indexed",
                title="Classification not indexed",
                count="—",
                why="Advanced breakdowns and task legend groups are unavailable.",
                severity=STATUS_UNAVAILABLE,
                issue_type="classification",
                action_id=ACTION_AUTHORING,
                action_label="Future",
            )
        )

        overall_status = _worst_status(
            link_status, gran_status, spat_status, qto_status, play_status
        )
        if total == 0:
            overall_status = STATUS_UNAVAILABLE
            overall_sentence = "No IFC model is indexed yet, so 4D/5D readiness cannot be scored."
        else:
            limits: list[str] = []
            if task_low:
                limits.append("task coverage")
            if spat_status in {STATUS_WARNING, STATUS_BLOCKED}:
                limits.append("spatial breakdown")
            if qto_status == STATUS_WARNING:
                limits.append("QTO gaps")
            if elem_high:
                core = "The model is indexed and mostly linked by element"
            elif linked_entities == 0:
                core = "The model is indexed but has no applied/confirmed element links yet"
            else:
                core = "The model is indexed with partial element links"
            if limits:
                overall_sentence = f"{core}, but {' and '.join(limits)} limit 4D readiness."
            else:
                overall_sentence = f"{core}. Review the charts below for remaining 4D/5D gaps."

        empty_lead_pct = (
            round(100.0 * empty_colour_intervals / interval_count, 1) if interval_count else 0.0
        )
        hist_defs = (
            ("1–10", lambda n: n <= 10),
            ("11–50", lambda n: 11 <= n <= 50),
            ("51–100", lambda n: 51 <= n <= 100),
            ("100+", lambda n: n > 100),
        )
        hist_raw = [
            {"label": label, "n": sum(1 for n in fan_sizes if pred(n))} for label, pred in hist_defs
        ]
        hist_max = max((row["n"] for row in hist_raw), default=0) or 1
        fan_histogram = [
            {**row, "bar_pct": round(100.0 * row["n"] / hist_max, 1)} for row in hist_raw
        ]

        with_qty = int(overview.get("entities_with_quantity") or 0)
        charts = {
            "element": {
                "linked": linked_entities,
                "unlinked": unlinked,
                "total": total,
                "pct": coverage,
                "linked_bar_pct": _share(linked_entities, total),
                "unlinked_bar_pct": _share(unlinked, total),
                "scope": "filter-aware",
            },
            "task": {
                "linked": linked_tasks,
                "unlinked": max(0, tasks_total - linked_tasks),
                "total": tasks_total,
                "pct": task_pct,
                "linked_bar_pct": _share(linked_tasks, tasks_total),
                "unlinked_bar_pct": _share(max(0, tasks_total - linked_tasks), tasks_total),
                "scope": "project-wide",
            },
            "spatial": {
                "storey_count": storey_count,
                "dominant_pct": dominant_pct,
                "dominant_label": dominant_label,
                "missing_level": missing_level,
                "space_count": space_count,
                "status": spat_status,
                "scope": "project-wide",
            },
            "qto": {
                "with_qto": with_qty,
                "missing": missing_qto,
                "pct": qto_pct,
                "with_bar_pct": _share(with_qty, total),
                "missing_bar_pct": _share(missing_qto, total),
                "weak_classes": weak_classes[:6],
                "scope": "filter-aware",
            },
            "granularity": {
                "linked_tasks": linked_tasks,
                "max_fanout": max_fanout,
                "median_fanout": median_fanout,
                "p90_fanout": p90_fanout,
                "multi_gid": multi_gid,
                "warn_at": FANOUT_WARN_ELEMENTS,
                "histogram": fan_histogram,
                "scope": "project-wide",
            },
            "playback": {
                "programme_start": programme_start.isoformat() if programme_start else None,
                "programme_end": programme_end.isoformat() if programme_end else None,
                "first_linked_start": (
                    first_linked_start.isoformat() if first_linked_start else None
                ),
                "interval_count": interval_count,
                "empty_colour_intervals": empty_colour_intervals,
                "empty_lead_pct": empty_lead_pct,
                "colour_pct": max(0.0, round(100.0 - empty_lead_pct, 1)),
                "scope": "project-wide",
            },
        }
        chart_data = {
            "project": {
                "elements_total": total,
                "elements_linked": linked_entities,
                "elements_unlinked": unlinked,
                "qto_yes": with_qty,
                "qto_no": missing_qto,
            },
            "by_class": [
                {
                    "key": row["ifc_type"],
                    "name": row["ifc_type"],
                    "elements": row["element_count"],
                    "linked": row["trusted_linked"],
                    "unlinked": row["unlinked"],
                    "qto": row["quantity_available"],
                }
                for row in class_rows
            ],
            "by_level": [
                {
                    "key": row["level_key"],
                    "name": row["level_label"],
                    "elements": row["entity_count"],
                    "linked": row["linked_count"],
                    "unlinked": row["unlinked_count"],
                    "qto": row["has_ifc_qto_count"],
                }
                for row in level_rows
            ],
        }

        return {
            "cards": cards,
            "findings": findings[:7],
            "playback_source": "linked_task_dates",
            "playback_not_timeline_summary": True,
            "summary": {
                "status": overall_status,
                "status_label": STATUS_LABELS[overall_status],
                "sentence": overall_sentence,
                "element_pct": coverage,
                "task_pct": task_pct,
                "qto_pct": qto_pct,
                "spatial_status": spat_status,
                "spatial_status_label": STATUS_LABELS[spat_status],
                "linked_tasks": linked_tasks,
                "tasks_total": tasks_total,
            },
            "charts": charts,
            "chart_data": chart_data,
        }

    def _empty(self, *, has_ifc: bool, ifc_file_name: str | None = None) -> dict[str, Any]:
        return {
            "has_ifc": has_ifc,
            "ifc_file_name": ifc_file_name,
            "overview": {
                "total_entities": 0,
                "ifc_class_count": 0,
                "storey_count": 0,
                "space_count": 0,
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
                "classification_message": "Classification breakdown: Unavailable",
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
            "readiness": self._build_readiness(
                overview={
                    "total_entities": 0,
                    "ifc_class_count": 0,
                    "storey_count": 0,
                    "space_count": 0,
                    "trusted_linked_entities": 0,
                    "unlinked_entities": 0,
                    "link_coverage_pct": None,
                    "entities_with_quantity": 0,
                    "quantity_availability_pct": None,
                    "has_quantities": False,
                    "has_trusted_links": False,
                    "has_storeys": False,
                },
                class_rows=[],
                level_rows=[],
                missing={
                    "missing_level_count": 0,
                    "missing_ifc_qto_count": 0,
                },
                missing_name=0,
                missing_type=0,
                activity_id_present=0,
                activity_id_filled=0,
            ),
            "honesty": {
                "not_boq": True,
                "not_qs_valuation": True,
                "not_erp": True,
                "not_company_actual_cost": True,
                "not_commercial_5d": True,
                "quantities_label": "IFC model quantities",
            },
        }
