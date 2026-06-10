# castor/ifc_viewer/views.py
"""Castor Simulator tab views — 3D viewer, fragment cache, colormap, gap analysis, timeline."""

from __future__ import annotations

import csv
import io
import logging
import os
from datetime import date, timedelta

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views import View
from django.views.generic import TemplateView

from core.mixins import ProjectAccessMixin, ProjectTabMixin
from ifc_processor.models import IFCFile

from .services.colormap import build_colormap
from .services.gap_analysis import build_gap_analysis

logger = logging.getLogger(__name__)

UNITS_MAP: dict[str, str] = {
    "volume": "m³",
    "netvolume": "m³",
    "grossvolume": "m³",
    "area": "m²",
    "netarea": "m²",
    "grossarea": "m²",
    "netsidearea": "m²",
    "grosssidearea": "m²",
    "length": "mm",
    "width": "mm",
    "height": "mm",
    "thickness": "mm",
    "depth": "mm",
    "perimeter": "mm",
    "weight": "kg",
    "count": "ea",
}


def _apply_units(props: dict) -> dict:
    """Format numeric property values: 3dp, comma-separated thousands, units where known."""
    result: dict = {}
    for key, value in props.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            result[key] = value
            continue
        rounded = round(float(value), 3)
        formatted = f"{rounded:,.3f}".rstrip("0").rstrip(".")
        attr = key.rsplit(".", 1)[-1].lower().replace(" ", "")
        unit = UNITS_MAP.get(attr)
        if unit:
            display = f"{formatted} {unit}"
            if unit == "mm" and abs(value) > 1000:
                m_str = f"{round(value / 1000, 3):.3f}".rstrip("0").rstrip(".")
                display += f" ({m_str} m)"
        else:
            display = formatted
        result[key] = display
    return result


class ViewerView(ProjectTabMixin, TemplateView):
    """Renders the 3D IFC viewer partial."""

    active_tab = "castor"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        project = ctx["project"]
        ctx["castor_subtab"] = "viewer"

        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        ctx["viewer_ifc_file"] = ifc_file
        ctx["ifc_file_url"] = ifc_file.file.url if ifc_file else None
        return ctx


class FragmentsCacheView(ProjectAccessMixin, View):
    """GET/POST endpoint for the pre-computed .frag geometry cache.

    GET  — returns the .frag binary if it exists, 404 otherwise.
    POST — receives the .frag binary as a raw octet-stream body and saves it.

    The .frag path is derived from the latest completed IFC file path with the
    extension swapped to .frag.  Each new IFC upload gets a UUID-named file, so
    a new upload automatically invalidates the old cache without explicit cleanup.
    """

    def _frag_path(self, project) -> str | None:
        """Return the filesystem path for the .frag cache file, or None."""
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file or not ifc_file.file.name:
            return None
        return ifc_file.file.path.rsplit(".", 1)[0] + ".frag"

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        frag_path = self._frag_path(project)
        if not frag_path or not os.path.exists(frag_path):
            return HttpResponse(status=404)
        with open(frag_path, "rb") as f:
            return HttpResponse(f.read(), content_type="application/octet-stream")

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        frag_path = self._frag_path(project)
        if not frag_path:
            return HttpResponse("No completed IFC file found.", status=404)
        body = request.body
        if not body:
            return HttpResponse("Empty body.", status=400)
        with open(frag_path, "wb") as f:
            f.write(body)
        logger.info("Fragment cache saved: %s (%d bytes)", frag_path, len(body))
        return HttpResponse(status=201)


# ---------------------------------------------------------------------------
# Castor Simulator — data endpoints
# ---------------------------------------------------------------------------


class ColormapView(ProjectAccessMixin, View):
    """JSON — {colormap: {global_id: color_hex}, legend: [{label, color}]} for Color By toolbar."""

    _VALID = {"material", "level", "element_type", "schedule_status"}

    def get(self, request, **kwargs: object) -> HttpResponse:
        by = request.GET.get("by", "element_type")
        if by not in self._VALID:
            return JsonResponse({"error": "invalid by parameter"}, status=400)

        project = self.get_project()
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file:
            return JsonResponse({"colormap": {}, "legend": []})

        return JsonResponse(build_colormap(ifc_file, by, project_id=str(project.pk)))


