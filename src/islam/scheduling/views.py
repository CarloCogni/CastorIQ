# islam/scheduling/views.py
"""4D TimeLiner scheduling views — file upload, linking, Gantt, and simulation."""

from __future__ import annotations

import json
import logging

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views import View
from django.views.generic import TemplateView

from core.http import toast_response, trigger_toast
from core.mixins import ProjectAccessMixin, ProjectModifyAccessMixin, ProjectTabMixin
from ifc_processor.models import IFCEntity, IFCFile

from .models import Task
from .services.excel_parser import parse_excel
from .services.linker import apply_matches, auto_match_tasks, param_match_tasks
from .services.msp_parser import parse_msp
from .services.validator import validate_schedule
from .services.xer_parser import parse_xer

logger = logging.getLogger(__name__)


class ScheduleView(ProjectTabMixin, TemplateView):
    """Main TimeLiner panel — entry point for all scheduling sub-tabs."""

    active_tab = "islam"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        project = ctx["project"]
        ctx["islam_subtab"] = "schedule"
        ctx["schedule_tab"] = self.request.GET.get("tab", "data_sources")

        tasks = Task.objects.filter(project=project).prefetch_related("ifc_entities")
        ctx["tasks"] = tasks
        ctx["task_count"] = tasks.count()

        ifc_files = IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
        ctx["ifc_files_available"] = ifc_files.exists()

        # Gantt + simulate date range
        if tasks.exists():
            from django.db.models import Max, Min
            agg = tasks.aggregate(min_start=Min("start_date"), max_end=Max("end_date"))
            ctx["gantt_min_date"] = agg["min_start"]
            ctx["gantt_max_date"] = agg["max_end"]
        else:
            ctx["gantt_min_date"] = None
            ctx["gantt_max_date"] = None

        return ctx


# ---------------------------------------------------------------------------
# Upload + parse
# ---------------------------------------------------------------------------

class TaskUploadView(ProjectModifyAccessMixin, View):
    """HTMX POST — accept schedule file, parse it, return preview table."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        uploaded = request.FILES.get("schedule_file")
        if not uploaded:
            return toast_response("No file selected.", "error", status=400)

        filename = uploaded.name.lower()
        try:
            if filename.endswith(".xlsx") or filename.endswith(".xls"):
                tasks = parse_excel(uploaded)
                source = "excel"
            elif filename.endswith(".xer"):
                tasks = parse_xer(uploaded)
                source = "xer"
            elif filename.endswith(".xml"):
                tasks = parse_msp(uploaded)
                source = "msp"
            else:
                return toast_response(
                    "Unsupported file type. Upload .xlsx, .xer, or .xml.", "error", status=400
                )
        except ValueError as exc:
            return toast_response(str(exc), "error", status=400)
        except Exception as exc:
            logger.exception("Schedule file parse error for project %s", project.pk)
            return toast_response(f"Parse failed: {exc}", "error", status=500)

        # AI validation (non-blocking)
        validation = validate_schedule(tasks, project_name=project.name)

        # Store parsed tasks in session for the save step
        request.session[f"parsed_tasks_{project.pk}"] = json.dumps(
            [
                {**t, "start_date": str(t["start_date"]), "end_date": str(t["end_date"])}
                for t in tasks
            ]
        )

        return render(
            request,
            "scheduling/components/task_list.html",
            {
                "tasks_preview": tasks,
                "source": source,
                "validation": validation,
                "project": project,
                "preview_mode": True,
            },
        )


class TaskSaveView(ProjectModifyAccessMixin, View):
    """HTMX POST — persist parsed tasks from session to the database."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        session_key = f"parsed_tasks_{project.pk}"
        raw = request.session.get(session_key)
        if not raw:
            return toast_response("No parsed tasks in session — re-upload the file.", "error", status=400)

        from datetime import date as Date

        try:
            tasks_data = json.loads(raw)
        except json.JSONDecodeError:
            return toast_response("Session data corrupt — re-upload the file.", "error", status=400)

        created = 0
        for td in tasks_data:
            try:
                Task.objects.create(
                    project=project,
                    name=td["name"],
                    description=td.get("description", ""),
                    start_date=Date.fromisoformat(td["start_date"]),
                    end_date=Date.fromisoformat(td["end_date"]),
                    status=td.get("status", "planned"),
                    source=td.get("source", "excel"),
                    activity_code=td.get("activity_code", ""),
                    color=td.get("color", "#3b82f6"),
                )
                created += 1
            except Exception as exc:
                logger.warning("Skipping task row: %s", exc)

        del request.session[session_key]

        tasks = Task.objects.filter(project=project).prefetch_related("ifc_entities")
        response = render(
            request,
            "scheduling/components/task_list.html",
            {"tasks": tasks, "project": project, "preview_mode": False},
        )
        return trigger_toast(response, f"{created} tasks saved successfully.", "success")


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------

