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
from .services.levels import (
    apply_levels_to_ifc,
    extract_levels_from_ifc,
    get_missing_level_hints,
    get_reference_points,
    get_storeys_from_db,
    match_storeys_to_tasks,
    suggest_levels_from_entities,
)
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


def _get_active_ifc_file(project):
    """Return the most recent completed IFCFile for a project, or None."""
    return (
        IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
        .order_by("-created_at")
        .first()
    )


def _storey_registry_ctx(project) -> dict:
    """Build context for storey_registry_body.html — used by multiple views."""
    ifc_file = _get_active_ifc_file(project)
    if not ifc_file:
        return {"storeys": [], "project": project}

    all_storeys = get_storeys_from_db(ifc_file)
    imported_map = {
        lv.ifc_storey_global_id: lv
        for lv in IslamLevel.objects.filter(
            project=project, ifc_storey_global_id__isnull=False
        )
        if lv.ifc_storey_global_id
    }
    storey_names = [s["name"] for s in all_storeys]
    in_schedule_map = match_storeys_to_tasks(storey_names, project)

    for s in all_storeys:
        s["linked_level"] = imported_map.get(s["global_id"])
        s["in_schedule"] = in_schedule_map.get(s["name"].lower(), False)

    return {"storeys": all_storeys, "project": project}


# ---------------------------------------------------------------------------
# Insights panel views
# ---------------------------------------------------------------------------


class InsightsView(ProjectTabMixin, TemplateView):
    """Main IFC Insights panel — readiness dashboard + QA/QC checks."""

    active_tab = "islam"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        project = ctx["project"]
        ctx["islam_subtab"] = "insights"

        ifc_file = _get_active_ifc_file(project)
        ctx.update(_build_ctx(project, ifc_file))
        return ctx


class InsightsRerunView(ProjectAccessMixin, View):
    """HTMX endpoint — re-runs checks and returns the updated panel partial."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_file = _get_active_ifc_file(project)
        ctx = _build_ctx(project, ifc_file)
        return render(request, "ifc_insights/panel.html", ctx)


class InsightsBreakdownView(ProjectAccessMixin, View):
    """HTMX endpoint — mini bar-chart card for level / element_type / material."""

    _VALID = {"level", "element_type", "material"}

    def get(self, request, breakdown_type: str, **kwargs: object) -> HttpResponse:
        if breakdown_type not in self._VALID:
            return HttpResponse(status=400)

        project = self.get_project()
        ifc_file = _get_active_ifc_file(project)
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
        ifc_file = _get_active_ifc_file(project)
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
    """Level Panel — IFC storey registry, reference points, schedule alignment."""

    active_tab = "islam"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["islam_subtab"] = "levels"
        project = ctx["project"]

        ifc_file = _get_active_ifc_file(project)
        ctx["ifc_file"] = ifc_file

        # Section 4: manual levels (no IFC storey link)
        ctx["manual_levels"] = IslamLevel.objects.filter(
            project=project, ifc_storey_global_id__isnull=True
        )

        if not ifc_file:
            ctx.update({
                "all_storeys": [],
                "reference_points": {},
                "missing_hints": [],
                "apply_diff": {"will_update": 0, "will_create": 0},
            })
            return ctx

        # Section 1: reference points
        ctx["reference_points"] = get_reference_points(ifc_file)

        # Section 2: storey registry with import status + in-schedule flag
        all_storeys = get_storeys_from_db(ifc_file)
        imported_map = {
            lv.ifc_storey_global_id: lv
            for lv in IslamLevel.objects.filter(
                project=project, ifc_storey_global_id__isnull=False
            )
            if lv.ifc_storey_global_id
        }
        in_schedule_map = match_storeys_to_tasks([s["name"] for s in all_storeys], project)
        for s in all_storeys:
            s["linked_level"] = imported_map.get(s["global_id"])
            s["in_schedule"] = in_schedule_map.get(s["name"].lower(), False)
        ctx["all_storeys"] = all_storeys

        # Section 3: level keywords in tasks not matched by any IFC storey
        storey_names_lower = {s["name"].lower() for s in all_storeys}
        ctx["missing_hints"] = get_missing_level_hints(storey_names_lower, project)

        # Section 5: apply diff
        ctx["apply_diff"] = {
            "will_update": len(imported_map),
            "will_create": ctx["manual_levels"].count(),
        }

        return ctx


class LevelSuggestView(ProjectAccessMixin, View):
    """HTMX GET — refresh the storey registry tbody from the DB."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ctx = _storey_registry_ctx(project)
        return render(request, "ifc_insights/components/storey_registry_body.html", ctx)


