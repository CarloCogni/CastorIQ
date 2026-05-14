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

from .models import LinkFeedback, MappingProfile, Task
from .services.column_mapper import CANONICAL_FIELDS, CANONICAL_LABELS, apply_mapping, extract_columns
from .services.embed_linker import embed_match_tasks
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
            # Excel and CSV go through the column-mapping UI first
            if filename.endswith(".xlsx") or filename.endswith(".xls") or filename.endswith(".csv"):
                col_data = extract_columns(uploaded, uploaded.name)
                # Store raw rows in session so MappingSubmitView can apply the mapping
                request.session[f"raw_headers_{project.pk}"] = json.dumps(col_data["headers"])
                request.session[f"raw_rows_{project.pk}"] = json.dumps(col_data["raw_rows"])
                request.session[f"raw_source_{project.pk}"] = col_data["source"]
                # Load saved profiles — pre-serialize column_mapping to JSON for the template
                profiles = [
                    {
                        "pk": str(p["pk"]),
                        "name": p["name"],
                        "column_mapping_json": json.dumps(p["column_mapping"]),
                    }
                    for p in MappingProfile.objects.filter(project=project).values("pk", "name", "column_mapping")
                ]
                return render(
                    request,
                    "scheduling/tabs/mapping.html",
                    {
                        "project": project,
                        "headers": col_data["headers"],
                        "sample_rows": col_data["sample_rows"],
                        "canonical_fields": CANONICAL_FIELDS,
                        "canonical_labels": CANONICAL_LABELS,
                        "profiles": profiles,
                        "filename": col_data["filename"],
                    },
                )
            elif filename.endswith(".xer"):
                tasks = parse_xer(uploaded)
                source = "xer"
            elif filename.endswith(".xml"):
                tasks = parse_msp(uploaded)
                source = "msp"
            else:
                return toast_response(
                    "Unsupported file type. Upload .xlsx, .xls, .csv, .xer, or .xml.", "error", status=400
                )
        except ValueError as exc:
            return toast_response(str(exc), "error", status=400)
        except Exception as exc:
            logger.exception("Schedule file parse error for project %s", project.pk)
            return toast_response(f"Parse failed: {exc}", "error", status=500)

        # XER / MSP bypass mapping — parse directly and go to preview
        validation = validate_schedule(tasks, project_name=project.name)
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


# ---------------------------------------------------------------------------
# Column mapping — Excel / CSV flow
# ---------------------------------------------------------------------------