class GapAnalysisView(ProjectAccessMixin, View):
    """HTMX — gap analysis panel HTML; ?export=1 returns CSV download."""

    _VALID_BY = {"level", "element_type", "material"}

    def get(self, request, **kwargs: object) -> HttpResponse:
        by = request.GET.get("by", "level")
        if by not in self._VALID_BY:
            by = "level"

        project = self.get_project()
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )

        if not ifc_file:
            return render(
                request,
                "ifc_viewer/components/gap_analysis_panel.html",
                {"rows": [], "by": by, "project": project},
            )

        rows = build_gap_analysis(ifc_file, by, project_id=str(project.pk))

        if request.GET.get("export"):
            return self._csv_response(rows, project.name)

        return render(
            request,
            "ifc_viewer/components/gap_analysis_panel.html",
            {"rows": rows, "by": by, "project": project},
        )

    def _csv_response(self, rows: list[dict], project_name: str) -> HttpResponse:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["group", "total", "linked", "pct", "gap"])
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in ["group", "total", "linked", "pct", "gap"]})
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in project_name)
        response = HttpResponse(buf.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="gap_analysis_{safe}.csv"'
        return response


class EntityTypesView(ProjectAccessMixin, View):
    """JSON — distinct IFC types and level names for the Selection sidebar dropdowns.

    No params  → {types: [...], levels: [...]}
    ?type=X    → {global_ids: [...]} all entities of that IFC type
    ?level=X   → {global_ids: [...]} all entities in that building storey
    """

    def get(self, request, **kwargs: object) -> HttpResponse:
        from ifc_processor.models import IFCEntity, IFCSpatialElement

        project = self.get_project()
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file:
            return JsonResponse({"types": [], "levels": [], "global_ids": []})

        select_type = request.GET.get("type")
        select_level = request.GET.get("level")

        if select_type:
            gids = list(
                IFCEntity.objects.filter(ifc_file=ifc_file, ifc_type=select_type).values_list(
                    "global_id", flat=True
                )
            )
            return JsonResponse({"global_ids": gids})

        if select_level:
            gids = list(
                IFCEntity.objects.filter(
                    ifc_file=ifc_file,
                    spatial_container__entity__name=select_level,
                ).values_list("global_id", flat=True)
            )
            return JsonResponse({"global_ids": gids})

        types = list(
            IFCEntity.objects.filter(ifc_file=ifc_file)
            .values_list("ifc_type", flat=True)
            .distinct()
            .order_by("ifc_type")
        )
        levels = list(
            IFCSpatialElement.objects.filter(
                ifc_file=ifc_file,
                spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
            )
            .select_related("entity")
            .order_by("elevation")
            .values_list("entity__name", flat=True)
        )
        return JsonResponse({"types": types, "levels": levels})


class SpatialTreeView(ProjectAccessMixin, View):
    """JSON — spatial tree grouped by building storey → IFC type → elements.

    Returns {tree: [{name, type, count, global_ids, children: [{name, count, global_ids,
    children: [{globalId, name}]}]}]}.  Each type group is capped at MAX_PER_GROUP
    leaf elements for response-size safety.
    """

    MAX_PER_GROUP = 50

    def get(self, request, **kwargs: object) -> HttpResponse:
        from collections import defaultdict

        from ifc_processor.models import IFCEntity, IFCSpatialElement

        project = self.get_project()
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file:
            return JsonResponse({"tree": []})

        storeys = list(
            IFCSpatialElement.objects.filter(
                ifc_file=ifc_file,
                spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
            )
            .select_related("entity")
            .order_by("elevation")
        )

        tree = []
        for storey in storeys:
            storey_name = storey.entity.name if storey.entity_id else "Unknown Level"

            entities = list(
                IFCEntity.objects.filter(ifc_file=ifc_file, spatial_container=storey)
                .only("global_id", "name", "ifc_type")
                .order_by("ifc_type", "name")
            )
            if not entities:
                continue

            by_type: dict[str, list] = defaultdict(list)
            for e in entities:
                by_type[e.ifc_type].append(e)

            all_gids = [e.global_id for e in entities]
            type_groups = [
                {
                    "name": ifc_type,
                    "count": len(items),
                    "global_ids": [e.global_id for e in items],
                    "children": [
                        {"globalId": e.global_id, "name": e.name or e.global_id[:8]}
                        for e in items[: self.MAX_PER_GROUP]
                    ],
                }
                for ifc_type, items in sorted(by_type.items())
            ]

            tree.append(
                {
                    "name": storey_name,
                    "type": "IfcBuildingStorey",
                    "count": len(entities),
                    "global_ids": all_gids,
                    "children": type_groups,
                }
            )

        return JsonResponse({"tree": tree})


class BuildSequenceView(ProjectAccessMixin, View):
    """JSON — [{level_name, z_elevation, entity_global_ids}] sorted by Z for Build Sequence animation."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file:
            return JsonResponse({"levels": []})

        from ifc_processor.models import IFCEntity
        from model_quality.models import Level

        level_z: dict[str, float] = {
            lvl.name: lvl.z_elevation for lvl in Level.objects.filter(project=project)
        }

        groups: dict[str, list[str]] = {}
        for entity in (
            IFCEntity.objects.filter(ifc_file=ifc_file)
            .only("global_id", "spatial_container")
            .iterator(chunk_size=500)
        ):
            key = entity.spatial_container or "Unknown"
            groups.setdefault(key, []).append(entity.global_id)

        levels = [
            {
                "level_name": name,
                "z_elevation": level_z.get(name, 0.0),
                "entity_global_ids": ids,
            }
            for name in sorted(groups, key=lambda n: (level_z.get(n, float("inf")), n))
            for ids in [groups[name]]
        ]

        return JsonResponse({"levels": levels})


class ViewerEmbedView(ProjectAccessMixin, View):
    """GET — minimal self-contained viewer page designed to be loaded in an <iframe>.

    Returns X-Frame-Options: SAMEORIGIN so browsers allow it to be framed
    from the same origin.  Has no page chrome — just the WebGL canvas,
    a loading overlay, and a postMessage API for the parent tab.
    """

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        ctx = {
            "project": project,
            "viewer_ifc_file": ifc_file,
            "ifc_file_url": ifc_file.file.url if ifc_file else None,
        }
        response = render(request, "ifc_viewer/viewer_embed.html", ctx)
        response["X-Frame-Options"] = "SAMEORIGIN"
        return response


class ElementPropertiesView(ProjectAccessMixin, View):
    """JSON — IFC entity properties for a GlobalId clicked in the 3D viewer."""

    def get(self, request, global_id: str, **kwargs: object) -> HttpResponse:
        from ifc_processor.models import IFCEntity, IFCFile

        project = self.get_project()
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file:
            return JsonResponse({"found": False, "global_id": global_id})

        try:
            entity = IFCEntity.objects.select_related(
                "spatial_container__entity",
                "element_type",
            ).get(ifc_file=ifc_file, global_id=global_id)
        except IFCEntity.DoesNotExist:
            return JsonResponse({"found": False, "global_id": global_id})

        location = None
        if entity.spatial_container_id and entity.spatial_container.entity_id:
            location = entity.spatial_container.entity.name or None

        from scheduling.models import TaskEntityBinding

        bindings = (
            TaskEntityBinding.objects.filter(
                entity_global_id=global_id,
                task__project=project,
            )
            .select_related("task")
            .order_by("task__start_date")
        )
        linked_tasks = [
            {
                "activity_code": b.task.activity_code,
                "name": b.task.name,
                "status": b.task.status,
                "planned_start": b.task.start_date.isoformat() if b.task.start_date else None,
                "planned_end": b.task.end_date.isoformat() if b.task.end_date else None,
                "actual_start": b.task.actual_start.isoformat() if b.task.actual_start else None,
                "actual_end": b.task.actual_end.isoformat() if b.task.actual_end else None,
                "stage": b.task.stage,
                "sub_stage": b.task.sub_stage,
            }
            for b in bindings
        ]

        # Type.* keys duplicate instance-level properties (15/16 identical values).
        filtered_props = {k: v for k, v in entity.properties.items() if not k.startswith("Type.")}

        return JsonResponse(
            {
                "found": True,
                "global_id": entity.global_id,
                "ifc_type": entity.ifc_type,
                "name": entity.name,
                "ifc_description": entity.ifc_description,
                "tag": entity.tag,
                "location": location,
                "properties": _apply_units(filtered_props),
                "linked_tasks": linked_tasks,
            }
        )


class TimelineView(ProjectAccessMixin, View):
    """JSON — weekly 4D timeline intervals with 3-state entity buckets and stats."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from collections import defaultdict

        from ifc_processor.models import IFCEntity as IFCEntityModel
        from scheduling.models import Task, TaskEntityBinding

        project = self.get_project()

        tasks = list(
            Task.objects.filter(project=project, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
        )

        if not tasks:
            return JsonResponse({"has_tasks": False, "intervals": []})

        # Load all bindings for these tasks in one query, group by task_id in Python.
        # TaskEntityBinding is the authoritative source — M2M ifc_entities may lag behind.
        task_map = {t.pk: t for t in tasks}
        binding_gids: dict[object, list[str]] = defaultdict(list)
        for row in TaskEntityBinding.objects.filter(task_id__in=task_map).values(
            "task_id", "entity_global_id"
        ):
            binding_gids[row["task_id"]].append(row["entity_global_id"])

        # entity GID → [(start_date, end_date, actual_start, actual_end), ...]
        entity_tasks: dict[str, list] = defaultdict(list)
        for task_id, gids in binding_gids.items():
            task = task_map[task_id]
            for gid in gids:
                entity_tasks[gid].append(
                    (task.start_date, task.end_date, task.actual_start, task.actual_end)
                )

        # All entity GIDs across all completed IFC files for this project
        ifc_files = IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
        all_gids: list[str] = list(
            IFCEntityModel.objects.filter(ifc_file__in=ifc_files)
            .values_list("global_id", flat=True)
            .iterator(chunk_size=1000)
        ) or list(entity_tasks.keys())

        linked_set = set(entity_tasks.keys())
        no_task_gids = [gid for gid in all_gids if gid not in linked_set]
        task_gids = [gid for gid in all_gids if gid in linked_set]
        total = len(all_gids)

        min_date = min(t.start_date for t in tasks)
        max_date = max(t.end_date for t in tasks)

        def _task_state(start_date, end_date, actual_start, actual_end, snapshot):
            """Return the display state of a single task at the given snapshot date."""
            # Delayed: finished late — only visible once simulation reaches actual_start
            if (
                actual_start is not None
                and actual_end is not None
                and actual_end > end_date
                and snapshot >= actual_start
            ):
                return "delayed"
            # Delayed: still running past 120% of planned duration
            if (
                actual_start is not None
                and actual_end is None
                and snapshot >= actual_start
                and snapshot > actual_start + (end_date - start_date) * 1.2
            ):
                return "delayed"
            # Complete: has actual_end on or before planned end
            if actual_end is not None and actual_end <= end_date:
                return "complete"
            # In progress: actually started, not yet ended
            if actual_start is not None and actual_start <= snapshot and actual_end is None:
                return "in_progress"
            # No actual data — fall back to planned dates
            if end_date <= snapshot:
                return "complete"
            if start_date <= snapshot < end_date:
                return "in_progress"
            return "not_started"

        intervals = []
        current = min_date
        week_num = 1
        while current <= max_date:
            not_started: list[str] = []
            in_progress: list[str] = []
            complete: list[str] = []
            delayed: list[str] = []

            for gid in task_gids:
                states = [
                    _task_state(s, e, a_s, a_e, current) for s, e, a_s, a_e in entity_tasks[gid]
                ]
                if "delayed" in states:
                    delayed.append(gid)
                elif all(st == "complete" for st in states):
                    complete.append(gid)
                elif "in_progress" in states:
                    in_progress.append(gid)
                else:
                    not_started.append(gid)

            intervals.append(
                {
                    "date": current.isoformat(),
                    "label": f"Week {week_num}",
                    "entities": {
                        "not_started": not_started,
                        "in_progress": in_progress,
                        "complete": complete,
                        "delayed": delayed,
                    },
                    "stats": {
                        "total": total,
                        "complete": len(complete),
                        "in_progress": len(in_progress),
                        "delayed": len(delayed),
                        "not_started": len(not_started) + len(no_task_gids),
                    },
                }
            )

            current += timedelta(weeks=1)
            week_num += 1

        return JsonResponse(
            {
                "has_tasks": True,
                "project_start": min_date.isoformat(),
                "project_end": max_date.isoformat(),
                "no_task": no_task_gids,
                "intervals": intervals,
            }
        )


# ──────────────────────────────────────────────────────────────────────────────
# Stakeholder Export Report
# ──────────────────────────────────────────────────────────────────────────────

_STAGE_LABEL: dict[str, str] = {
    "substructure": "Substructure",
    "structure": "Structure",
    "envelope": "Envelope / Facade",
    "mep": "MEP",
    "finishes": "Finishes",
    "external": "External Works",
}

_STATUS_BAR_COLOR: dict[str, str] = {
    "complete": "#166534",
    "active": "#1e40af",
    "in_progress": "#1e40af",
    "delayed": "#92400e",
}


def _svg_esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _month_ticks(start: date, end: date):
    """Yield (days_from_start, label) for each month boundary in [start, end]."""
    d = date(start.year, start.month, 1)
    while d <= end:
        if d >= start:
            yield (d - start).days, d.strftime("%b %Y")
        d = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def generate_gantt_svg(
    tasks: list,
    deps: list | None = None,
    width: int = 1100,
    row_h: int = 26,
) -> str:
    """Return a self-contained SVG Gantt chart from Task objects.

    deps: list of {pred: activity_code, succ: activity_code} dicts.
    Critical-path arrows are drawn red; others gray.
    """
    HDR_H = 44
    LEFT_W = 280

    if not tasks:
        return (
            f'<svg width="{width}" height="60" xmlns="http://www.w3.org/2000/svg">'
            '<text x="12" y="34" fill="#94a3b8" font-size="13">No tasks to display.</text>'
            "</svg>"
        )

    proj_start = min(t.start_date for t in tasks if t.start_date)
    proj_end = max(t.end_date for t in tasks if t.end_date)
    total_days = max((proj_end - proj_start).days + 1, 1)
    ppd = max(1.2, min(12.0, (width - LEFT_W) / total_days))
    today = date.today()

    stage_order = ["substructure", "structure", "envelope", "mep", "finishes", "external", ""]
    sorted_tasks = sorted(
        tasks,
        key=lambda t: (
            stage_order.index(t.stage) if t.stage in stage_order else len(stage_order),
            t.start_date or date.min,
        ),
    )

    rows: list[tuple] = []
    prev_stage = object()
    for t in sorted_tasks:
        if t.stage != prev_stage:
            rows.append(("group", _STAGE_LABEL.get(t.stage or "", t.stage or "General")))
            prev_stage = t.stage
        rows.append(("task", t))

    svg_h = HDR_H + len(rows) * row_h
    p: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{svg_h}" '
        'style="font-family:Arial,sans-serif;background:#0f172a;">'
        "<defs>"
        f'<clipPath id="lcp"><rect x="0" y="0" width="{LEFT_W - 8}" height="{svg_h}"/></clipPath>'
        '<marker id="ar-c" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">'
        '<path d="M 0 0 L 5 2.5 L 0 5 Z" fill="#ef4444"/></marker>'
        '<marker id="ar-d" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">'
        '<path d="M 0 0 L 5 2.5 L 0 5 Z" fill="#475569"/></marker>'
        "</defs>",
    ]

    for days_off, _ in _month_ticks(proj_start, proj_end):
        x = LEFT_W + days_off * ppd
        if x > LEFT_W:
            p.append(
                f'<line x1="{x:.1f}" y1="{HDR_H}" x2="{x:.1f}" y2="{svg_h}"'
                ' stroke="#1e293b" stroke-width="1"/>'
            )

    p.append(f'<rect x="0" y="0" width="{width}" height="{HDR_H}" fill="#1e293b"/>')
    p.append(
        '<text x="8" y="28" fill="#94a3b8" font-size="10" font-weight="600">ACTIVITY / TASK</text>'
    )
    for days_off, lbl in _month_ticks(proj_start, proj_end):
        x = LEFT_W + days_off * ppd
        if x >= LEFT_W:
            p.append(
                f'<text x="{x + 3:.1f}" y="28" fill="#94a3b8" font-size="9">{_svg_esc(lbl)}</text>'
            )

    p.append(
        f'<line x1="{LEFT_W}" y1="0" x2="{LEFT_W}" y2="{svg_h}" stroke="#334155" stroke-width="1"/>'
    )

    today_x = LEFT_W + (today - proj_start).days * ppd
    if LEFT_W <= today_x <= width:
        p.append(
            f'<line x1="{today_x:.1f}" y1="{HDR_H}" x2="{today_x:.1f}" y2="{svg_h}"'
            ' stroke="#ef4444" stroke-width="1.5" stroke-dasharray="4,3"/>'
        )
        p.append(
            f'<text x="{today_x + 2:.1f}" y="{HDR_H + 10}"'
            ' fill="#ef4444" font-size="7" font-weight="700">TODAY</text>'
        )

    pos_map: dict[str, tuple[float, float, int]] = {}
    crit_map: dict[str, bool] = {}

    for i, row in enumerate(rows):
        y = HDR_H + i * row_h
        if row[0] == "group":
            p.append(f'<rect x="0" y="{y}" width="{width}" height="{row_h}" fill="#1e293b"/>')
            p.append(
                f'<text x="8" y="{y + row_h // 2 + 4}" fill="#94a3b8"'
                f' font-size="9" font-weight="700">{_svg_esc(row[1].upper())}</text>'
            )
        else:
            task = row[1]
            bg = "#0f172a" if i % 2 == 0 else "#111827"
            p.append(f'<rect x="0" y="{y}" width="{width}" height="{row_h}" fill="{bg}"/>')
            code = (task.activity_code or "")[:10]
            name_text = (task.name or "")[:40]
            label = f"{code}  {name_text}" if code else name_text
            p.append(
                f'<text x="6" y="{y + row_h // 2 + 4}" fill="#cbd5e1" font-size="9"'
                f' clip-path="url(#lcp)">{_svg_esc(label)}</text>'
            )
            if not task.start_date or not task.end_date:
                continue
            bar_x = LEFT_W + (task.start_date - proj_start).days * ppd
            bar_w = max(3.0, (task.end_date - task.start_date).days * ppd)
            if task.activity_code:
                pos_map[task.activity_code] = (bar_x, bar_w, i)
                crit_map[task.activity_code] = bool(task.is_critical)
            color = _STATUS_BAR_COLOR.get(task.status or "", "#1e3a5f")
            crit = ' stroke="#ef4444" stroke-width="1.5"' if task.is_critical else ""
            p.append(
                f'<rect x="{bar_x:.1f}" y="{y + 5}" width="{bar_w:.1f}" height="10"'
                f' fill="{color}" rx="2"{crit}/>'
            )
            if task.actual_start:
                act_end = task.actual_end or today
                act_x = LEFT_W + (task.actual_start - proj_start).days * ppd
                act_w = max(3.0, (act_end - task.actual_start).days * ppd)
                p.append(
                    f'<rect x="{act_x:.1f}" y="{y + 17}" width="{act_w:.1f}" height="5"'
                    ' fill="#22c55e" rx="1" opacity="0.8"/>'
                )

    if deps and pos_map:
        for dep in deps[:200]:
            pred_code = dep.get("pred", "")
            succ_code = dep.get("succ", "")
            if pred_code not in pos_map or succ_code not in pos_map:
                continue
            pbx, pbw, pi = pos_map[pred_code]
            sbx, _sbw, si = pos_map[succ_code]
            is_crit = crit_map.get(pred_code, False) and crit_map.get(succ_code, False)
            color = "#ef4444" if is_crit else "#475569"
            sw = "1.5" if is_crit else "1"
            marker = "url(#ar-c)" if is_crit else "url(#ar-d)"
            px_coord = pbx + pbw
            py_coord = HDR_H + pi * row_h + row_h / 2
            sx_coord = sbx - 2
            sy_coord = HDR_H + si * row_h + row_h / 2
            ex = max(px_coord, sx_coord) + 8
            if abs(py_coord - sy_coord) < 1:
                d = f"M {px_coord:.1f} {py_coord:.1f} H {sx_coord:.1f}"
            else:
                d = f"M {px_coord:.1f} {py_coord:.1f} H {ex:.1f} V {sy_coord:.1f} H {sx_coord:.1f}"
            p.append(
                f'<path d="{d}" stroke="{color}" stroke-width="{sw}"'
                f' fill="none" marker-end="{marker}" opacity="0.7"/>'
            )

    p.append("</svg>")
    return "".join(p)


def generate_scurve_svg(series: dict, width: int = 780, height: int = 260) -> str:
    """Return a self-contained S-curve SVG from EVM series data."""
    pad_l, pad_r, pad_t, pad_b = 46, 16, 20, 36
    chart_w = width - pad_l - pad_r
    chart_h = height - pad_t - pad_b

    pv_pts = series.get("pv", [])
    ev_pts = series.get("ev", [])
    ac_pts = series.get("ac", [])

    if not pv_pts:
        return (
            f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg"'
            ' style="background:#1e293b;border-radius:6px;">'
            '<text x="12" y="30" fill="#94a3b8" font-size="12">No EVM data available.</text>'
            "</svg>"
        )

    all_dates = sorted({p["date"] for lst in [pv_pts, ev_pts, ac_pts] for p in lst})
    d_start = date.fromisoformat(all_dates[0])
    d_end = date.fromisoformat(all_dates[-1])
    total_days = max((d_end - d_start).days, 1)

    def to_xy(pts: list) -> list:
        return [
            (
                pad_l + (date.fromisoformat(p["date"]) - d_start).days / total_days * chart_w,
                pad_t + chart_h - min(p["pct"], 100) / 100 * chart_h,
            )
            for p in pts
        ]

    def polyline(pts: list, color: str, dashed: bool = False) -> str:
        if not pts:
            return ""
        coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        dash = ' stroke-dasharray="6,3"' if dashed else ""
        return (
            f'<polyline points="{coords}" fill="none" stroke="{color}"'
            f' stroke-width="2"{dash} stroke-linejoin="round" stroke-linecap="round"/>'
        )

    p: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"'
        ' style="font-family:Arial,sans-serif;background:#1e293b;border-radius:6px;">'
    ]

    for pct in (0, 25, 50, 75, 100):
        gy = pad_t + chart_h - pct / 100 * chart_h
        p.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + chart_w}" y2="{gy:.1f}"'
            ' stroke="#334155" stroke-width="1"/>'
        )
        p.append(
            f'<text x="{pad_l - 4}" y="{gy + 4:.1f}" fill="#64748b"'
            f' font-size="9" text-anchor="end">{pct}%</text>'
        )

    for days_off, lbl in _month_ticks(d_start, d_end):
        tx = pad_l + days_off / total_days * chart_w
        p.append(
            f'<line x1="{tx:.1f}" y1="{pad_t}" x2="{tx:.1f}" y2="{pad_t + chart_h}"'
            ' stroke="#1e293b" stroke-width="1"/>'
        )
        p.append(
            f'<text x="{tx:.1f}" y="{height - 4}" fill="#64748b"'
            f' font-size="8" text-anchor="middle">{_svg_esc(lbl)}</text>'
        )

    today = date.today()
    if d_start <= today <= d_end:
        tx = pad_l + (today - d_start).days / total_days * chart_w
        p.append(
            f'<line x1="{tx:.1f}" y1="{pad_t}" x2="{tx:.1f}" y2="{pad_t + chart_h}"'
            ' stroke="#ef4444" stroke-width="1" stroke-dasharray="3,3"/>'
        )

    p.append(polyline(to_xy(pv_pts), "#3b82f6", dashed=True))
    p.append(polyline(to_xy(ev_pts), "#22c55e"))
    p.append(polyline(to_xy(ac_pts), "#f59e0b"))

    for i, (color, lbl) in enumerate(
        [("#3b82f6", "Planned Value"), ("#22c55e", "Earned Value"), ("#f59e0b", "Actual Cost")]
    ):
        lx = pad_l + i * 130
        p.append(f'<rect x="{lx}" y="{height - 13}" width="14" height="4" fill="{color}" rx="1"/>')
        p.append(f'<text x="{lx + 18}" y="{height - 6}" fill="#94a3b8" font-size="9">{lbl}</text>')

    p.append("</svg>")
    return "".join(p)