class LevelAddView(ProjectAccessMixin, View):
    """HTMX POST — create or import an IslamLevel.

    When ifc_storey_global_id is supplied: import IFC storey (source='ifc'),
    return the full storey registry body for #storey-registry-body swap.

    Without ifc_storey_global_id: create manual level, return single row
    for #manual-levels-tbody beforeend swap.
    """

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        name = request.POST.get("name", "").strip()
        z_raw = request.POST.get("z_elevation", "").strip()
        gid = request.POST.get("ifc_storey_global_id", "").strip()

        if not name:
            return toast_response("Level name is required.", "error", status=400)
        try:
            z = float(z_raw)
        except ValueError:
            return toast_response("Z elevation must be a number.", "error", status=400)

        if gid:
            level, created = IslamLevel.objects.get_or_create(
                project=project,
                ifc_storey_global_id=gid,
                defaults={"name": name, "z_elevation": z, "source": IslamLevel.Source.IFC},
            )
            if not created:
                level.name = name
                level.z_elevation = z
                level.save(update_fields=["name", "z_elevation"])

            ctx = _storey_registry_ctx(project)
            response = render(request, "ifc_insights/components/storey_registry_body.html", ctx)
            verb = "imported" if created else "updated"
            return trigger_toast(response, f"'{level.name}' {verb} in Level Registry.")

        # Manual add — append single row to manual levels table
        level = IslamLevel.objects.create(
            project=project,
            name=name,
            z_elevation=z,
            source=IslamLevel.Source.MANUAL,
        )
        response = render(request, "ifc_insights/components/level_row.html", {"level": level, "project": project})
        return trigger_toast(response, f"Level '{level.name}' added.")


class LevelEditView(ProjectAccessMixin, View):
    """HTMX POST — update name/z_elevation on an existing IslamLevel.

    When refresh_registry=1 is in POST: return full storey registry body
    (used when editing a storey-linked level in Section 2).
    Otherwise: return single level_row.html (Section 4 manual levels).
    """

    def post(self, request, level_pk, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        level = get_object_or_404(IslamLevel, pk=level_pk, project=project)

        name = request.POST.get("name", "").strip()
        z_raw = request.POST.get("z_elevation", "").strip()
        refresh_registry = request.POST.get("refresh_registry", "").strip()

        if not name:
            return toast_response("Level name is required.", "error", status=400)
        try:
            z = float(z_raw)
        except ValueError:
            return toast_response("Z elevation must be a number.", "error", status=400)

        level.name = name
        level.z_elevation = z
        level.save(update_fields=["name", "z_elevation"])

        if refresh_registry:
            ctx = _storey_registry_ctx(project)
            response = render(request, "ifc_insights/components/storey_registry_body.html", ctx)
        else:
            response = render(request, "ifc_insights/components/level_row.html", {"level": level, "project": project})
        return trigger_toast(response, "Level updated.")


class LevelDeleteView(ProjectAccessMixin, View):
    """HTMX DELETE — remove a level record.

    When storey_gid + refresh_registry=1 are in query params: the level was
    storey-linked; return storey registry body in unlinked state.
    Otherwise: return empty 200 so HTMX removes the manual level row.
    """

    def delete(self, request, level_pk, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        level = get_object_or_404(IslamLevel, pk=level_pk, project=project)
        name = level.name
        storey_gid = request.GET.get("storey_gid", "").strip()
        refresh_registry = request.GET.get("refresh_registry", "").strip()
        level.delete()

        if storey_gid and refresh_registry:
            ctx = _storey_registry_ctx(project)
            response = render(request, "ifc_insights/components/storey_registry_body.html", ctx)
            return trigger_toast(response, f"'{name}' unlinked from Level Registry.")

        response = HttpResponse("")
        return trigger_toast(response, f"Level '{name}' deleted.")


class LevelApplyView(ProjectAccessMixin, View):
    """POST — write all project IslamLevel records back to the IFC file."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_file = _get_active_ifc_file(project)
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
