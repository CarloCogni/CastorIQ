# islam/ifc_viewer/views.py
"""Castor Simulator tab views — 3D viewer, fragment cache, colormap, gap analysis, timeline."""

from __future__ import annotations

import csv
import io
import logging
import os
from datetime import timedelta

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views import View
from django.views.generic import TemplateView

from core.mixins import ProjectAccessMixin, ProjectTabMixin
from ifc_processor.models import IFCFile

from .services.colormap import build_colormap
from .services.gap_analysis import build_gap_analysis

logger = logging.getLogger(__name__)


class ViewerView(ProjectTabMixin, TemplateView):
    """Renders the 3D IFC viewer partial."""

    active_tab = "islam"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        project = ctx["project"]
        ctx["islam_subtab"] = "viewer"

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

        return JsonResponse(build_colormap(ifc_file, by))


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

        rows = build_gap_analysis(ifc_file, by)

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
        from islam.ifc_insights.models import IslamLevel

        level_z: dict[str, float] = {
            lvl.name: lvl.z_elevation for lvl in IslamLevel.objects.filter(project=project)
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

        from islam.scheduling.models import TaskEntityBinding

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
                "properties": filtered_props,
                "linked_tasks": linked_tasks,
            }
        )


class TimelineView(ProjectAccessMixin, View):
    """JSON — weekly 4D timeline intervals with 3-state entity buckets and stats."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from collections import defaultdict

        from ifc_processor.models import IFCEntity as IFCEntityModel
        from islam.scheduling.models import Task

        project = self.get_project()

        tasks = list(
            Task.objects.filter(project=project, is_non_physical=False)
            .prefetch_related("ifc_entities")
            .exclude(start_date=None)
            .exclude(end_date=None)
        )

        if not tasks:
            return JsonResponse({"has_tasks": False, "intervals": []})

        # entity GID → [(start_date, end_date), ...] — uses prefetch cache
        entity_tasks: dict[str, list] = defaultdict(list)
        for task in tasks:
            for entity in task.ifc_entities.all():
                entity_tasks[entity.global_id].append((task.start_date, task.end_date))

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

        intervals = []
        current = min_date
        week_num = 1
        while current <= max_date:
            not_started: list[str] = []
            in_progress: list[str] = []
            complete: list[str] = []

            for gid in task_gids:
                ranges = entity_tasks[gid]
                # complete: all linked tasks ended at or before T
                if all(end <= current for _, end in ranges):
                    complete.append(gid)
                # in_progress: at least one task started but not yet ended
                elif any(start <= current < end for start, end in ranges):
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
                    },
                    "stats": {
                        "total": total,
                        "complete": len(complete),
                        "in_progress": len(in_progress),
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