class ExportReportView(ProjectAccessMixin, View):
    """GET -- generate and download a standalone HTML stakeholder report."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.models import Task, TaskDependency
        from scheduling.services.evm import compute_evm, compute_wbs_heatmap

        project = self.get_project()
        tasks = list(
            Task.objects.filter(project=project, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
            .order_by("stage", "start_date")
        )

        task_pks = {t.pk for t in tasks}
        raw_deps = TaskDependency.objects.filter(
            predecessor__project=project, successor__project=project
        ).select_related("predecessor", "successor")
        deps = [
            {"pred": d.predecessor.activity_code, "succ": d.successor.activity_code}
            for d in raw_deps
            if d.predecessor.pk in task_pks
            and d.successor.pk in task_pks
            and d.predecessor.activity_code
            and d.successor.activity_code
        ]

        evm = compute_evm(str(project.pk))
        wbs_stages = compute_wbs_heatmap(str(project.pk))

        gantt_svg = generate_gantt_svg(tasks, deps)
        scurve_svg = generate_scurve_svg(evm.get("series", {}) if evm.get("has_data") else {})

        total = len(tasks)
        completed = sum(1 for t in tasks if t.status == "complete")
        pct_complete = round(completed / total * 100, 1) if total else 0.0

        risk_tasks = sorted(
            [t for t in tasks if t.status != "complete"],
            key=lambda t: t.total_float if t.total_float is not None else 9999,
        )[:10]

        ctx = {
            "project": project,
            "export_date": date.today().isoformat(),
            "total_tasks": total,
            "pct_complete": pct_complete,
            "evm": evm,
            "wbs_stages": wbs_stages,
            "risk_tasks": risk_tasks,
            "gantt_svg": gantt_svg,
            "scurve_svg": scurve_svg,
        }

        html = render_to_string("ifc_viewer/export_report.html", ctx, request=request)
        safe_name = "".join(c if c.isalnum() or c in "- _" else "_" for c in project.name)
        resp = HttpResponse(html, content_type="text/html; charset=utf-8")
        resp["Content-Disposition"] = (
            f'attachment; filename="{safe_name}-report-{date.today()}.html"'
        )
        return resp
