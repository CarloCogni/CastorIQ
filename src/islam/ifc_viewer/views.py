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
            lvl.name: lvl.z_elevation
            for lvl in IslamLevel.objects.filter(project=project)
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


class TimelineView(ProjectAccessMixin, View):
    """JSON — weekly timeline buckets of active/complete entity global_ids from Task schedule."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()

        from islam.scheduling.models import Task

        tasks = list(
            Task.objects.filter(project=project)
            .prefetch_related("ifc_entities")
            .exclude(start_date=None)
            .exclude(end_date=None)
        )

        if not tasks:
            return JsonResponse({"has_tasks": False, "weeks": []})

        min_date = min(t.start_date for t in tasks)
        max_date = max(t.end_date for t in tasks)

        weeks = []
        current = min_date
        while current <= max_date:
            week_end = current + timedelta(days=6)
            active: list[str] = []
            complete: list[str] = []
            for task in tasks:
                ids = list(task.ifc_entities.values_list("global_id", flat=True))
                if not ids:
                    continue
                if task.end_date < current:
                    complete.extend(ids)
                elif task.start_date <= week_end:
                    active.extend(ids)
            weeks.append({
                "week_start": current.isoformat(),
                "active": active,
                "complete": complete,
            })
            current += timedelta(weeks=1)

        return JsonResponse({
            "has_tasks": True,
            "min_date": min_date.isoformat(),
            "max_date": max_date.isoformat(),
            "weeks": weeks,
        })