class MappingSubmitView(ProjectModifyAccessMixin, View):
    """HTMX POST — apply user column mapping to raw rows, show preview."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()

        raw_headers = request.session.get(f"raw_headers_{project.pk}")
        raw_rows = request.session.get(f"raw_rows_{project.pk}")
        source = request.session.get(f"raw_source_{project.pk}", "excel")

        if not raw_headers or not raw_rows:
            return toast_response("Session expired — please re-upload the file.", "error", status=400)

        headers = json.loads(raw_headers)
        rows = json.loads(raw_rows)

        column_mapping = {
            field: request.POST.get(f"col_{field}", "").strip()
            for field in CANONICAL_FIELDS
        }
        # Remove unmapped optional fields so apply_mapping only sees real mappings
        column_mapping = {k: v for k, v in column_mapping.items() if v}

        try:
            tasks = apply_mapping(headers, rows, column_mapping, source)
        except ValueError as exc:
            return toast_response(str(exc), "error", status=400)

        if not tasks:
            return toast_response("No valid task rows found with this mapping.", "error", status=400)

        # Optionally save profile
        profile_name = request.POST.get("profile_name", "").strip()
        if profile_name:
            MappingProfile.objects.update_or_create(
                project=project,
                name=profile_name,
                defaults={"column_mapping": column_mapping},
            )

        validation = validate_schedule(tasks, project_name=project.name)
        request.session[f"parsed_tasks_{project.pk}"] = json.dumps(
            [
                {**t, "start_date": str(t["start_date"]), "end_date": str(t["end_date"])}
                for t in tasks
            ]
        )
        # Clean up raw session data
        for key in (f"raw_headers_{project.pk}", f"raw_rows_{project.pk}", f"raw_source_{project.pk}"):
            request.session.pop(key, None)

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


# ---------------------------------------------------------------------------
# Embedding-based linking
# ---------------------------------------------------------------------------

class EmbedLinkView(ProjectModifyAccessMixin, View):
    """HTMX POST — run embedding similarity, create pending LinkFeedback rows."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        tasks = list(Task.objects.filter(project=project))
        ifc_files = IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
        entities_qs = IFCEntity.objects.filter(ifc_file__in=ifc_files)

        if not tasks:
            return toast_response("No tasks to link — import a schedule first.", "error", status=400)
        if not entities_qs.exists():
            return toast_response("No IFC entities found — process an IFC file first.", "error", status=400)

        try:
            matches = embed_match_tasks(tasks, entities_qs)
        except Exception as exc:
            logger.exception("Embed link failed for project %s", project.pk)
            return toast_response(f"Embedding failed: {exc}", "error", status=500)

        # Clear old pending feedback for these tasks, keep accepted/rejected
        task_ids = [t.pk for t in tasks]
        LinkFeedback.objects.filter(task_id__in=task_ids, accepted__isnull=True).delete()

        for m in matches:
            try:
                entity = IFCEntity.objects.get(pk=m["entity_id"])
                task = Task.objects.get(pk=m["task_id"])
                LinkFeedback.objects.create(
                    task=task,
                    ifc_entity=entity,
                    method=LinkFeedback.Method.EMBEDDING,
                    confidence_at_time=m["confidence"],
                )
            except Exception as exc:
                logger.warning("Could not create LinkFeedback: %s", exc)

        feedbacks = (
            LinkFeedback.objects
            .filter(task__project=project)
            .select_related("task", "ifc_entity", "corrected_to")
            .order_by("-confidence_at_time")
        )
        msg = f"Found {len(matches)} candidate links from {len(tasks)} tasks."
        response = render(
            request,
            "scheduling/components/validation_table.html",
            {"feedbacks": feedbacks, "project": project},
        )
        return trigger_toast(response, msg, "success")


class LinkAcceptView(ProjectModifyAccessMixin, View):
    """HTMX POST — accept a suggested link and create the M2M connection."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        feedback = get_object_or_404(
            LinkFeedback, pk=kwargs["feedback_pk"], task__project=project
        )
        feedback.accepted = True
        feedback.save(update_fields=["accepted"])
        feedback.task.ifc_entities.add(feedback.ifc_entity)

        return render(
            request,
            "scheduling/components/validation_row.html",
            {"fb": feedback, "project": project},
        )


class LinkRejectView(ProjectModifyAccessMixin, View):
    """HTMX POST — reject a suggested link."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        feedback = get_object_or_404(
            LinkFeedback, pk=kwargs["feedback_pk"], task__project=project
        )
        feedback.accepted = False
        feedback.save(update_fields=["accepted"])

        return render(
            request,
            "scheduling/components/validation_row.html",
            {"fb": feedback, "project": project},
        )


class LinkChangeView(ProjectModifyAccessMixin, View):
    """HTMX POST — override the suggested entity with a user-chosen one."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        feedback = get_object_or_404(
            LinkFeedback, pk=kwargs["feedback_pk"], task__project=project
        )
        entity_id = request.POST.get("entity_id", "").strip()
        if not entity_id:
            return toast_response("No entity selected.", "error", status=400)

        try:
            entity = IFCEntity.objects.get(pk=entity_id, ifc_file__project=project)
        except IFCEntity.DoesNotExist:
            return toast_response("Entity not found.", "error", status=404)

        feedback.corrected_to = entity
        feedback.accepted = True
        feedback.save(update_fields=["corrected_to", "accepted"])
        feedback.task.ifc_entities.add(entity)

        return render(
            request,
            "scheduling/components/validation_row.html",
            {"fb": feedback, "project": project},
        )


class LinkSearchView(ProjectAccessMixin, View):
    """HTMX GET — typeahead entity search for the change-entity panel."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        q = request.GET.get("q", "").strip()
        feedback_pk = request.GET.get("feedback_pk", "")

        ifc_files = IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
        qs = IFCEntity.objects.filter(ifc_file__in=ifc_files)
        if q:
            qs = qs.filter(name__icontains=q)
        entities = qs.order_by("name")[:10]

        return render(
            request,
            "scheduling/components/link_search.html",
            {"entities": entities, "project": project, "feedback_pk": feedback_pk},
        )
