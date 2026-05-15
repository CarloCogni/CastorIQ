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
from django.urls import reverse
from ifc_processor.models import IFCEntity, IFCFile

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
from .services.metrics import breakdown_data, entity_metrics, non_physical_metrics, schedule_progress_metrics

logger = logging.getLogger(__name__)

_BLANK_CHECKS = {
    "issues": [],
    "total_elements": 0,
    "total_issues": 0,
    "severity_counts": {"critical": 0, "warning": 0, "info": 0},
}


def _available_stages(project) -> list[tuple[str, str]]:
    """Return [(key, label)] for stages that have at least one physical task in this project."""
    try:
        from islam.scheduling.models import Task

        present = set(
            Task.objects.filter(project=project, is_non_physical=False)
            .exclude(stage="")
            .values_list("stage", flat=True)
            .distinct()
        )
        return [(k, v) for k, v in Task.Stage.choices if k in present]
    except Exception:
        return []


def _available_sub_stages(project, stage: str = "") -> list[tuple[str, str]]:
    """Return [(key, label)] for sub_stages with ≥1 task in this project.

    If stage is given, restricts to sub_stages whose parent is that stage.
    """
    try:
        from islam.scheduling.models import Task

        qs = Task.objects.filter(project=project, is_non_physical=False).exclude(sub_stage="")
        if stage:
            qs = qs.filter(stage=stage)
        present = set(qs.values_list("sub_stage", flat=True).distinct())
        return [(k, v) for k, v in Task.SubStage.choices if k in present]
    except Exception:
        return []


def _build_ctx(project, ifc_file) -> dict:
    """Build the full panel context from DB metrics + IFC checks."""
    ctx: dict = {"project": project, "ifc_file": ifc_file}

    ctx.update(schedule_progress_metrics(project))
    ctx.update(non_physical_metrics(project))
    ctx["available_stages"] = _available_stages(project)
    ctx["available_sub_stages"] = _available_sub_stages(project)

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
# Progress mode
# ---------------------------------------------------------------------------


class ProgressModeView(ProjectAccessMixin, View):
    """HTMX POST — persist the schedule progress mode, return the updated ring card."""

    _VALID_MODES = {"count", "cost", "duration", "weight"}

    def post(self, request, **kwargs: object) -> HttpResponse:
        from islam.scheduling.models import IslamProgressMode

        project = self.get_project()
        mode = request.POST.get("mode", "count")
        if mode not in self._VALID_MODES:
            mode = "count"

        obj, _ = IslamProgressMode.objects.get_or_create(project=project)
        if obj.mode != mode:
            obj.mode = mode
            obj.save(update_fields=["mode"])

        stage = request.POST.get("stage", "")
        sub_stage = request.POST.get("sub_stage", "")
        metrics = schedule_progress_metrics(project, stage=stage, sub_stage=sub_stage)
        metrics["available_stages"] = _available_stages(project)
        metrics["available_sub_stages"] = _available_sub_stages(project, stage=stage)
        return render(
            request,
            "ifc_insights/components/progress_ring.html",
            {"project": project, **metrics},
        )


