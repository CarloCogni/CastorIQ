# islam/ifc_insights/views.py
"""IFC Insights tab views — QA/QC checks dashboard + entity metrics + Level Panel."""

from __future__ import annotations

import csv
import io
import logging

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views import View
from django.views.generic import TemplateView

from core.http import toast_response, trigger_toast
from core.mixins import ProjectAccessMixin, ProjectTabMixin
from ifc_processor.models import IFCFile

from .models import IslamLevel
from .services.checks import run_all_checks
from .services.levels import apply_levels_to_ifc, extract_levels_from_ifc, suggest_levels_from_entities
from .services.metrics import breakdown_data, entity_metrics

logger = logging.getLogger(__name__)

_BLANK_CHECKS = {
    "issues": [],
    "total_elements": 0,
    "total_issues": 0,
    "severity_counts": {"critical": 0, "warning": 0, "info": 0},
}


def _build_ctx(project, ifc_file) -> dict:
    """Build the full panel context from DB metrics + IFC checks."""
    ctx: dict = {"project": project, "ifc_file": ifc_file}

    if not ifc_file:
        return ctx

    ctx.update(entity_metrics(ifc_file))

    if ifc_file.file.name:
        try:
            ctx["check_results"] = run_all_checks(ifc_file.file.path)
        except Exception as exc:
            logger.error("IFC checks failed for project %s: %s", project.pk, exc)
            ctx["check_results"] = {"error": str(exc), **_BLANK_CHECKS}

    return ctx


class InsightsView(ProjectTabMixin, TemplateView):
    """Main IFC Insights panel — readiness dashboard + QA/QC checks."""

    active_tab = "islam"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        project = ctx["project"]
        ctx["islam_subtab"] = "insights"

        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        ctx.update(_build_ctx(project, ifc_file))
        return ctx


class InsightsRerunView(ProjectAccessMixin, View):
    """HTMX endpoint — re-runs checks and returns the updated panel partial."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        ctx = _build_ctx(project, ifc_file)
        return render(request, "ifc_insights/panel.html", ctx)


class InsightsBreakdownView(ProjectAccessMixin, View):
    """HTMX endpoint — mini bar-chart card for level / element_type / material."""

    _VALID = {"level", "element_type", "material"}

    def get(self, request, breakdown_type: str, **kwargs: object) -> HttpResponse:
        if breakdown_type not in self._VALID:
            return HttpResponse(status=400)

        project = self.get_project()
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file:
            return render(
                request,
                "ifc_insights/components/breakdown_card.html",
                {"title": breakdown_type, "rows": []},
            )

        data = breakdown_data(ifc_file, breakdown_type)
        data["project"] = project
        return render(request, "ifc_insights/components/breakdown_card.html", data)


class InsightsExportView(ProjectAccessMixin, View):
    """Download all QA/QC issues as CSV."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()

        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file:
            return HttpResponse("No processed IFC file found for this project.", status=404)

        try:
            results = run_all_checks(ifc_file.file.path)
        except Exception as exc:
            return HttpResponse(f"Check failed: {exc}", status=500)

        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=["global_id", "element_type", "issue", "severity", "suggested_fix"],
        )
        writer.writeheader()
        for issue in results.get("issues", []):
            writer.writerow(issue)

        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in project.name)
        response = HttpResponse(buf.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="ifc_insights_{safe_name}.csv"'
        return response


# ---------------------------------------------------------------------------
# Level Panel views
# ---------------------------------------------------------------------------


class LevelsView(ProjectTabMixin, TemplateView):
    """Level Panel main page — list + manage project floor levels."""

    active_tab = "islam"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["islam_subtab"] = "levels"
        ctx["levels"] = IslamLevel.objects.filter(project=ctx["project"])
        ctx["ifc_file"] = (
            IFCFile.objects.filter(project=ctx["project"], status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        return ctx


class LevelSuggestView(ProjectAccessMixin, View):
    """HTMX — extract IfcBuildingStorey objects from IFC and return suggestion rows."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file:
            return render(request, "ifc_insights/components/level_suggest_results.html", {"suggestions": [], "project": project})

        suggestions = extract_levels_from_ifc(ifc_file.file.path)
        if not suggestions:
            suggestions = suggest_levels_from_entities(ifc_file)

        return render(
            request,
            "ifc_insights/components/level_suggest_results.html",
            {"suggestions": suggestions, "project": project},
        )


class LevelAddView(ProjectAccessMixin, View):
    """HTMX POST — create a new IslamLevel and return updated row."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        name = request.POST.get("name", "").strip()
        z_raw = request.POST.get("z_elevation", "").strip()

        if not name:
            return toast_response("Level name is required.", "error", status=400)
        try:
            z = float(z_raw)
        except ValueError:
            return toast_response("Z elevation must be a number.", "error", status=400)

        level = IslamLevel.objects.create(
            project=project,
            name=name,
            z_elevation=z,
            source=IslamLevel.Source.MANUAL,
        )
        response = render(request, "ifc_insights/components/level_row.html", {"level": level, "project": project})
        return trigger_toast(response, f"Level '{level.name}' added.")


class LevelEditView(ProjectAccessMixin, View):
    """HTMX POST — update name/z_elevation on an existing level."""

    def post(self, request, level_pk, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        level = get_object_or_404(IslamLevel, pk=level_pk, project=project)

        name = request.POST.get("name", "").strip()
        z_raw = request.POST.get("z_elevation", "").strip()

        if not name:
            return toast_response("Level name is required.", "error", status=400)
        try:
            z = float(z_raw)
        except ValueError:
            return toast_response("Z elevation must be a number.", "error", status=400)

        level.name = name
        level.z_elevation = z
        level.save(update_fields=["name", "z_elevation"])

        response = render(request, "ifc_insights/components/level_row.html", {"level": level, "project": project})
        return trigger_toast(response, "Level updated.")


class LevelDeleteView(ProjectAccessMixin, View):
    """HTMX DELETE — remove a level record; returns empty 200 so HTMX removes the row."""

    def delete(self, request, level_pk, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        level = get_object_or_404(IslamLevel, pk=level_pk, project=project)
        name = level.name
        level.delete()
        response = HttpResponse("")
        return trigger_toast(response, f"Level '{name}' deleted.")


class LevelApplyView(ProjectAccessMixin, View):
    """POST — write all project levels back to the IFC file."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file:
            return toast_response("No processed IFC file found.", "error", status=404)

        levels = list(IslamLevel.objects.filter(project=project))
        if not levels:
            return toast_response("No levels to apply.", "error", status=400)

        result = apply_levels_to_ifc(ifc_file.file.path, levels, ifc_file)

        if result["errors"]:
            logger.warning("Level apply errors for project %s: %s", project.pk, result["errors"])

        msg = f"Applied: {result['updated']} updated, {result['created']} created."
        if result["commit_hash"]:
            msg += f" Commit: {result['commit_hash'][:8]}."
        if result["errors"]:
            msg += f" {len(result['errors'])} error(s) — check logs."

        return toast_response(msg, "success" if not result["errors"] else "info")