class LinkAutoView(ProjectModifyAccessMixin, View):
    """HTMX POST — AI semantic matching of tasks to IFC entities."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        tasks = list(Task.objects.filter(project=project))
        ifc_files = IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
        entities = list(IFCEntity.objects.filter(ifc_file__in=ifc_files))

        if not tasks:
            return toast_response("No tasks to link — import a schedule first.", "error", status=400)
        if not entities:
            return toast_response("No IFC entities found — process an IFC file first.", "error", status=400)

        matches = auto_match_tasks(tasks, entities)
        if matches:
            apply_matches(Task, matches)

        tasks_qs = Task.objects.filter(project=project).prefetch_related("ifc_entities")
        response = render(
            request,
            "scheduling/tabs/attach.html",
            {"tasks": tasks_qs, "matches": matches, "project": project, "match_mode": "auto"},
        )
        msg = f"AI matched {sum(1 for m in matches if m['entity_ids'])} of {len(tasks)} tasks."
        return trigger_toast(response, msg, "success")


class LinkParamView(ProjectModifyAccessMixin, View):
    """HTMX POST — parameter mapping of tasks to IFC entities."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        param_name = request.POST.get("param_name", "").strip()
        if not param_name:
            return toast_response("Enter a property name to match on.", "error", status=400)

        tasks = list(Task.objects.filter(project=project))
        ifc_files = IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
        entities = list(IFCEntity.objects.filter(ifc_file__in=ifc_files))

        if not tasks:
            return toast_response("No tasks to link — import a schedule first.", "error", status=400)

        matches = param_match_tasks(tasks, entities, param_name)
        if matches:
            apply_matches(Task, matches)

        tasks_qs = Task.objects.filter(project=project).prefetch_related("ifc_entities")
        response = render(
            request,
            "scheduling/tabs/attach.html",
            {
                "tasks": tasks_qs,
                "matches": matches,
                "project": project,
                "match_mode": "param",
                "param_name": param_name,
            },
        )
        linked = sum(1 for m in matches if m["entity_ids"])
        return trigger_toast(response, f"Parameter '{param_name}' matched {linked} tasks.", "success")


# ---------------------------------------------------------------------------
# Task management partials
# ---------------------------------------------------------------------------

class TaskListPartialView(ProjectAccessMixin, View):
    """HTMX GET — return the task list partial."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        tasks = Task.objects.filter(project=project).prefetch_related("ifc_entities")
        return render(
            request,
            "scheduling/components/task_list.html",
            {"tasks": tasks, "project": project, "preview_mode": False},
        )


class TaskDeleteView(ProjectModifyAccessMixin, View):
    """HTMX DELETE/POST — delete a single task and return updated list."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        task = get_object_or_404(Task, pk=kwargs["task_pk"], project=project)
        task_name = task.name
        task.delete()

        tasks = Task.objects.filter(project=project).prefetch_related("ifc_entities")
        response = render(
            request,
            "scheduling/components/task_list.html",
            {"tasks": tasks, "project": project, "preview_mode": False},
        )
        return trigger_toast(response, f"'{task_name}' deleted.", "success")


# ---------------------------------------------------------------------------
# Data endpoints
# ---------------------------------------------------------------------------

class GanttDataView(ProjectAccessMixin, View):
    """JSON endpoint — return task data for the Gantt chart renderer."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        tasks = Task.objects.filter(project=project).prefetch_related("ifc_entities")

        data = []
        for task in tasks:
            data.append(
                {
                    "id": str(task.pk),
                    "name": task.name,
                    "start": task.start_date.isoformat(),
                    "end": task.end_date.isoformat(),
                    "status": task.status,
                    "color": task.color,
                    "link_status": task.link_status,
                    "entity_global_ids": task.entity_global_ids(),
                }
            )

        return JsonResponse({"tasks": data})