class ProgressRingView(ProjectAccessMixin, View):
    """HTMX GET — return progress ring filtered by construction stage / sub-stage."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        stage = request.GET.get("stage", "")
        sub_stage = request.GET.get("sub_stage", "")
        metrics = schedule_progress_metrics(project, stage=stage, sub_stage=sub_stage)
        metrics["available_stages"] = _available_stages(project)
        metrics["available_sub_stages"] = _available_sub_stages(project, stage=stage)
        return render(
            request,
            "ifc_insights/components/progress_ring.html",
            {"project": project, **metrics},
        )


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


# ---------------------------------------------------------------------------
# IFC Issues — module-level helpers (used by both HTMX views and export)
# ---------------------------------------------------------------------------

_ISSUES_ONLY = ("global_id", "ifc_type", "name", "spatial_container_id", "properties")


def _has_activity_id(props: dict) -> bool:
    """Return True if props contains a non-empty Activity ID value."""
    return any(k.lower().endswith("activity id") and v for k, v in props.items())


def _has_cost(props: dict) -> bool:
    """Return True if props contains a parseable Cost or Unit Cost value."""
    for k, v in props.items():
        if k.lower().endswith("cost") and v:
            try:
                float(str(v).replace(",", ""))
                return True
            except (TypeError, ValueError):
                pass
    return False


def _entity_level(entity) -> str:
    """Return storey name from spatial_container chain, or '—'."""
    try:
        return entity.spatial_container.entity.name or "—"
    except AttributeError:
        return "—"


def _missing_activity_rows(ifc_file) -> list[dict]:
    """Return dicts for all entities that have no valid Activity ID."""
    rows: list[dict] = []
    qs = (
        IFCEntity.objects.filter(ifc_file=ifc_file)
        .only(*_ISSUES_ONLY)
        .select_related("spatial_container__entity")
    )
    for entity in qs.iterator(chunk_size=500):
        if not _has_activity_id(entity.properties or {}):
            rows.append({
                "global_id": entity.global_id,
                "ifc_type": entity.ifc_type or "—",
                "name": entity.name or "—",
                "level": _entity_level(entity),
            })
    return rows


def _missing_cost_rows(ifc_file) -> list[dict]:
    """Return dicts for all entities that have no valid Cost property."""
    rows: list[dict] = []
    qs = (
        IFCEntity.objects.filter(ifc_file=ifc_file)
        .only(*_ISSUES_ONLY)
        .select_related("spatial_container__entity")
    )
    for entity in qs.iterator(chunk_size=500):
        if not _has_cost(entity.properties or {}):
            rows.append({
                "global_id": entity.global_id,
                "ifc_type": entity.ifc_type or "—",
                "name": entity.name or "—",
                "level": _entity_level(entity),
            })
    return rows


def _activity_audit_rows(ifc_file, project) -> list[dict]:
    """Group entities by (Activity ID, Activity Name, IFC Type) and check schedule binding."""
    groups: dict = {}
    for entity in (
        IFCEntity.objects.filter(ifc_file=ifc_file)
        .only("global_id", "ifc_type", "properties")
        .iterator(chunk_size=500)
    ):
        props = entity.properties or {}
        act_id = act_name = None
        for k, v in props.items():
            kl = k.lower()
            if kl.endswith("activity id") and v:
                act_id = str(v).strip()
            elif kl.endswith("activity name") and v:
                act_name = str(v).strip()
        if not act_id:
            continue
        key = (act_id, act_name or "—", entity.ifc_type or "Unknown")
        entry = groups.setdefault(key, {"count": 0, "global_ids": set()})
        entry["count"] += 1
        entry["global_ids"].add(entity.global_id)

    try:
        from islam.scheduling.models import TaskEntityBinding  # local — avoids circular
        bound = set(
            TaskEntityBinding.objects.filter(task__project=project)
            .values_list("entity_global_id", flat=True)
        )
    except ImportError:
        bound = set()

    return [
        {
            "act_id": act_id,
            "act_name": act_name,
            "ifc_type": ifc_type,
            "count": data["count"],
            "linked": bool(data["global_ids"] & bound),
        }
        for (act_id, act_name, ifc_type), data in sorted(
            groups.items(), key=lambda x: -x[1]["count"]
        )
    ]


# ---------------------------------------------------------------------------
# IFC Issues tab views
# ---------------------------------------------------------------------------


class IssuesView(ProjectTabMixin, TemplateView):
    """IFC Issues tab — IFC Health Checks (Section 0) + 4 lazy-loaded HTMX audit sections."""

    active_tab = "islam"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["islam_subtab"] = "ifc_issues"
        project = ctx["project"]
        ifc_file = _get_active_ifc_file(project)
        ctx.update(_build_ctx(project, ifc_file))
        return ctx


class IssuesCountView(ProjectAccessMixin, View):
    """JSON endpoint — total missing-activity + missing-cost count for badge."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        ifc_file = _get_active_ifc_file(project)
        if not ifc_file:
            return JsonResponse({"total": 0})
        missing_4d = missing_5d = 0
        for entity in (
            IFCEntity.objects.filter(ifc_file=ifc_file)
            .only("properties")
            .iterator(chunk_size=500)
        ):
            props = entity.properties or {}
            if not _has_activity_id(props):
                missing_4d += 1
            if not _has_cost(props):
                missing_5d += 1
        return JsonResponse({"total": missing_4d + missing_5d})


class IssuesMissingActivityView(ProjectAccessMixin, View):
    """HTMX partial — table of entities missing Activity ID."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_file = _get_active_ifc_file(project)
        rows = _missing_activity_rows(ifc_file) if ifc_file else []
        viewer_url = reverse("islam:viewer", kwargs={"pk": project.pk})
        return render(
            request,
            "ifc_insights/components/issues_missing_activity.html",
            {
                "rows": rows[:500],
                "total_count": len(rows),
                "project": project,
                "viewer_url": viewer_url,
            },
        )


class IssuesMissingCostView(ProjectAccessMixin, View):
    """HTMX partial — table of entities missing Cost data."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_file = _get_active_ifc_file(project)
        rows = _missing_cost_rows(ifc_file) if ifc_file else []
        viewer_url = reverse("islam:viewer", kwargs={"pk": project.pk})
        return render(
            request,
            "ifc_insights/components/issues_missing_cost.html",
            {
                "rows": rows[:500],
                "total_count": len(rows),
                "project": project,
                "viewer_url": viewer_url,
            },
        )


class IssuesActivityAuditView(ProjectAccessMixin, View):
    """HTMX partial — Activity ID / Name audit grouped table."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_file = _get_active_ifc_file(project)
        audit_rows = _activity_audit_rows(ifc_file, project) if ifc_file else []
        return render(
            request,
            "ifc_insights/components/issues_activity_audit.html",
            {"audit_rows": audit_rows, "project": project},
        )


class IssuesLevelsHealthView(ProjectAccessMixin, View):
    """HTMX partial — storey health (element count + schedule alignment)."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_file = _get_active_ifc_file(project)
        storey_rows: list[dict] = []
        if ifc_file:
            from .services.levels import get_storeys_from_db, match_storeys_to_tasks
            all_storeys = get_storeys_from_db(ifc_file)
            in_schedule_map = match_storeys_to_tasks(
                [s["name"] for s in all_storeys], project
            )
            for s in all_storeys:
                in_sched = in_schedule_map.get(s["name"].lower(), False)
                if s["element_count"] > 0 and in_sched:
                    status = "ok"
                elif s["element_count"] > 0:
                    status = "no_schedule"
                else:
                    status = "no_elements"
                storey_rows.append({**s, "in_schedule": in_sched, "status": status})

        levels_url = reverse("islam:levels", kwargs={"pk": project.pk})
        return render(
            request,
            "ifc_insights/components/issues_levels_health.html",
            {"storey_rows": storey_rows, "project": project, "levels_url": levels_url},
        )


class IssuesExportView(ProjectAccessMixin, View):
    """CSV download for a named issue section."""

    _SECTION_FIELDS: dict[str, list[str]] = {
        "missing_activity": ["global_id", "ifc_type", "name", "level"],
        "missing_cost": ["global_id", "ifc_type", "name", "level"],
        "activity_audit": ["activity_id", "activity_name", "ifc_type", "count", "linked_to_task"],
    }

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        section = request.GET.get("section", "")
        if section not in self._SECTION_FIELDS:
            return HttpResponse("Unknown section.", status=400)

        ifc_file = _get_active_ifc_file(project)
        if not ifc_file:
            return HttpResponse("No IFC file found.", status=404)

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=self._SECTION_FIELDS[section])
        writer.writeheader()

        if section == "missing_activity":
            for row in _missing_activity_rows(ifc_file):
                writer.writerow(row)
        elif section == "missing_cost":
            for row in _missing_cost_rows(ifc_file):
                writer.writerow(row)
        elif section == "activity_audit":
            for row in _activity_audit_rows(ifc_file, project):
                writer.writerow({
                    "activity_id": row["act_id"],
                    "activity_name": row["act_name"],
                    "ifc_type": row["ifc_type"],
                    "count": row["count"],
                    "linked_to_task": "Yes" if row["linked"] else "No",
                })

        safe_name = "".join(
            c if c.isalnum() or c in "-_ " else "_" for c in project.name
        )
        response = HttpResponse(buf.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="ifc_issues_{section}_{safe_name}.csv"'
        )
        return response
