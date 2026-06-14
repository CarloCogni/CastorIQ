# castor/scheduling/views.py
"""4D TimeLiner scheduling views — file upload, linking, Gantt, and simulation."""

from __future__ import annotations

import csv
import io
import json
import logging
import math
from datetime import date

from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views import View
from django.views.generic import TemplateView

from core.http import toast_response, trigger_toast
from core.mixins import ProjectAccessMixin, ProjectModifyAccessMixin, ProjectTabMixin
from ifc_processor.models import IFCEntity, IFCFile
from scheduling.governance_access import GovernanceCapabilityMixin
from scheduling.services.governance.authority import GovernanceAuthorityError, GovernanceCapability

from .models import (
    MappingProfile,
    P6ResourceAssignment,
    P6WBSNode,
    ScheduleSource,
    Task,
    TaskDependency,
    TaskEntityBinding,
)
from .parsers.p6xml_parser import parse_p6xml
from .services.approved_match_persistence import (
    ApprovalValidationError,
    ApprovedMatchPersistenceService,
    MatchApprovalRequest,
    StalePreviewError,
)
from .services.autolink import run_autolink
from .services.column_mapper import (
    CANONICAL_FIELDS,
    CANONICAL_LABELS,
    apply_mapping,
    default_visible_columns,
    extract_columns,
    suggest_mapping,
)
from .services.critical_path import compute_critical_path
from .services.evm import compute_evm
from .services.match_preview import MatchPreviewService
from .services.msp_parser import parse_msp
from .services.p6_save import save_p6_pending_data
from .services.pct_normalize import normalize_pct_complete
from .services.source_version.content_hash import (
    hash_parsed_tasks_payload,
    store_session_import_artifact,
)
from .services.source_version.import_persistence import persist_schedule_import
from .services.source_version.import_provenance import (
    ImportProvenanceContext,
    ScheduleImportProvenanceCoordinator,
)
from .services.validator import validate_schedule
from .services.xer_parser import parse_xer

logger = logging.getLogger(__name__)


def _annotate_binding_link_state(project, tasks: list) -> None:
    """Attach binding_link_count and binding_link_status to task instances in-place."""
    from .services.link_resolver import entity_gids_by_task, link_status_for_task

    if not tasks:
        return
    link_map = entity_gids_by_task(project.pk, [t.pk for t in tasks])
    for task in tasks:
        gids = link_map.get(str(task.pk), [])
        task.binding_link_count = len(gids)
        task.binding_link_status = link_status_for_task(task, gids)


def _task_list_render_context(project, queryset, **extra: object) -> dict:
    """Build task_list.html context with TaskEntityBinding link counts (not M2M)."""
    tasks = list(queryset)
    _annotate_binding_link_state(project, tasks)
    return {
        "tasks": tasks,
        "project": project,
        "preview_mode": False,
        **extra,
    }


class ScheduleView(ProjectTabMixin, TemplateView):
    """Main TimeLiner panel — entry point for all scheduling sub-tabs."""

    active_tab = "castor"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        project = ctx["project"]
        ctx["castor_subtab"] = "schedule"
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

        ctx["ifc_param_name"] = self.request.session.get(
            f"ifc_param_name_{project.pk}", "Activity ID"
        )
        ctx["binding_review_count"] = TaskEntityBinding.objects.filter(
            task__project=project, needs_review=True
        ).count()
        ctx["dep_count"] = TaskDependency.objects.filter(predecessor__project=project).count()
        ctx["schedule_sources"] = list(
            ScheduleSource.objects.filter(project=project).order_by("-imported_at")[:10]
        )
        ctx["intel_suggestions"] = [
            "Which tasks are delayed and by how much?",
            "What is the overall schedule performance?",
            "Summarise MEP stage progress.",
            "Which tasks are at risk of missing their deadline?",
            "What work is planned to start next week?",
        ]

        if ctx["schedule_tab"] == "data_sources":
            from django.db.models import Count as _Count

            from .models import P6Calendar

            # Calendars with at least one task assigned
            task_cal_counts = dict(
                Task.objects.filter(project=project)
                .exclude(calendar_object_id="")
                .exclude(calendar_object_id=None)
                .values("calendar_object_id")
                .annotate(n=_Count("pk"))
                .values_list("calendar_object_id", "n")
            )
            used_cals = []
            for cal in P6Calendar.objects.filter(
                project=project,
                p6_calendar_id__in=list(task_cal_counts.keys()),
                is_pending=False,
            ).order_by("-p6_calendar_id"):
                wd = set(cal.working_days or [])
                # Compact weekday abbreviations in canonical Sun–Sat order,
                # space-separated so "Su Mo Tu We Th Sa" is legible.
                _day_abbr = {
                    "Sunday": "Su",
                    "Monday": "Mo",
                    "Tuesday": "Tu",
                    "Wednesday": "We",
                    "Thursday": "Th",
                    "Friday": "Fr",
                    "Saturday": "Sa",
                }
                _all_days = [
                    "Sunday",
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                ]
                week_str = " ".join(_day_abbr[d] for d in _all_days if d in wd)
                holidays = sorted(cal.holidays or [])
                used_cals.append(
                    {
                        "cal_id": cal.p6_calendar_id,
                        "task_count": task_cal_counts.get(cal.p6_calendar_id, 0),
                        "week_str": week_str,
                        "n_holidays": len(holidays),
                        "holiday_sample": holidays[:5],
                    }
                )
            ctx["project_calendars"] = sorted(used_cals, key=lambda c: -c["task_count"])

            from .models import P6ResourceAssignment

            phys_tasks = (
                Task.objects.filter(project=project, is_non_physical=False)
                .exclude(start_date=None)
                .exclude(end_date=None)
            )
            n_total = phys_tasks.count()

            if n_total > 0:
                n_with_actual_start = phys_tasks.filter(actual_start__isnull=False).count()
                n_with_cost = phys_tasks.filter(cost__isnull=False, cost__gt=0).count()
                ra_planned = P6ResourceAssignment.objects.filter(
                    task__project=project, planned_cost__gt=0
                ).count()
                ra_actual = P6ResourceAssignment.objects.filter(
                    task__project=project, actual_cost__gt=0
                ).count()

                # EV % complete coverage: active tasks with real P6 pct data
                active_qs = phys_tasks.filter(
                    actual_start__isnull=False,
                ).exclude(status="complete")
                n_active = active_qs.count()
                n_ev_real = (
                    active_qs.filter(
                        Q(physical_percent_complete__gt=0) | Q(duration_percent_complete__gt=0)
                    ).count()
                    if n_active
                    else 0
                )

                # P6 data date — from most recent P6 XML source for this project
                p6_source = (
                    ScheduleSource.objects.filter(project=project, data_date__isnull=False)
                    .order_by("-imported_at")
                    .first()
                )
                p6_data_date = p6_source.data_date if p6_source else None
                data_freshness_days = (date.today() - p6_data_date).days if p6_data_date else None

                ctx["data_readiness"] = {
                    "total_tasks": n_total,
                    "baseline_pct": 100,  # already filtered on start/end not null
                    "actuals_pct": round(n_with_actual_start * 100 / n_total, 1),
                    "planned_cost_pct": round(n_with_cost * 100 / n_total, 1),
                    "planned_cost_from_ra": ra_planned > 0,
                    "actual_cost_available": ra_actual > 0,
                    "ra_actual_count": ra_actual,
                    "has_calendar": bool(used_cals),
                    "n_active": n_active,
                    "n_ev_real_pct": n_ev_real,
                    "ev_real_pct_coverage": round(n_ev_real * 100 / n_active, 1) if n_active else 0,
                    "p6_data_date": p6_data_date,
                    "data_freshness_days": data_freshness_days,
                    "data_freshness_months": round(data_freshness_days / 30.4, 1)
                    if data_freshness_days
                    else None,
                }
            else:
                ctx["data_readiness"] = None

        return ctx


def _scan_date_range(
    rows: list[list],
    headers: list[str],
    start_header: str,
    end_header: str,
) -> dict | None:
    """Scan all rows and return {"start": ISO, "end": ISO} or None."""
    try:
        si, ei = headers.index(start_header), headers.index(end_header)
    except ValueError:
        return None
    from .services.column_mapper import _to_date  # co-located private helper

    min_s = max_e = None
    for row in rows:
        sv = str(row[si]).strip() if si < len(row) and row[si] is not None else ""
        ev = str(row[ei]).strip() if ei < len(row) and row[ei] is not None else ""
        s, e = _to_date(sv), _to_date(ev)
        if s and (min_s is None or s < min_s):
            min_s = s
        if e and (max_e is None or e > max_e):
            max_e = e
    return {"start": min_s.isoformat(), "end": max_e.isoformat()} if min_s and max_e else None


_PREVIEW_PARSED_COLS = [
    "name",
    "start_date",
    "end_date",
    "wbs_name",
    "status",
    "activity_code",
    "actual_start",
    "actual_end",
    "activity_type",
    "total_float_days",
]
_PREVIEW_PARSED_VISIBLE = [
    "name",
    "start_date",
    "end_date",
    "status",
    "activity_code",
    "activity_type",
]


class SchedulePreviewView(ProjectModifyAccessMixin, View):
    """JSON POST — detect format, return columns + suggested mapping + ≤200 rows.

    Used by the Dynamic Preview Table UI to render an interactive column-mapping
    experience before the user commits to saving the schedule.

    Response schema (all formats):
        format           — "excel" | "csv" | "xer" | "msp" | "p6xml"
        needs_mapping    — bool: True for Excel/CSV, False for XER/XML
        raw_columns      — list[str]: header names (Excel/CSV) or canonical field names
        suggested_mapping — dict[str, str]: {canonical_field: matched_column}
                           Empty for XER/XML (already fully mapped)
        default_visible  — list[str]: columns to show initially in the preview table
        rows             — list[list[str]]: up to 200 rows of raw values
        total_rows       — int: true total for Excel/CSV; ≤200 for XER/XML (preview cap)
    """

    def post(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        uploaded = request.FILES.get("schedule_file")
        if not uploaded:
            return JsonResponse({"error": "No file selected."}, status=400)

        filename = uploaded.name.lower()
        try:
            if filename.endswith((".xlsx", ".xls", ".csv")):
                return self._preview_tabular(request, project, uploaded)
            elif filename.endswith(".xer"):
                return self._preview_parsed(request, project, uploaded, parse_xer)
            elif filename.endswith(".xml"):
                file_bytes = uploaded.read()
                if b"APIBusinessObjects" in file_bytes[:2048]:
                    _tasks, _deps, _aux = parse_p6xml(io.BytesIO(file_bytes))
                    save_p6_pending_data(project, _aux)
                    _dd = (_aux.get("project_meta") or {}).get("data_date")
                    if _dd:
                        request.session[f"p6_data_date_{project.pk}"] = _dd.isoformat()
                    return self._preview_parsed(
                        request, project, uploaded, lambda _f: (_tasks, _deps)
                    )
                return self._preview_parsed(request, project, io.BytesIO(file_bytes), parse_msp)
            else:
                return JsonResponse(
                    {"error": ("Unsupported file type. Upload .xlsx, .xls, .csv, .xer, or .xml.")},
                    status=400,
                )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("Schedule preview failed")
            return JsonResponse({"error": f"Preview failed: {exc}"}, status=500)

    def _preview_tabular(self, request, project, file_obj) -> JsonResponse:
        col_data = extract_columns(file_obj, file_obj.name)
        headers: list[str] = col_data["headers"]
        raw_rows: list[list] = col_data["raw_rows"]

        # Store in session so MappingSubmitView can apply the mapping without a re-upload.
        request.session[f"raw_headers_{project.pk}"] = json.dumps(headers)
        request.session[f"raw_rows_{project.pk}"] = json.dumps(raw_rows)
        request.session[f"raw_source_{project.pk}"] = col_data["source"]
        store_session_import_artifact(
            request,
            project.pk,
            filename=file_obj.name,
            content=file_obj.read() if hasattr(file_obj, "read") else None,
        )
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)

        mapping = suggest_mapping(headers)
        visible = default_visible_columns(headers, mapping)

        date_range = None
        start_h, end_h = mapping.get("start_date"), mapping.get("end_date")
        if start_h and end_h:
            date_range = _scan_date_range(raw_rows, headers, start_h, end_h)

        preview_rows = raw_rows[:200]
        return JsonResponse(
            {
                "format": col_data["source"],
                "needs_mapping": True,
                "raw_columns": headers,
                "suggested_mapping": mapping,
                "default_visible": visible,
                "rows": [[str(v) if v is not None else "" for v in row] for row in preview_rows],
                "total_rows": len(raw_rows),
                "preview_rows": len(preview_rows),
                "project_date_range": date_range,
                "deps": [],
            }
        )

    def _preview_parsed(self, request, project, file_obj, parser_fn) -> JsonResponse:
        content = file_obj.read() if hasattr(file_obj, "read") else b""
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        tasks, raw_deps = parser_fn(file_obj)
        store_session_import_artifact(
            request,
            project.pk,
            filename=getattr(file_obj, "name", ""),
            content=content,
            tasks_fallback=tasks,
        )

        # Full parse — store in session so TaskSaveView can persist without a re-upload.
        request.session[f"parsed_tasks_{project.pk}"] = json.dumps(
            [
                {
                    **t,
                    "start_date": str(t["start_date"]),
                    "end_date": str(t["end_date"]),
                    "actual_start": str(t["actual_start"]) if t.get("actual_start") else None,
                    "actual_end": str(t["actual_end"]) if t.get("actual_end") else None,
                    "early_start": str(t["early_start"]) if t.get("early_start") else None,
                    "early_finish": str(t["early_finish"]) if t.get("early_finish") else None,
                    "late_start": str(t["late_start"]) if t.get("late_start") else None,
                    "late_finish": str(t["late_finish"]) if t.get("late_finish") else None,
                    "expected_finish": str(t["expected_finish"])
                    if t.get("expected_finish")
                    else None,
                    "constraint_date": str(t["constraint_date"])
                    if t.get("constraint_date")
                    else None,
                }
                for t in tasks
            ]
        )
        if raw_deps:
            request.session[f"parsed_deps_{project.pk}"] = json.dumps(raw_deps)

        # Build ID → activity_code maps to normalise raw deps for the browser
        xer_to_code: dict[str, str] = {
            t["_xer_task_id"]: t["activity_code"]
            for t in tasks
            if t.get("_xer_task_id") and t.get("activity_code")
        }
        uid_to_code: dict[str, str] = {
            t["_msp_uid"]: t["activity_code"]
            for t in tasks
            if t.get("_msp_uid") and t.get("activity_code")
        }
        p6_to_code: dict[str, str] = {
            t["_p6_obj_id"]: t["activity_code"]
            for t in tasks
            if t.get("_p6_obj_id") and t.get("activity_code")
        }

        normalized_deps: list[dict] = []
        for d in raw_deps or []:
            if "pred_xer_id" in d:
                pred_code = xer_to_code.get(d["pred_xer_id"])
                succ_code = xer_to_code.get(d["succ_xer_id"])
            elif "pred_uid" in d:
                pred_code = uid_to_code.get(d["pred_uid"])
                succ_code = uid_to_code.get(d["succ_uid"])
            elif "pred_p6_obj_id" in d:
                pred_code = p6_to_code.get(d["pred_p6_obj_id"])
                succ_code = p6_to_code.get(d["succ_p6_obj_id"])
            else:
                continue
            if pred_code and succ_code:
                normalized_deps.append(
                    {
                        "pred": pred_code,
                        "succ": succ_code,
                        "type": d.get("dep_type", "FS"),
                        "lag": d.get("lag_days", 0),
                    }
                )

        normalized_deps.sort(key=lambda x: x["pred"])
        normalized_deps = normalized_deps[:5000]

        cols = _PREVIEW_PARSED_COLS
        # total_float_days=0 means critical — must not collapse to "" like falsy `or ""` would
        numeric_cols = {"total_float_days"}
        all_rows = [
            [
                (str(t.get(c)) if t.get(c) is not None else "")
                if c in numeric_cols
                else str(t.get(c) or "")
                for c in cols
            ]
            for t in tasks
        ]
        fmt = tasks[0].get("source", "msp") if tasks else "msp"

        starts = [t["start_date"] for t in tasks if t.get("start_date")]
        ends = [t["end_date"] for t in tasks if t.get("end_date")]
        date_range = (
            {"start": min(starts).isoformat(), "end": max(ends).isoformat()}
            if starts and ends
            else None
        )

        preview = all_rows[:200]
        return JsonResponse(
            {
                "format": fmt,
                "needs_mapping": False,
                "raw_columns": cols,
                "suggested_mapping": {col: col for col in cols},
                "default_visible": _PREVIEW_PARSED_VISIBLE,
                "rows": preview,
                "total_rows": len(tasks),
                "preview_rows": len(preview),
                "project_date_range": date_range,
                "deps": normalized_deps,
            }
        )


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
                    for p in MappingProfile.objects.filter(project=project).values(
                        "pk", "name", "column_mapping"
                    )
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
                xer_bytes = uploaded.read()
                tasks, raw_deps = parse_xer(io.BytesIO(xer_bytes))
                source = "xer"
                store_session_import_artifact(
                    request, project.pk, filename=uploaded.name, content=xer_bytes
                )
            elif filename.endswith(".xml"):
                file_bytes = uploaded.read()
                if b"APIBusinessObjects" in file_bytes[:2048]:
                    tasks, raw_deps, aux_data = parse_p6xml(io.BytesIO(file_bytes))
                    save_p6_pending_data(project, aux_data)
                    source = "p6xml"
                    _dd = (aux_data.get("project_meta") or {}).get("data_date")
                    if _dd:
                        request.session[f"p6_data_date_{project.pk}"] = _dd.isoformat()
                else:
                    tasks, raw_deps = parse_msp(io.BytesIO(file_bytes))
                    source = "msp"
                store_session_import_artifact(
                    request, project.pk, filename=uploaded.name, content=file_bytes
                )
            else:
                return toast_response(
                    "Unsupported file type. Upload .xlsx, .xls, .csv, .xer, or .xml.",
                    "error",
                    status=400,
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
                {
                    **t,
                    "start_date": str(t["start_date"]),
                    "end_date": str(t["end_date"]),
                    "actual_start": str(t["actual_start"]) if t.get("actual_start") else None,
                    "actual_end": str(t["actual_end"]) if t.get("actual_end") else None,
                    "early_start": str(t["early_start"]) if t.get("early_start") else None,
                    "early_finish": str(t["early_finish"]) if t.get("early_finish") else None,
                    "late_start": str(t["late_start"]) if t.get("late_start") else None,
                    "late_finish": str(t["late_finish"]) if t.get("late_finish") else None,
                    "expected_finish": str(t["expected_finish"])
                    if t.get("expected_finish")
                    else None,
                    "constraint_date": str(t["constraint_date"])
                    if t.get("constraint_date")
                    else None,
                }
                for t in tasks
            ]
        )
        if raw_deps:
            request.session[f"parsed_deps_{project.pk}"] = json.dumps(raw_deps)
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


def _resolve_import_phys_pct(task_data: dict) -> float | None:
    """Normalize physical progress from import dict keys (defense in depth at save)."""
    for key in ("_p6_phys_pct", "_csv_pct_complete"):
        normalized = normalize_pct_complete(task_data.get(key))
        if normalized is not None:
            return normalized
    return None


class TaskSaveView(ProjectModifyAccessMixin, View):
    """HTMX POST — persist parsed tasks from session to the database."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        session_key = f"parsed_tasks_{project.pk}"
        raw = request.session.get(session_key)
        if not raw:
            return toast_response(
                "No parsed tasks in session — re-upload the file.", "error", status=400
            )

        try:
            tasks_data = json.loads(raw)
        except json.JSONDecodeError:
            return toast_response("Session data corrupt — re-upload the file.", "error", status=400)

        replace_mode = request.POST.get("replace") == "true" or bool(
            request.session.pop(f"schedule_replace_{project.pk}", False)
        )

        filename = request.session.pop(f"schedule_filename_{project.pk}", "")
        source_format = tasks_data[0].get("source", "excel") if tasks_data else "excel"
        content_hash = request.session.pop(f"schedule_content_hash_{project.pk}", "") or (
            hash_parsed_tasks_payload(tasks_data)
        )
        _p6_dd_str = request.session.pop(f"p6_data_date_{project.pk}", None)
        _p6_data_date = date.fromisoformat(_p6_dd_str) if _p6_dd_str else None

        coordinator = ScheduleImportProvenanceCoordinator(project, request.user)
        ctx = ImportProvenanceContext(
            source_type=source_format,
            source_filename=filename,
            content_hash=content_hash,
            mode=ScheduleImportProvenanceCoordinator.resolve_mode(replace_mode),
            data_date=_p6_data_date,
        )
        run_id = coordinator.start_run(ctx)

        raw_deps_json = request.session.pop(f"parsed_deps_{project.pk}", None)
        raw_deps: list[dict] = json.loads(raw_deps_json) if raw_deps_json else []
        del request.session[session_key]

        try:
            with transaction.atomic():
                persist_result = persist_schedule_import(
                    project,
                    tasks_data=tasks_data,
                    raw_deps=raw_deps,
                    replace_mode=replace_mode,
                    filename=filename,
                    source_format=source_format,
                    data_date=_p6_data_date,
                )
                coordinator.complete_success(run_id, ctx, persist_result)
        except Exception as exc:
            logger.exception("Schedule import failed for project %s", project.pk)
            coordinator.complete_failure(run_id, error_summary=str(exc))
            return toast_response(f"Import failed: {exc}", "error", status=500)

        created = persist_result.created
        updated = persist_result.updated
        unchanged = persist_result.unchanged
        skipped_count = persist_result.skipped_count
        cleaned = persist_result.cleaned
        dep_count = persist_result.dep_count

        has_p6_cpm = any(
            td.get("total_float_days") is not None or td.get("early_start") for td in tasks_data
        )
        if has_p6_cpm:
            logger.info(
                "P6 CPM fields present in import for project %s — will recompute after save",
                project.pk,
            )

        cpm_attempted = False
        cpm_ok = False
        cpm_skipped = False
        try:
            schedulable = (
                Task.objects.filter(project=project, is_non_physical=False)
                .exclude(start_date=None)
                .exclude(end_date=None)
            )
            if schedulable.exists():
                cpm_attempted = True
                cpm = compute_critical_path(str(project.pk))
                cpm_ok = True
                logger.info(
                    "CPM recomputed after import: %d critical of %d tasks (project %s)",
                    len(cpm["critical_task_ids"]),
                    len(cpm["task_data"]),
                    project.pk,
                )
            elif Task.objects.filter(project=project).exists():
                cpm_skipped = True
                logger.info(
                    "CPM skipped after import — no schedulable tasks (project %s)",
                    project.pk,
                )
        except Exception as exc:
            logger.warning("CPM recompute after import failed: %s", exc)

        tasks = Task.objects.filter(project=project).order_by("start_date", "name")
        response = render(
            request,
            "scheduling/components/task_list.html",
            _task_list_render_context(project, tasks, dep_count=dep_count),
        )
        parts = []
        if created:
            parts.append(f"{created} task{'s' if created != 1 else ''} created")
        if updated:
            parts.append(f"{updated} updated")
        if unchanged:
            parts.append(f"{unchanged} unchanged")
        msg = (", ".join(parts) or "No new tasks") + "."
        if dep_count:
            msg += f" {dep_count} dependenc{'y' if dep_count == 1 else 'ies'} imported."
            if cpm_attempted and cpm_ok:
                msg += " CPM recomputed."
        elif cpm_attempted and cpm_ok:
            msg += " CPM recomputed."
        if cpm_attempted and not cpm_ok:
            msg += " Schedule imported, but CPM recompute failed — check logs or re-run CPM."
        if cpm_skipped:
            msg += " CPM skipped — no schedulable tasks."
        if skipped_count:
            msg += f" {skipped_count} task row{'s' if skipped_count != 1 else ''} skipped — check logs."
        if cleaned:
            msg += f" Cleaned {cleaned} duplicate task{'s' if cleaned != 1 else ''}."
        toast_level = "success"
        if cpm_attempted and not cpm_ok:
            toast_level = "error"
        elif skipped_count:
            toast_level = "info"
        return trigger_toast(response, msg, toast_level)


class ScheduleClearView(ProjectModifyAccessMixin, View):
    """POST — delete all tasks and dependencies for this project."""

    def post(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        TaskDependency.objects.filter(predecessor__project=project).delete()
        deleted, _ = Task.objects.filter(project=project).delete()
        ScheduleSource.objects.filter(project=project).delete()
        # Cascade handles confirmed P6 records; explicitly remove orphaned pending ones.
        P6WBSNode.objects.filter(project=project, is_pending=True).delete()
        P6ResourceAssignment.objects.filter(project=project, is_pending=True).delete()
        logger.info("Cleared %d tasks for project %s", deleted, project.pk)
        return JsonResponse({"deleted": deleted, "status": "ok"})


class ScheduleSourceDeleteView(ProjectModifyAccessMixin, View):
    """POST — delete one ScheduleSource record and all tasks imported from it."""

    def post(self, request, source_pk: str, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        source = get_object_or_404(ScheduleSource, pk=source_pk, project=project)
        task_count = Task.objects.filter(project=project, schedule_source=source).count()
        Task.objects.filter(project=project, schedule_source=source).delete()
        source.delete()
        logger.info(
            "Deleted source '%s' and %d tasks for project %s",
            source.filename,
            task_count,
            project.pk,
        )
        return JsonResponse({"deleted_tasks": task_count, "status": "ok"})


class ScheduleSourcePreviewView(ProjectAccessMixin, View):
    """GET — rebuild a preview JSON payload from already-saved tasks for a ScheduleSource.

    Response is identical in shape to SchedulePreviewView so the frontend can
    call initFromData() directly without any special handling.
    """

    def get(self, request, source_pk: str, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        source = get_object_or_404(ScheduleSource, pk=source_pk, project=project)

        tasks = list(Task.objects.filter(schedule_source=source).order_by("start_date", "name"))
        # Old imports predating the schedule_source FK have schedule_source=NULL.
        # Fall back to all project tasks so the preview still shows the full schedule.
        if not tasks:
            tasks = list(Task.objects.filter(project=project).order_by("start_date", "name"))

        task_pks = {t.pk for t in tasks}
        raw_deps = (
            TaskDependency.objects.filter(predecessor__in=task_pks, successor__in=task_pks)
            .select_related("predecessor", "successor")
            .order_by("predecessor__activity_code")
        )
        normalized_deps = [
            {
                "pred": d.predecessor.activity_code,
                "succ": d.successor.activity_code,
                "type": d.dep_type,
                "lag": d.lag_days,
            }
            for d in raw_deps
            if d.predecessor.activity_code and d.successor.activity_code
        ]

        cols = _PREVIEW_PARSED_COLS
        rows = []
        for t in tasks:
            row = []
            for c in cols:
                if c == "name":
                    row.append(t.name)
                elif c == "start_date":
                    row.append(str(t.start_date))
                elif c == "end_date":
                    row.append(str(t.end_date))
                elif c == "wbs_name":
                    row.append("")
                elif c == "status":
                    row.append(t.status)
                elif c == "activity_code":
                    row.append(t.activity_code)
                elif c == "actual_start":
                    row.append(str(t.actual_start) if t.actual_start else "")
                elif c == "actual_end":
                    row.append(str(t.actual_end) if t.actual_end else "")
                elif c == "activity_type":
                    row.append(t.activity_type)
                elif c == "total_float_days":
                    row.append(str(t.total_float) if t.total_float is not None else "")
                else:
                    row.append("")
            rows.append(row)

        starts = [t.start_date for t in tasks if t.start_date]
        ends = [t.end_date for t in tasks if t.end_date]
        date_range = (
            {"start": min(starts).isoformat(), "end": max(ends).isoformat()}
            if starts and ends
            else None
        )

        preview = rows[:200]
        return JsonResponse(
            {
                "format": source.source_format,
                "needs_mapping": False,
                "raw_columns": cols,
                "suggested_mapping": {col: col for col in cols},
                "default_visible": _PREVIEW_PARSED_VISIBLE,
                "rows": preview,
                "total_rows": len(tasks),
                "preview_rows": len(preview),
                "project_date_range": date_range,
                "deps": normalized_deps,
            }
        )


class AllTasksPreviewView(ProjectAccessMixin, View):
    """GET — all tasks for the project in Gantt preview format."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        total = Task.objects.filter(project=project).count()
        tasks = list(Task.objects.filter(project=project).order_by("start_date", "name"))
        task_pks = {t.pk for t in tasks}
        raw_deps = (
            TaskDependency.objects.filter(predecessor__in=task_pks, successor__in=task_pks)
            .select_related("predecessor", "successor")
            .order_by("predecessor__activity_code")
        )
        normalized_deps = [
            {
                "pred": d.predecessor.activity_code,
                "succ": d.successor.activity_code,
                "type": d.dep_type,
                "lag": d.lag_days,
            }
            for d in raw_deps
            if d.predecessor.activity_code and d.successor.activity_code
        ]
        cols = _PREVIEW_PARSED_COLS
        rows = []
        for t in tasks:
            row = []
            for c in cols:
                if c == "name":
                    row.append(t.name)
                elif c == "start_date":
                    row.append(str(t.start_date))
                elif c == "end_date":
                    row.append(str(t.end_date))
                elif c == "wbs_name":
                    row.append("")
                elif c == "status":
                    row.append(t.status)
                elif c == "activity_code":
                    row.append(t.activity_code)
                elif c == "actual_start":
                    row.append(str(t.actual_start) if t.actual_start else "")
                elif c == "actual_end":
                    row.append(str(t.actual_end) if t.actual_end else "")
                elif c == "activity_type":
                    row.append(t.activity_type)
                elif c == "total_float_days":
                    row.append(str(t.total_float) if t.total_float is not None else "")
                else:
                    row.append("")
            rows.append(row)
        starts = [t.start_date for t in tasks if t.start_date]
        ends = [t.end_date for t in tasks if t.end_date]
        date_range = (
            {"start": min(starts).isoformat(), "end": max(ends).isoformat()}
            if starts and ends
            else None
        )
        return JsonResponse(
            {
                "rows": rows,
                "deps": normalized_deps,
                "total_rows": total,
                "project_date_range": date_range,
            }
        )


class TaskActualDateView(ProjectModifyAccessMixin, View):
    """HTMX POST — update actual_start / actual_end on a single task inline."""

    def post(self, request, task_pk: str, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        task = get_object_or_404(Task, pk=task_pk, project=project)

        actual_start_raw = request.POST.get("actual_start", "").strip()
        actual_end_raw = request.POST.get("actual_end", "").strip()

        try:
            actual_start = date.fromisoformat(actual_start_raw) if actual_start_raw else None
            actual_end = date.fromisoformat(actual_end_raw) if actual_end_raw else None
        except ValueError as exc:
            return toast_response(f"Invalid date: {exc}", "error", status=400)

        if actual_start and actual_end and actual_end < actual_start:
            return toast_response(
                "Actual end must be on or after actual start.", "error", status=400
            )

        task.actual_start = actual_start
        task.actual_end = actual_end
        task.save(update_fields=["actual_start", "actual_end"])

        response = render(
            request,
            "scheduling/components/actual_date_cells.html",
            {"task": task, "project": project},
        )
        return trigger_toast(response, "Actual dates updated.", "success")


class LinkParamView(ProjectModifyAccessMixin, View):
    """HTMX POST — parameter mapping (persistence blocked until E1-E approval)."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        self.get_project()
        param_name = request.POST.get("param_name", "").strip()
        if not param_name:
            return toast_response("Enter a property name to match on.", "error", status=400)

        return toast_response(
            "Parameter Match persistence is disabled until you preview and approve. "
            "Use Preview Match on the 4D Link tab first.",
            "info",
            status=400,
        )


class MatchPreviewView(ProjectAccessMixin, View):
    """GET — read-only exact-match preview for Task.activity_code ↔ IFC property."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        param_name = request.GET.get("param_name", "Activity ID").strip()
        if not param_name:
            return JsonResponse({"error": "param_name is required."}, status=400)

        preview = MatchPreviewService(project).preview(param_name)
        payload = preview.to_dict()

        wants_json = request.GET.get(
            "format"
        ) == "json" or "application/json" in request.headers.get("Accept", "")
        if wants_json and not request.headers.get("HX-Request"):
            if preview.errors:
                return JsonResponse(payload, status=400)
            return JsonResponse(payload)

        response = render(
            request,
            "scheduling/components/param_match_preview.html",
            {"preview": preview, "project": project},
        )
        if preview.errors:
            return trigger_toast(
                response,
                preview.errors[0],
                "error",
            )
        return response


class ApplyApprovedMatchView(ProjectModifyAccessMixin, View):
    """POST — persist trusted bindings after fingerprint-validated approval."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()

        if request.content_type.startswith("application/json"):
            try:
                payload = json.loads(request.body.decode() or "{}")
            except json.JSONDecodeError:
                return JsonResponse({"error": "Invalid JSON body."}, status=400)
        else:
            payload = request.POST.dict()
            payload["confirm_acknowledged"] = request.POST.get("confirm_acknowledged")

        try:
            approval = MatchApprovalRequest.from_payload(payload)
            result = ApprovedMatchPersistenceService(project, request.user).persist(approval)
        except StalePreviewError as exc:
            if request.headers.get("HX-Request"):
                response = render(
                    request,
                    "scheduling/components/param_match_apply_conflict.html",
                    {"error": exc.message, "details": exc.details, "project": project},
                    status=409,
                )
                return trigger_toast(
                    response,
                    "Preview is stale — regenerate preview before applying.",
                    "error",
                )
            return JsonResponse(
                {"error": exc.message, **exc.details},
                status=409,
            )
        except ApprovalValidationError as exc:
            if request.headers.get("HX-Request"):
                return toast_response(exc.message, "error", status=400)
            return JsonResponse({"error": exc.message, **exc.details}, status=400)

        result_dict = result.to_dict()
        wants_json = (
            request.GET.get("format") == "json"
            or payload.get("format") == "json"
            or (
                "application/json" in request.headers.get("Accept", "")
                and not request.headers.get("HX-Request")
            )
        )
        if wants_json:
            return JsonResponse(result_dict)

        response = render(
            request,
            "scheduling/components/param_match_apply_result.html",
            {"result": result, "project": project},
        )
        msg = (
            f"Persisted {result.inserted_accepted_bindings} new, "
            f"{result.promoted_review_bindings} promoted, "
            f"{result.noop_existing_accepted_bindings} unchanged."
        )
        return trigger_toast(response, msg, "success")


class AutoLinkView(ProjectModifyAccessMixin, View):
    """HTMX POST — run the 4-layer smart auto-link pipeline and return summary."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_param_name = request.session.get(f"ifc_param_name_{project.pk}") or None

        try:
            summary = run_autolink(project, ifc_param_name)
        except Exception as exc:
            logger.exception("Auto-link pipeline failed for project %s", project.pk)
            return toast_response(f"Auto-link failed: {exc}", "error", status=500)

        total_linked = (
            summary["linked_exact"]
            + summary["linked_normalized"]
            + summary["linked_heuristic"]
            + summary["linked_embedding"]
        )
        response = render(
            request,
            "scheduling/components/autolink_summary.html",
            {"summary": summary, "project": project, "ifc_param_name": ifc_param_name},
        )
        msg = (
            f"Linked {total_linked} of {summary['total_tasks']} tasks."
            f" {summary['needs_review']} need review."
        )
        return trigger_toast(response, msg, "success")


class TaskListPartialView(ProjectAccessMixin, View):
    """HTMX GET — return the task list partial."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        tasks = Task.objects.filter(project=project).order_by("start_date", "name")
        return render(
            request,
            "scheduling/components/task_list.html",
            _task_list_render_context(project, tasks),
        )


class TaskDeleteView(ProjectModifyAccessMixin, View):
    """HTMX DELETE/POST — delete a single task and return updated list."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        task = get_object_or_404(Task, pk=kwargs["task_pk"], project=project)
        task_name = task.name
        task.delete()

        tasks = Task.objects.filter(project=project).order_by("start_date", "name")
        response = render(
            request,
            "scheduling/components/task_list.html",
            _task_list_render_context(project, tasks),
        )
        return trigger_toast(response, f"'{task_name}' deleted.", "success")


_STAGE_COLORS: dict[str, str] = {
    "substructure": "#78350f",
    "structure": "#dc2626",
    "envelope": "#d97706",
    "mep": "#2563eb",
    "finishes": "#16a34a",
    "external": "#0891b2",
    "": "#6b7280",
}


def _compute_progress(task: Task, today: date) -> int:
    """Estimate task completion 0–100% from actual/planned dates and status."""
    if task.actual_end or task.status == Task.Status.COMPLETE:
        return 100
    if task.actual_start:
        dur = max((task.end_date - task.actual_start).days, 1)
        elapsed = (today - task.actual_start).days
        return max(0, min(99, int(elapsed / dur * 100)))
    if task.status == Task.Status.ACTIVE and task.start_date <= today:
        dur = max((task.end_date - task.start_date).days, 1)
        elapsed = (today - task.start_date).days
        return max(0, min(99, int(elapsed / dur * 100)))
    return 0


class GanttDataView(ProjectAccessMixin, View):
    """JSON endpoint — task data for the Gantt chart and Simulate tab.

    Supports optional pagination via ?page=<n>&page_size=<n> (default page_size 200).
    Paginated responses include total_count, total_pages, page, and has_more fields.
    Without ?page the full task list is returned for backward compatibility.
    """

    _DEFAULT_PAGE_SIZE = 200
    _MAX_PAGE_SIZE = 1000

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.governance.reader import BindingGovernanceReader

        from .services.link_resolver import link_status_for_task

        project = self.get_project()
        qs = Task.objects.filter(project=project, is_non_physical=False).order_by(
            "start_date", "activity_code"
        )

        page_str = request.GET.get("page")
        if page_str is not None:
            try:
                page = max(1, int(page_str))
            except (ValueError, TypeError):
                page = 1
            try:
                page_size = max(
                    1,
                    min(
                        self._MAX_PAGE_SIZE,
                        int(request.GET.get("page_size", self._DEFAULT_PAGE_SIZE)),
                    ),
                )
            except (ValueError, TypeError):
                page_size = self._DEFAULT_PAGE_SIZE

            total_count = qs.count()
            total_pages = max(1, math.ceil(total_count / page_size))
            offset = (page - 1) * page_size
            tasks = list(qs[offset : offset + page_size])
            pagination = {
                "total_count": total_count,
                "total_pages": total_pages,
                "page": page,
                "has_more": page < total_pages,
            }
        else:
            tasks = list(qs)
            pagination = None

        task_pks = [t.pk for t in tasks]
        reader = BindingGovernanceReader(project.pk)
        trusted_map = reader.entity_gids_by_task(task_pks, trusted_only=True)
        review_map = reader.entity_gids_by_task(task_pks, review_only=True)
        data = []
        for task in tasks:
            tid = str(task.pk)
            trusted_gids = trusted_map.get(tid, [])
            review_gids = review_map.get(tid, [])
            data.append(
                {
                    "id": tid,
                    "name": task.name,
                    "start": task.start_date.isoformat(),
                    "end": task.end_date.isoformat(),
                    "actual_start": task.actual_start.isoformat() if task.actual_start else None,
                    "actual_end": task.actual_end.isoformat() if task.actual_end else None,
                    "stage": task.stage or "",
                    "sub_stage": task.sub_stage or "",
                    "is_critical": task.is_critical,
                    "total_float": task.total_float,
                    "activity_code": task.activity_code or "",
                    "status": task.status,
                    "link_status": link_status_for_task(task, trusted_gids + review_gids),
                    "trusted_entity_global_ids": trusted_gids,
                    "review_entity_global_ids": review_gids,
                    "trusted_entity_count": len(trusted_gids),
                    "review_entity_count": len(review_gids),
                    "entity_global_ids": trusted_gids,
                }
            )

        response = {"tasks": data}
        if pagination is not None:
            response.update(pagination)
        return JsonResponse(response)


class TaskDetailView(ProjectAccessMixin, View):
    """HTMX GET — task detail side panel for the Gantt chart."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.reader import BindingGovernanceReader

        project = self.get_project()
        task = get_object_or_404(Task, pk=kwargs["task_pk"], project=project)
        reader = BindingGovernanceReader(project.pk)
        trusted_gids = reader.trusted_entity_gids_for_task(task.pk)
        review_gids = reader.review_entity_gids_for_task(task.pk)
        ifc_files = IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
        trusted_entities = list(
            IFCEntity.objects.filter(ifc_file__in=ifc_files, global_id__in=trusted_gids).only(
                "global_id", "name", "ifc_type", "properties"
            )
        )
        review_entities = list(
            IFCEntity.objects.filter(ifc_file__in=ifc_files, global_id__in=review_gids).only(
                "global_id", "name", "ifc_type", "properties"
            )
        )
        property_hints = []
        for entity in (
            IFCEntity.objects.filter(ifc_file__in=ifc_files)
            .only("global_id", "name", "ifc_type", "properties")
            .iterator(chunk_size=200)
        ):
            if entity.global_id in trusted_gids or entity.global_id in review_gids:
                continue
            act_id = None
            for key, value in (entity.properties or {}).items():
                if value and key.lower().endswith("activity id"):
                    act_id = str(value).strip()
                    break
            if act_id:
                property_hints.append(
                    {
                        "global_id": entity.global_id,
                        "name": entity.name or entity.global_id,
                        "ifc_type": entity.ifc_type,
                        "activity_id": act_id,
                    }
                )
                if len(property_hints) >= 20:
                    break

        today = date.today()
        progress = _compute_progress(task, today)

        siblings_count = (
            TaskEntityBinding.objects.filter(
                entity_global_id__in=trusted_gids,
                task__project=project,
                needs_review=False,
            )
            .exclude(task_id=task.pk)
            .values("task_id")
            .distinct()
            .count()
            if trusted_gids
            else 0
        )

        return render(
            request,
            "scheduling/components/task_detail.html",
            {
                "task": task,
                "entities": trusted_entities,
                "trusted_entities": trusted_entities,
                "review_entities": review_entities,
                "property_hints": property_hints,
                "trusted_count": len(trusted_gids),
                "review_count": len(review_gids),
                "progress": progress,
                "siblings_count": siblings_count,
                "stage_color": _STAGE_COLORS.get(task.stage or "", "#6b7280"),
                "entity_global_ids_json": json.dumps(trusted_gids),
                "project": project,
            },
        )


class CriticalPathView(ProjectModifyAccessMixin, View):
    """POST — run CPM for the project and return JSON results."""

    def post(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        try:
            result = compute_critical_path(str(project.pk))
        except Exception as exc:
            logger.exception("CPM failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class EVMDataView(ProjectAccessMixin, View):
    """JSON — EVM metrics and S-curve series for the EVM Dashboard tab."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from .services.utils import get_project_data_date

        project = self.get_project()
        try:
            result = compute_evm(str(project.pk))
            _, is_real = get_project_data_date(str(project.pk))
            result["data_date_is_real"] = is_real
        except Exception as exc:
            logger.exception("EVM failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class ScheduleIntelligenceView(ProjectAccessMixin, View):
    """JSON — Schedule intelligence: CPM summary, Earned Schedule, WBS risk scores."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from .services.evm_engine import compute_schedule_intelligence

        project = self.get_project()
        try:
            data = compute_schedule_intelligence(str(project.pk))
        except Exception as exc:
            logger.exception("Schedule intelligence failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(data)


class ScheduleChatView(ProjectAccessMixin, View):
    """JSON — Project Controls chat: POST {message} → {response}.

    Embeds live EVM + schedule intelligence in the system prompt and calls
    the configured LLM (claude-sonnet-4-6 when ASK_PROVIDER=anthropic).
    """

    def post(self, request, **kwargs: object) -> JsonResponse:
        from .services.controls_chat import ProjectControlsChatService

        project = self.get_project()
        try:
            body = json.loads(request.body or b"{}")
            message = str(body.get("message", "")).strip()
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not message:
            return JsonResponse({"error": "message is required."}, status=400)

        svc = ProjectControlsChatService(project, request.user)
        result = svc.ask(message)

        if result.get("error") and not result.get("response"):
            return JsonResponse(result, status=500)
        return JsonResponse(result)


class WBSHeatmapView(ProjectAccessMixin, View):
    """JSON — per-stage performance metrics for the WBS heatmap."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        try:
            from .services.evm import compute_wbs_heatmap

            stages = compute_wbs_heatmap(str(project.pk))
        except Exception as exc:
            logger.exception("WBS heatmap failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse({"has_data": bool(stages), "stages": stages or []})


class DelayDistributionView(ProjectAccessMixin, View):
    """JSON — delay bucket distribution for the Delay Distribution chart."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        try:
            from .services.evm import compute_delay_distribution

            result = compute_delay_distribution(str(project.pk))
        except Exception as exc:
            logger.exception("Delay distribution failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class MonteCarloView(ProjectAccessMixin, View):
    """JSON — Monte Carlo schedule simulation: P50/P80/P95 completion dates."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        try:
            from .services.monte_carlo import compute_monte_carlo

            result = compute_monte_carlo(str(project.pk))
        except Exception as exc:
            logger.exception("Monte Carlo failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class TrendAnalysisView(ProjectAccessMixin, View):
    """JSON — SPI/CPI historical trends, TCPI, and Schedule Recovery Index."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        try:
            from .services.trend_engine import compute_trend_analysis

            result = compute_trend_analysis(str(project.pk))
        except Exception as exc:
            logger.exception("Trend analysis failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class CompletionMLView(ProjectAccessMixin, View):
    """JSON — Completion Probability (ML): logistic regression on completed tasks."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from .services.completion_ml import run_completion_ml

        project = self.get_project()
        try:
            result = run_completion_ml(str(project.pk))
        except Exception as exc:
            logger.exception("Completion ML failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class TimeLocationView(ProjectAccessMixin, View):
    """JSON — Time-Location (flowline) chart data: floor-located tasks only."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from .services.timelocation import compute_timelocation
        from .services.trade_resolver import load_override_map

        project = self.get_project()
        override_map = (
            load_override_map(str(project.pk))
            if request.GET.get("audit_view") == "corrected"
            else None
        )
        try:
            result = compute_timelocation(str(project.pk), override_map=override_map)
        except Exception as exc:
            logger.exception("Timelocation failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class ScheduleAuditSectionMismatchView(ProjectAccessMixin, View):
    """JSON — Schedule Audit Layer 1: activity-name vs CSI-code section mismatches."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from .services.schedule_audit import run_section_mismatch_audit
        from .services.trade_resolver import save_override_map

        project = self.get_project()
        try:
            result = run_section_mismatch_audit(str(project.pk), user=request.user)
            # Cache the confirmed override map so consumers can use ?audit_view=corrected
            if result.get("has_data"):
                save_override_map(str(project.pk), result)
        except Exception as exc:
            logger.exception("Section mismatch audit failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class DelayRootCauseView(ProjectAccessMixin, View):
    """JSON — delay root-cause clustering via CPM driving-relationship propagation."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from .services.delay_rootcause import run_delay_rootcause
        from .services.trade_resolver import load_override_map

        project = self.get_project()
        threshold = int(request.GET.get("threshold", 0))
        override_map = (
            load_override_map(str(project.pk))
            if request.GET.get("audit_view") == "corrected"
            else None
        )
        try:
            result = run_delay_rootcause(
                str(project.pk),
                delay_threshold_days=threshold,
                override_map=override_map,
            )
        except Exception as exc:
            logger.exception("Delay root-cause failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class CashFlowView(ProjectAccessMixin, View):
    """JSON — monthly cash flow forecast: planned / actual / remaining."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from .services.cashflow import compute_cashflow

        project = self.get_project()
        try:
            result = compute_cashflow(str(project.pk))
        except Exception as exc:
            logger.exception("Cashflow failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class DCMACheckView(ProjectAccessMixin, View):
    """JSON — DCMA 14-point schedule quality assessment."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from .services.dcma_check import run_dcma_check

        project = self.get_project()
        try:
            result = run_dcma_check(str(project.pk))
        except Exception as exc:
            logger.exception("DCMA check failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class AnomalyDetectionView(ProjectAccessMixin, View):
    """JSON — single-snapshot anomaly detection: stall, cross-sectional outliers, logic errors."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from .services.anomaly_detect import detect_anomalies
        from .services.trade_resolver import load_override_map

        project = self.get_project()
        override_map = (
            load_override_map(str(project.pk))
            if request.GET.get("audit_view") == "corrected"
            else None
        )
        try:
            result = detect_anomalies(str(project.pk), override_map=override_map)
        except Exception as exc:
            logger.exception("Anomaly detection failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class ScheduleReportView(ProjectAccessMixin, View):
    """GET — download a PDF or DOCX schedule report.

    Query params:
        format   pdf | docx   (default: pdf)
        sections comma-separated section keys (default: all)
        type     executive    (default: executive; reserved for future weekly/daily)
    """

    def get(self, request, **kwargs: object):
        from django.http import HttpResponse

        from .services.report_generator import ALL_SECTIONS, generate_report

        project = self.get_project()
        fmt = request.GET.get("format", "pdf").lower()
        report_type = request.GET.get("type", "executive")

        raw_sections = request.GET.get("sections", "")
        if raw_sections:
            sections_list: list[str] | None = [
                s.strip() for s in raw_sections.split(",") if s.strip() in ALL_SECTIONS
            ]
            if not sections_list:
                sections_list = None
        else:
            sections_list = None

        if fmt not in ("pdf", "docx"):
            return HttpResponse("Invalid format. Use pdf or docx.", status=400)

        try:
            content, filename = generate_report(
                str(project.pk),
                fmt=fmt,
                report_type=report_type,
                sections=sections_list,
            )
        except Exception as exc:
            logger.exception("Report generation failed for project %s", project.pk)
            return HttpResponse(f"Report generation failed: {exc}", status=500)

        content_type = (
            "application/pdf"
            if fmt == "pdf"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class DecisionSummaryView(ProjectAccessMixin, View):
    """JSON — executive plain-language decision summary (three blocks)."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from .services.decision_layer import compute_decision_summary

        project = self.get_project()
        try:
            result = compute_decision_summary(str(project.pk))
        except Exception as exc:
            logger.exception("Decision summary failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class FloorHealthView(ProjectAccessMixin, View):
    """JSON — per-floor Project Health Matrix: Build Quality + Project Status."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from .services.floor_health import compute_floor_health
        from .services.trade_resolver import load_override_map

        project = self.get_project()
        override_map = (
            load_override_map(str(project.pk))
            if request.GET.get("audit_view") == "corrected"
            else None
        )
        try:
            result = compute_floor_health(str(project.pk), override_map=override_map)
        except Exception as exc:
            logger.exception("Floor health failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(result)


class LinkGovernanceSummaryView(ProjectAccessMixin, View):
    """GET — read-only trusted link governance summary for one project."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.governance.summary import GovernanceSummaryService

        project = self.get_project()
        payload = GovernanceSummaryService(str(project.pk)).build()
        return JsonResponse(payload)


class LinkGovernanceReviewQueueView(ProjectAccessMixin, View):
    """GET — paginated read-only link governance review queue (JSON or HTMX)."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.review_queue import LinkReviewQueueService

        project = self.get_project()
        filters = LinkReviewQueueService.filters_from_request(request.GET.dict())
        service = LinkReviewQueueService(str(project.pk), project_pk=project.pk)
        payload = service.build(filters)

        if request.headers.get("HX-Request"):
            queue_modes = [
                ("review", "Review"),
                ("trusted", "Trusted"),
                ("property_hints", "Property hints"),
                ("legacy_only", "Legacy M2M"),
                ("multiple_trusted", "Multi-trusted"),
                ("possible_conflicts", "Conflicts"),
                ("all_governance", "All"),
            ]
            return render(
                request,
                "scheduling/components/governance_review_queue.html",
                {
                    "project": project,
                    "queue": payload,
                    "filters": filters,
                    "queue_modes": queue_modes,
                    "governance_capabilities": _governance_capabilities_context(
                        project, request.user
                    ),
                },
            )
        return JsonResponse(payload)


class LinkGovernanceTaskView(ProjectAccessMixin, View):
    """GET — task-centric governance read model."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.governance.review_queue import LinkReviewQueueService

        project = self.get_project()
        service = LinkReviewQueueService(str(project.pk), project_pk=project.pk)
        payload = service.task_centric(kwargs["task_pk"])
        return JsonResponse(payload)


class LinkGovernanceEntityView(ProjectAccessMixin, View):
    """GET — entity-centric governance read model by GlobalId."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.services.governance.review_queue import LinkReviewQueueService

        project = self.get_project()
        service = LinkReviewQueueService(str(project.pk), project_pk=project.pk)
        payload = service.entity_centric(kwargs["global_id"])
        return JsonResponse(payload)


class LinkGovernanceWorkspaceView(ProjectAccessMixin, View):
    """GET — HTMX shell for link governance review workspace."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.authority import GovernanceAuthorityPolicy

        project = self.get_project()
        capabilities = GovernanceAuthorityPolicy(project, request.user).capabilities_summary()
        return render(
            request,
            "scheduling/tabs/link_governance.html",
            {"project": project, "governance_capabilities": capabilities},
        )


def _parse_json_or_form(request) -> dict | None:
    """Parse POST body as JSON or form fields."""
    if request.content_type.startswith("application/json"):
        try:
            return json.loads(request.body.decode() or "{}")
        except json.JSONDecodeError:
            return None
    data = request.POST.dict()
    if "binding_ids" not in data and request.POST.getlist("binding_ids"):
        data["binding_ids"] = request.POST.getlist("binding_ids")
    return data


def _parse_binding_ids(payload: dict) -> list[str]:
    """Extract binding UUID strings from request payload."""
    raw = payload.get("binding_ids") or payload.get("binding_id")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    return [str(x).strip() for x in raw if str(x).strip()]


def _parse_parity_items_from_request(request) -> list[dict[str, str]]:
    """Build parity repair item dicts from HTMX form selection."""
    items: list[dict[str, str]] = []
    for bid in request.POST.getlist("binding_ids"):
        repair_type = request.POST.get(f"repair_type_{bid}", "")
        if bid and repair_type:
            items.append({"binding_id": bid, "repair_type": repair_type})
    for row_key in request.POST.getlist("parity_row"):
        repair_type = request.POST.get(f"repair_type_row_{row_key}", "")
        task_id = request.POST.get(f"task_id_row_{row_key}", "")
        entity_gid = request.POST.get(f"entity_gid_row_{row_key}", "")
        if repair_type and task_id and entity_gid:
            items.append(
                {
                    "task_id": task_id,
                    "entity_global_id": entity_gid,
                    "repair_type": repair_type,
                }
            )
    return items


def _decision_error(
    request,
    message: str,
    *,
    status: int = 400,
    details: dict | None = None,
) -> HttpResponse:
    """Return JSON or HTMX toast for decision validation errors."""
    body = {"error": message, **(details or {})}
    if request.headers.get("HX-Request"):
        return toast_response(message, "error", status=status)
    return JsonResponse(body, status=status)


def _governance_capabilities_context(project, user) -> dict:
    from scheduling.services.governance.authority import GovernanceAuthorityPolicy

    return GovernanceAuthorityPolicy(project, user).capabilities_summary()


class LinkDecisionPreviewOneView(ProjectModifyAccessMixin, View):
    """POST — preview individual binding approval with fingerprint."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.link_decision import (
            DecisionValidationError,
            LinkDecisionService,
        )

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        service = LinkDecisionService(project, request.user)
        try:
            preview = service.preview_one(kwargs["binding_pk"])
        except DecisionValidationError as exc:
            return _decision_error(request, exc.message, status=400, details=exc.details)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_decision_confirm_one.html",
                {
                    "project": project,
                    "preview": preview,
                    "binding_id": kwargs["binding_pk"],
                    "queue_mode": payload.get("queue_mode", "review"),
                    "queue_page": payload.get("queue_page", 1),
                },
            )
        return JsonResponse(preview.to_dict())


class LinkDecisionApplyOneView(ProjectModifyAccessMixin, View):
    """POST — apply individual binding approval."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.link_decision import (
            DecisionValidationError,
            LinkDecisionService,
            StaleDecisionError,
        )
        from scheduling.services.governance.review_queue import LinkReviewQueueService

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        fingerprint = payload.get("selection_fingerprint", "")
        conflict_ack = payload.get("conflict_acknowledged") in (True, "true", "1", "on")

        service = LinkDecisionService(project, request.user)
        try:
            result = service.approve_one(
                kwargs["binding_pk"],
                selection_fingerprint=fingerprint,
                conflict_acknowledged=conflict_ack,
            )
        except StaleDecisionError as exc:
            return _decision_error(request, exc.message, status=409, details=exc.details)
        except GovernanceAuthorityError as exc:
            return _decision_error(
                request, exc.result.reason, status=403, details=exc.result.to_dict()
            )
        except DecisionValidationError as exc:
            status = 422 if "acknowledgment" in exc.message.lower() else 400
            return _decision_error(request, exc.message, status=status, details=exc.details)
        except GovernanceAuthorityError as exc:
            return _decision_error(
                request, exc.result.reason, status=403, details=exc.result.to_dict()
            )

        mode = payload.get("queue_mode", "review")
        page = int(payload.get("queue_page", 1) or 1)
        filters = LinkReviewQueueService.filters_from_request({"mode": mode, "page": page})
        queue = LinkReviewQueueService(str(project.pk), project_pk=project.pk).build(filters)
        queue_modes = [
            ("review", "Review"),
            ("trusted", "Trusted"),
            ("property_hints", "Property hints"),
            ("legacy_only", "Legacy M2M"),
            ("multiple_trusted", "Multi-trusted"),
            ("possible_conflicts", "Conflicts"),
            ("all_governance", "All"),
        ]
        queue_html = render_to_string(
            "scheduling/components/governance_review_queue.html",
            {
                "project": project,
                "queue": queue,
                "filters": filters,
                "queue_modes": queue_modes,
                "governance_capabilities": _governance_capabilities_context(project, request.user),
            },
            request=request,
        )
        result_html = render_to_string(
            "scheduling/components/governance_decision_result.html",
            {"result": result, "project": project},
            request=request,
        )
        msg = (
            f"Approved {result.promoted_count}, "
            f"{result.noop_count} already trusted, "
            f"{result.m2m_additions} M2M added."
        )
        response = HttpResponse(
            result_html
            + f'<div id="governance-queue-panel" hx-swap-oob="innerHTML">{queue_html}</div>'
        )
        return trigger_toast(response, msg, "success")


class LinkDecisionBulkPreviewView(GovernanceCapabilityMixin, View):
    """POST — preview selected bulk approval."""

    governance_capability = GovernanceCapability.APPROVE_BULK

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.link_decision import (
            BULK_UI_MAX,
            DecisionValidationError,
            LinkDecisionService,
        )

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        binding_ids = _parse_binding_ids(payload)
        if not binding_ids:
            return _decision_error(request, "At least one binding must be selected.", status=400)
        if len(binding_ids) > BULK_UI_MAX:
            return _decision_error(
                request,
                f"Selection exceeds UI maximum of {BULK_UI_MAX} items.",
                status=400,
                details={"max": BULK_UI_MAX, "requested": len(binding_ids)},
            )

        service = LinkDecisionService(project, request.user)
        try:
            preview = service.preview_selected(binding_ids)
        except DecisionValidationError as exc:
            return _decision_error(request, exc.message, status=400, details=exc.details)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_bulk_preview.html",
                {
                    "project": project,
                    "preview": preview,
                    "binding_ids": binding_ids,
                    "queue_mode": payload.get("queue_mode", "review"),
                    "queue_page": payload.get("queue_page", 1),
                },
            )
        return JsonResponse(preview.to_dict())


class LinkDecisionBulkApplyView(GovernanceCapabilityMixin, View):
    """POST — apply selected bulk approval (all-or-nothing)."""

    governance_capability = GovernanceCapability.APPROVE_BULK

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.link_decision import (
            DecisionValidationError,
            LinkDecisionService,
            StaleDecisionError,
        )
        from scheduling.services.governance.review_queue import LinkReviewQueueService

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        binding_ids = _parse_binding_ids(payload)
        fingerprint = payload.get("selection_fingerprint", "")
        confirmation = payload.get("confirmation", "")
        confirm_ack = payload.get("confirm_acknowledged") in (True, "true", "1", "on")
        conflict_ack = payload.get("conflict_acknowledged") in (True, "true", "1", "on")

        service = LinkDecisionService(project, request.user)
        try:
            result = service.approve_selected(
                binding_ids,
                selection_fingerprint=fingerprint,
                confirmation=confirmation,
                confirm_acknowledged=confirm_ack,
                conflict_acknowledged=conflict_ack,
                require_bulk_phrase=True,
            )
        except StaleDecisionError as exc:
            return _decision_error(request, exc.message, status=409, details=exc.details)
        except DecisionValidationError as exc:
            status = 422 if "acknowledgment" in exc.message.lower() else 400
            return _decision_error(request, exc.message, status=status, details=exc.details)
        except GovernanceAuthorityError as exc:
            return _decision_error(
                request, exc.result.reason, status=403, details=exc.result.to_dict()
            )

        mode = payload.get("queue_mode", "review")
        page = int(payload.get("queue_page", 1) or 1)
        filters = LinkReviewQueueService.filters_from_request({"mode": mode, "page": page})
        queue = LinkReviewQueueService(str(project.pk), project_pk=project.pk).build(filters)
        queue_modes = [
            ("review", "Review"),
            ("trusted", "Trusted"),
            ("property_hints", "Property hints"),
            ("legacy_only", "Legacy M2M"),
            ("multiple_trusted", "Multi-trusted"),
            ("possible_conflicts", "Conflicts"),
            ("all_governance", "All"),
        ]
        queue_html = render_to_string(
            "scheduling/components/governance_review_queue.html",
            {
                "project": project,
                "queue": queue,
                "filters": filters,
                "queue_modes": queue_modes,
                "governance_capabilities": _governance_capabilities_context(project, request.user),
            },
            request=request,
        )
        result_html = render_to_string(
            "scheduling/components/governance_decision_result.html",
            {"result": result, "project": project, "bulk": True},
            request=request,
        )
        msg = (
            f"Bulk approved {result.promoted_count}, "
            f"{result.noop_count} no-op, "
            f"{result.m2m_additions} M2M added."
        )
        response = HttpResponse(
            result_html
            + f'<div id="governance-queue-panel" hx-swap-oob="innerHTML">{queue_html}</div>'
        )
        return trigger_toast(response, msg, "success")


class LinkGovernanceReconciliationView(ProjectAccessMixin, View):
    """GET — read-only binding reconciliation diagnostic (JSON or HTMX)."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_reconciliation import (
            BindingReconciliationService,
        )

        project = self.get_project()
        filters = BindingReconciliationService.filters_from_request(request.GET.dict())
        payload = BindingReconciliationService(str(project.pk), project_pk=project.pk).build(
            filters
        )

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_reconciliation_panel.html",
                {
                    "project": project,
                    "reconciliation": payload,
                    "filters": filters,
                    "governance_capabilities": _governance_capabilities_context(
                        project, request.user
                    ),
                },
            )
        return JsonResponse(payload)


class LinkGovernanceReconciliationDetailView(ProjectAccessMixin, View):
    """GET — read-only reconciliation detail for one binding."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_reconciliation import (
            BindingReconciliationService,
        )

        project = self.get_project()
        payload = BindingReconciliationService(
            str(project.pk), project_pk=project.pk
        ).binding_detail(kwargs["binding_pk"])
        if "error" in payload:
            return JsonResponse(payload, status=404)
        return JsonResponse(payload)


def _lifecycle_error(
    request,
    message: str,
    *,
    status: int = 400,
    details: dict | None = None,
) -> HttpResponse:
    """Return JSON or HTMX toast for lifecycle validation errors."""
    body = {"error": message, **(details or {})}
    if request.headers.get("HX-Request"):
        return toast_response(message, "error", status=status)
    return JsonResponse(body, status=status)


def _lifecycle_error_from_exc(request, exc: Exception) -> HttpResponse:
    if isinstance(exc, GovernanceAuthorityError):
        return _lifecycle_error(
            request, exc.result.reason, status=403, details=exc.result.to_dict()
        )
    from scheduling.services.governance.binding_lifecycle import (
        LifecycleValidationError,
        StaleLifecycleError,
    )

    if isinstance(exc, StaleLifecycleError):
        return _lifecycle_error(request, exc.message, status=409, details=exc.details)
    if isinstance(exc, LifecycleValidationError):
        return _lifecycle_error(request, exc.message, status=400, details=exc.details)
    raise exc


class LinkLifecycleRejectPreviewView(ProjectModifyAccessMixin, View):
    """POST — preview rejection of an active review binding."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
            LifecycleValidationError,
        )

        project = self.get_project()
        service = BindingLifecycleService(project, request.user)
        try:
            preview = service.preview_reject(str(kwargs["binding_pk"]))
        except LifecycleValidationError as exc:
            return _lifecycle_error(request, exc.message, status=400, details=exc.details)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_lifecycle_confirm.html",
                {
                    "project": project,
                    "preview": preview,
                    "operation": "reject",
                    "binding_id": kwargs["binding_pk"],
                    "reason_codes": [
                        ("wrong_task", "Wrong task"),
                        ("wrong_entity", "Wrong entity"),
                        ("wrong_location", "Wrong location"),
                        ("wrong_discipline", "Wrong discipline"),
                        ("wrong_type", "Wrong type"),
                        ("duplicate", "Duplicate"),
                        ("insufficient_evidence", "Insufficient evidence"),
                        ("obsolete_suggestion", "Obsolete suggestion"),
                        ("other", "Other"),
                    ],
                },
            )
        return JsonResponse(preview.to_dict())


class LinkLifecycleRejectApplyView(ProjectModifyAccessMixin, View):
    """POST — apply audited rejection."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
            LifecycleValidationError,
            StaleLifecycleError,
        )
        from scheduling.services.governance.review_queue import LinkReviewQueueService

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        service = BindingLifecycleService(project, request.user)
        try:
            result = service.reject(
                str(kwargs["binding_pk"]),
                fingerprint=payload.get("fingerprint", ""),
                reason_code=payload.get("reason_code", ""),
                reason_text=payload.get("reason_text", ""),
            )
        except StaleLifecycleError as exc:
            return _lifecycle_error(request, exc.message, status=409, details=exc.details)
        except LifecycleValidationError as exc:
            return _lifecycle_error(request, exc.message, status=400, details=exc.details)

        if request.headers.get("HX-Request"):
            queue_svc = LinkReviewQueueService(project.pk, project_pk=project.pk)
            queue = queue_svc.build(queue_svc.filters_from_request({"mode": "review", "page": 1}))
            queue_html = render_to_string(
                "scheduling/components/governance_review_queue.html",
                {
                    "project": project,
                    "queue": queue,
                    "filters": queue_svc.filters_from_request({"mode": "review", "page": 1}),
                    "queue_modes": [("review", "Review"), ("trusted", "Trusted")],
                    "governance_capabilities": _governance_capabilities_context(
                        project, request.user
                    ),
                },
                request=request,
            )
            result_html = render_to_string(
                "scheduling/components/governance_lifecycle_result.html",
                {"project": project, "result": result, "operation": "reject"},
                request=request,
            )
            return HttpResponse(
                result_html
                + f'<div id="governance-queue-panel" hx-swap-oob="innerHTML">{queue_html}</div>'
            )
        return JsonResponse(result.to_dict())


class LinkLifecycleReaffirmPreviewView(ProjectModifyAccessMixin, View):
    """POST — preview reaffirmation of a trusted binding."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
            LifecycleValidationError,
        )

        project = self.get_project()
        service = BindingLifecycleService(project, request.user)
        try:
            preview = service.preview_reaffirm(str(kwargs["binding_pk"]))
        except LifecycleValidationError as exc:
            return _lifecycle_error(request, exc.message, status=400, details=exc.details)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_lifecycle_confirm.html",
                {
                    "project": project,
                    "preview": preview,
                    "operation": "reaffirm",
                    "binding_id": kwargs["binding_pk"],
                    "reason_codes": [
                        ("evidence_verified", "Evidence verified"),
                        ("manual_override_confirmed", "Manual override confirmed"),
                        ("source_change_reviewed", "Source change reviewed"),
                        ("reconciliation_false_positive", "Reconciliation false positive"),
                        ("other", "Other"),
                    ],
                },
            )
        return JsonResponse(preview.to_dict())


class LinkLifecycleReaffirmApplyView(ProjectModifyAccessMixin, View):
    """POST — apply reaffirmation."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
            LifecycleValidationError,
            StaleLifecycleError,
        )

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        service = BindingLifecycleService(project, request.user)
        try:
            result = service.reaffirm(
                str(kwargs["binding_pk"]),
                fingerprint=payload.get("fingerprint", ""),
                reason_code=payload.get("reason_code", ""),
                reason_text=payload.get("reason_text", ""),
                repair_m2m=payload.get("repair_m2m") in (True, "true", "1", "on"),
            )
        except StaleLifecycleError as exc:
            return _lifecycle_error(request, exc.message, status=409, details=exc.details)
        except LifecycleValidationError as exc:
            return _lifecycle_error(request, exc.message, status=400, details=exc.details)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_lifecycle_result.html",
                {"project": project, "result": result, "operation": "reaffirm"},
            )
        return JsonResponse(result.to_dict())


class LinkLifecycleReversePreviewView(GovernanceCapabilityMixin, View):
    """POST — preview trusted binding reversal."""

    governance_capability = GovernanceCapability.REVERSE

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
            LifecycleValidationError,
        )

        project = self.get_project()
        service = BindingLifecycleService(project, request.user)
        try:
            preview = service.preview_reverse(str(kwargs["binding_pk"]))
        except LifecycleValidationError as exc:
            return _lifecycle_error(request, exc.message, status=400, details=exc.details)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_lifecycle_confirm.html",
                {
                    "project": project,
                    "preview": preview,
                    "operation": "reverse",
                    "binding_id": kwargs["binding_pk"],
                    "confirm_phrase": "REVERSE TRUSTED LINK",
                    "reason_codes": [
                        ("mistaken_approval", "Mistaken approval"),
                        ("source_changed", "Source changed"),
                        ("task_removed", "Task removed"),
                        ("entity_removed", "Entity removed"),
                        ("scope_changed", "Scope changed"),
                        ("governance_correction", "Governance correction"),
                        ("other", "Other"),
                    ],
                },
            )
        return JsonResponse(preview.to_dict())


class LinkLifecycleReverseApplyView(GovernanceCapabilityMixin, View):
    """POST — apply audited reversal."""

    governance_capability = GovernanceCapability.REVERSE

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
            LifecycleValidationError,
            StaleLifecycleError,
        )

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        service = BindingLifecycleService(project, request.user)
        try:
            result = service.reverse(
                str(kwargs["binding_pk"]),
                fingerprint=payload.get("fingerprint", ""),
                reason_code=payload.get("reason_code", ""),
                reason_text=payload.get("reason_text", ""),
                confirmation=payload.get("confirmation", ""),
            )
        except StaleLifecycleError as exc:
            return _lifecycle_error(request, exc.message, status=409, details=exc.details)
        except LifecycleValidationError as exc:
            return _lifecycle_error(request, exc.message, status=400, details=exc.details)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_lifecycle_result.html",
                {"project": project, "result": result, "operation": "reverse"},
            )
        return JsonResponse(result.to_dict())


class LinkLifecycleSupersedePreviewView(GovernanceCapabilityMixin, View):
    """POST — preview supersession of trusted binding by review replacement."""

    governance_capability = GovernanceCapability.SUPERSEDE

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
            LifecycleValidationError,
        )

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        replacement_id = payload.get("replacement_binding_id", "")
        service = BindingLifecycleService(project, request.user)
        try:
            preview = service.preview_supersede(str(kwargs["binding_pk"]), str(replacement_id))
        except LifecycleValidationError as exc:
            return _lifecycle_error(request, exc.message, status=400, details=exc.details)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_lifecycle_confirm.html",
                {
                    "project": project,
                    "preview": preview,
                    "operation": "supersede",
                    "binding_id": kwargs["binding_pk"],
                    "replacement_binding_id": replacement_id,
                    "confirm_phrase": "SUPERSEDE LINK",
                    "reason_codes": [
                        ("mistaken_approval", "Mistaken approval"),
                        ("source_changed", "Source changed"),
                        ("scope_changed", "Scope changed"),
                        ("governance_correction", "Governance correction"),
                        ("other", "Other"),
                    ],
                },
            )
        return JsonResponse(preview.to_dict())


class LinkLifecycleSupersedeApplyView(GovernanceCapabilityMixin, View):
    """POST — apply atomic supersession."""

    governance_capability = GovernanceCapability.SUPERSEDE

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
            LifecycleValidationError,
            StaleLifecycleError,
        )

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        service = BindingLifecycleService(project, request.user)
        try:
            result = service.supersede(
                str(kwargs["binding_pk"]),
                str(payload.get("replacement_binding_id", "")),
                fingerprint=payload.get("fingerprint", ""),
                reason_code=payload.get("reason_code", ""),
                reason_text=payload.get("reason_text", ""),
                confirmation=payload.get("confirmation", ""),
            )
        except StaleLifecycleError as exc:
            return _lifecycle_error(request, exc.message, status=409, details=exc.details)
        except LifecycleValidationError as exc:
            return _lifecycle_error(request, exc.message, status=400, details=exc.details)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_lifecycle_result.html",
                {"project": project, "result": result, "operation": "supersede"},
            )
        return JsonResponse(result.to_dict())


class LinkLifecycleSupersedePairView(GovernanceCapabilityMixin, View):
    """GET — supersede pairing form with eligible review replacements."""

    governance_capability = GovernanceCapability.SUPERSEDE

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.review_queue import LinkReviewQueueService

        project = self.get_project()
        service = LinkReviewQueueService(str(project.pk), project_pk=project.pk)
        candidates = service.supersede_replacement_candidates(kwargs["binding_pk"])
        return render(
            request,
            "scheduling/components/governance_supersede_pair.html",
            {
                "project": project,
                "binding_id": kwargs["binding_pk"],
                "candidates": candidates,
            },
        )


class LinkLifecycleParityBulkPreviewView(GovernanceCapabilityMixin, View):
    """POST — preview selected parity repairs."""

    governance_capability = GovernanceCapability.REPAIR_M2M_ADD

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
            LifecycleValidationError,
        )

        project = self.get_project()
        items = _parse_parity_items_from_request(request)
        if not items:
            payload = _parse_json_or_form(request) or {}
            items = payload.get("parity_items") or payload.get("items") or []
            if isinstance(items, dict):
                items = [items]
        service = BindingLifecycleService(project, request.user)
        try:
            preview = service.preview_parity_selected(items)
        except LifecycleValidationError as exc:
            return _lifecycle_error(request, exc.message, status=400)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_lifecycle_confirm.html",
                {
                    "project": project,
                    "preview": preview,
                    "operation": "parity_bulk",
                    "confirm_phrase": "PARITY REPAIR",
                    "reason_codes": [
                        ("accepted_missing_m2m", "Accepted missing M2M"),
                        ("m2m_without_accepted", "M2M without accepted binding"),
                        ("review_m2m_leak", "Review M2M leak"),
                        ("other", "Other"),
                    ],
                    "parity_items_json": json.dumps(items),
                },
            )
        return JsonResponse(preview)


class LinkLifecycleParityBulkApplyView(GovernanceCapabilityMixin, View):
    """POST — apply selected parity repairs atomically."""

    governance_capability = GovernanceCapability.REPAIR_M2M_ADD

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
            LifecycleValidationError,
            StaleLifecycleError,
        )

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        items = _parse_parity_items_from_request(request)
        if not items:
            items = payload.get("parity_items") or []
            if isinstance(items, str):
                items = json.loads(items or "[]")
        service = BindingLifecycleService(project, request.user)
        try:
            result = service.repair_parity_selected(
                items,
                fingerprint=payload.get("fingerprint", ""),
                reason_code=payload.get("reason_code", ""),
                reason_text=payload.get("reason_text", ""),
                confirmation=payload.get("confirmation", ""),
            )
        except StaleLifecycleError as exc:
            return _lifecycle_error(request, exc.message, status=409, details=exc.details)
        except LifecycleValidationError as exc:
            return _lifecycle_error(request, exc.message, status=400)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_lifecycle_result.html",
                {"project": project, "result": result, "operation": "parity"},
            )
        return JsonResponse(result.to_dict())


class LinkLifecycleParityPreviewView(ProjectAccessMixin, View):
    """POST — preview audited M2M parity repair."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        from django.core.exceptions import PermissionDenied

        from scheduling.services.governance.authority import (
            GovernanceAuthorityError,
            GovernanceAuthorityPolicy,
            require_parity_repair_authority,
        )
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
            LifecycleValidationError,
        )

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        repair_type = payload.get("repair_type", "")
        try:
            require_parity_repair_authority(
                GovernanceAuthorityPolicy(project, request.user),
                repair_type,
            )
        except GovernanceAuthorityError as exc:
            raise PermissionDenied(exc.result.reason) from exc

        service = BindingLifecycleService(project, request.user)
        try:
            preview = service.preview_parity_repair(
                binding_id=payload.get("binding_id"),
                task_id=payload.get("task_id"),
                entity_global_id=payload.get("entity_global_id"),
                repair_type=payload.get("repair_type", ""),
            )
        except LifecycleValidationError as exc:
            return _lifecycle_error(request, exc.message, status=400, details=exc.details)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_lifecycle_confirm.html",
                {
                    "project": project,
                    "preview": preview,
                    "operation": "parity",
                    "binding_id": payload.get("binding_id"),
                    "task_id": payload.get("task_id"),
                    "entity_global_id": payload.get("entity_global_id"),
                    "repair_type": payload.get("repair_type"),
                    "confirm_phrase": "PARITY REPAIR",
                    "reason_codes": [
                        ("accepted_missing_m2m", "Accepted missing M2M"),
                        ("m2m_without_accepted", "M2M without accepted binding"),
                        ("review_m2m_leak", "Review M2M leak"),
                        ("duplicate_compatibility", "Duplicate compatibility"),
                        ("other", "Other"),
                    ],
                },
            )
        return JsonResponse(preview.to_dict())


class LinkLifecycleParityApplyView(ProjectAccessMixin, View):
    """POST — apply audited parity repair."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.binding_lifecycle import (
            BindingLifecycleService,
        )

        project = self.get_project()
        payload = _parse_json_or_form(request) or {}
        service = BindingLifecycleService(project, request.user)
        try:
            result = service.repair_parity(
                fingerprint=payload.get("fingerprint", ""),
                reason_code=payload.get("reason_code", ""),
                reason_text=payload.get("reason_text", ""),
                confirmation=payload.get("confirmation", ""),
                binding_id=payload.get("binding_id"),
                task_id=payload.get("task_id"),
                entity_global_id=payload.get("entity_global_id"),
                repair_type=payload.get("repair_type", ""),
            )
        except Exception as exc:
            return _lifecycle_error_from_exc(request, exc)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_lifecycle_result.html",
                {"project": project, "result": result, "operation": "parity"},
            )
        return JsonResponse(result.to_dict())


class LinkGovernanceAuditHistoryView(ProjectAccessMixin, View):
    """GET — read-only immutable governance audit timeline."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from scheduling.services.governance.audit_history import BindingAuditHistoryService
        from scheduling.services.governance.authority import (
            GovernanceAuthorityError,
            GovernanceAuthorityPolicy,
            GovernanceCapability,
        )

        project = self.get_project()
        try:
            GovernanceAuthorityPolicy(project, request.user).require(
                GovernanceCapability.VIEW_AUDIT
            )
        except GovernanceAuthorityError as exc:
            return JsonResponse({"error": exc.result.reason}, status=403)

        filters = BindingAuditHistoryService.filters_from_request(dict(request.GET.items()))
        payload = BindingAuditHistoryService(str(project.pk)).build(filters)

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_audit_panel.html",
                {
                    "project": project,
                    "audit": payload,
                    "filters": filters,
                },
            )
        return JsonResponse(payload)


class LinkGovernanceOverviewView(ProjectAccessMixin, View):
    """GET — methodology-aware governance scorecard and overview."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from django.core.exceptions import PermissionDenied

        from scheduling.services.governance.authority import GovernanceAuthorityError
        from scheduling.services.governance.governance_overview import (
            GovernanceOverviewService,
        )

        project = self.get_project()
        filters = GovernanceOverviewService.filters_from_request(request.GET.dict())
        try:
            payload = GovernanceOverviewService(project).build(request.user, filters)
        except GovernanceAuthorityError as exc:
            raise PermissionDenied(exc.result.reason) from exc

        if request.headers.get("HX-Request"):
            return render(
                request,
                "scheduling/components/governance_overview_panel.html",
                {
                    "project": project,
                    "overview": payload,
                    "filters": filters,
                },
            )
        return JsonResponse(payload)


class LookaheadDataView(ProjectAccessMixin, View):
    """JSON — per-week task buckets (starting/in_progress/finishing) for the Look-ahead tab."""

    _MAX_WEEKS = 12

    def get(self, request, **kwargs: object) -> JsonResponse:
        from datetime import timedelta

        try:
            weeks = max(1, min(self._MAX_WEEKS, int(request.GET.get("weeks", 3))))
        except (ValueError, TypeError):
            weeks = 3

        project = self.get_project()
        today = date.today()
        today_monday = today - timedelta(days=today.weekday())  # snap to Monday

        tasks = list(
            Task.objects.filter(project=project, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
            .order_by("start_date")
        )

        from scheduling.services.link_resolver import entity_gids_by_task

        gids_by_task = entity_gids_by_task(project.pk, [t.pk for t in tasks], accepted_only=True)

        result_weeks = []
        for w in range(weeks):
            ws = today_monday + timedelta(weeks=w)
            we = ws + timedelta(days=6)

            starting = []
            in_progress = []
            finishing = []

            for t in tasks:
                s, e = t.start_date, t.end_date
                in_week_start = ws <= s <= we
                in_week_end = ws <= e <= we
                spans_week = s < ws and e > we

                entry = {
                    "id": str(t.pk),
                    "name": t.name,
                    "start": s.isoformat(),
                    "end": e.isoformat(),
                    "stage": t.stage or "",
                    "activity_code": t.activity_code or "",
                    "is_critical": t.is_critical,
                    "entity_global_ids": gids_by_task.get(str(t.pk), []),
                }

                if in_week_start:
                    starting.append(entry)
                elif in_week_end:
                    finishing.append(entry)
                elif spans_week:
                    in_progress.append(entry)

            label = "This Week" if w == 0 else ("Next Week" if w == 1 else f"Week +{w}")
            result_weeks.append(
                {
                    "week_num": w + 1,
                    "start": ws.isoformat(),
                    "end": we.isoformat(),
                    "label": label,
                    "starting": starting,
                    "in_progress": in_progress,
                    "finishing": finishing,
                }
            )

        return JsonResponse(
            {
                "has_data": bool(tasks),
                "as_of": today.isoformat(),
                "weeks": result_weeks,
            }
        )


class MappingSubmitView(ProjectModifyAccessMixin, View):
    """HTMX POST — apply user column mapping to raw rows, show preview."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()

        raw_headers = request.session.get(f"raw_headers_{project.pk}")
        raw_rows = request.session.get(f"raw_rows_{project.pk}")
        source = request.session.get(f"raw_source_{project.pk}", "excel")

        if not raw_headers or not raw_rows:
            return toast_response(
                "Session expired — please re-upload the file.", "error", status=400
            )

        headers = json.loads(raw_headers)
        rows = json.loads(raw_rows)

        column_mapping = {
            field: request.POST.get(f"col_{field}", "").strip() for field in CANONICAL_FIELDS
        }
        # Remove unmapped optional fields so apply_mapping only sees real mappings
        column_mapping = {k: v for k, v in column_mapping.items() if v}

        ifc_param_name = request.POST.get("ifc_param_name", "Activity ID").strip() or "Activity ID"
        # Persist for auto-link and TimeLiner to read back
        request.session[f"ifc_param_name_{project.pk}"] = ifc_param_name
        # Propagate replace flag so TaskSaveView deletes existing tasks on confirm
        if request.POST.get("replace") == "true":
            request.session[f"schedule_replace_{project.pk}"] = True

        try:
            tasks = apply_mapping(headers, rows, column_mapping, source)
        except ValueError as exc:
            return toast_response(str(exc), "error", status=400)

        if not tasks:
            return toast_response(
                "No valid task rows found with this mapping.", "error", status=400
            )

        # Optionally save profile
        profile_name = request.POST.get("profile_name", "").strip()
        if profile_name:
            MappingProfile.objects.update_or_create(
                project=project,
                name=profile_name,
                defaults={"column_mapping": column_mapping, "ifc_parameter_name": ifc_param_name},
            )

        validation = validate_schedule(tasks, project_name=project.name)
        store_session_import_artifact(
            request,
            project.pk,
            filename=request.session.get(f"schedule_filename_{project.pk}", ""),
            tasks_fallback=tasks,
        )
        request.session[f"parsed_tasks_{project.pk}"] = json.dumps(
            [
                {
                    **t,
                    "start_date": str(t["start_date"]),
                    "end_date": str(t["end_date"]),
                    "actual_start": str(t["actual_start"]) if t.get("actual_start") else None,
                    "actual_end": str(t["actual_end"]) if t.get("actual_end") else None,
                    "early_start": str(t["early_start"]) if t.get("early_start") else None,
                    "early_finish": str(t["early_finish"]) if t.get("early_finish") else None,
                    "late_start": str(t["late_start"]) if t.get("late_start") else None,
                    "late_finish": str(t["late_finish"]) if t.get("late_finish") else None,
                    "expected_finish": str(t["expected_finish"])
                    if t.get("expected_finish")
                    else None,
                    "constraint_date": str(t["constraint_date"])
                    if t.get("constraint_date")
                    else None,
                }
                for t in tasks
            ]
        )
        # Clean up raw session data
        for key in (
            f"raw_headers_{project.pk}",
            f"raw_rows_{project.pk}",
            f"raw_source_{project.pk}",
        ):
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


class DetectColumnsView(ProjectModifyAccessMixin, View):
    """JSON POST — use LLM to detect column mapping from headers + sample rows.

    Checks ColumnMappingLookup first; falls back to LLM if no saved mapping exists.

    Body: {"headers": [...], "sample_rows": [[...], ...], "filename": "..."}
    Response: {"mapping": {...}, "confidence": float, "notes": str,
               "from_lookup": bool, "fingerprint": str}
    """

    def post(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        headers = body.get("headers") or []
        sample_rows = body.get("sample_rows") or []
        filename = str(body.get("filename") or "")

        if not headers:
            return JsonResponse({"error": "headers required."}, status=400)

        from .models import ColumnMappingLookup
        from .services.column_detector import (
            detect_columns,
            filename_to_pattern,
            fingerprint_headers,
        )

        fp = fingerprint_headers(headers)

        # Return saved mapping immediately if one exists for this header set.
        try:
            lookup = ColumnMappingLookup.objects.get(project=project, column_fingerprint=fp)
            new_count = lookup.hit_count + 1
            ColumnMappingLookup.objects.filter(pk=lookup.pk).update(hit_count=new_count)
            lookup.hit_count = new_count
            return JsonResponse(
                {
                    "mapping": lookup.mapping,
                    "confidence": 1.0,
                    "notes": f"Using saved mapping · {lookup.hit_count} previous uses",
                    "from_lookup": True,
                    "fingerprint": fp,
                }
            )
        except ColumnMappingLookup.DoesNotExist:
            pass

        result = detect_columns(headers, sample_rows, filename, user=request.user)
        result["from_lookup"] = False
        result["fingerprint"] = fp
        result.setdefault("filename_pattern", filename_to_pattern(filename))
        return JsonResponse(result)


class SaveMappingLookupView(ProjectModifyAccessMixin, View):
    """JSON POST — persist a confirmed mapping so future uploads auto-apply it.

    Body: {"fingerprint": str, "filename_pattern": str, "mapping": {...}}
    """

    def post(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        fp = str(body.get("fingerprint") or "").strip()
        pattern = str(body.get("filename_pattern") or "").strip()[:255]
        mapping = body.get("mapping")

        if not fp or not mapping or not isinstance(mapping, dict):
            return JsonResponse({"error": "fingerprint and mapping required."}, status=400)

        from .models import ColumnMappingLookup

        ColumnMappingLookup.objects.update_or_create(
            project=project,
            column_fingerprint=fp,
            defaults={"filename_pattern": pattern, "mapping": mapping},
        )
        return JsonResponse({"status": "saved"})


class ScheduleHealthCheckView(ProjectAccessMixin, View):
    """JSON GET — run deterministic health checks on the project's schedule tasks."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        from .services.health_check import run_health_check

        result = run_health_check(project)
        return JsonResponse(result)


class ProjectComprehensionView(ProjectAccessMixin, View):
    """GET: return existing comprehension. POST: rebuild from current tasks.

    POST triggers the full Comprehension Engine pipeline (stats + LLM sample).
    GET returns the last saved result or {"exists": False} if none yet.
    """

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        from .models import ProjectComprehension

        try:
            comp = ProjectComprehension.objects.get(project=project)
            return JsonResponse(
                {
                    "exists": True,
                    "ai_summary": comp.ai_summary,
                    "project_type": comp.naming_conventions.get("project_type", ""),
                    "total_activities": comp.total_activities,
                    "physical_activities": comp.physical_activities,
                    "critical_activities": comp.critical_activities,
                    "wbs_levels": comp.wbs_levels,
                    "phases": comp.phases,
                    "milestones": comp.milestones[:5],
                    "confidence_score": comp.confidence_score,
                    "naming_conventions": comp.naming_conventions,
                    "code_prefix_meanings": comp.naming_conventions,
                    "code_pattern": comp.code_pattern,
                    "project_start": str(comp.project_start) if comp.project_start else None,
                    "project_finish": str(comp.project_finish) if comp.project_finish else None,
                    "avg_duration_days": comp.avg_duration_days,
                    "key_observations": [],
                    "updated_at": str(comp.updated_at),
                }
            )
        except ProjectComprehension.DoesNotExist:
            return JsonResponse({"exists": False})

    def post(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        from .services.comprehension import build_comprehension

        result = build_comprehension(project, user=request.user)
        return JsonResponse(result)


def _get_ifc_files(project):
    return IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)


def _build_review_summary(project) -> dict:
    qs = TaskEntityBinding.objects.filter(task__project=project)

    physical_pks = set(
        Task.objects.filter(project=project, is_non_physical=False).values_list("pk", flat=True)
    )
    non_physical_pks = set(
        Task.objects.filter(project=project, is_non_physical=True).values_list("pk", flat=True)
    )

    # Unique task PKs that have at least one accepted binding
    accepted_task_pks = set(
        qs.filter(needs_review=False).values_list("task_id", flat=True).distinct()
    )
    # Unique task PKs with any binding at all
    bound_task_pks = set(qs.values_list("task_id", flat=True).distinct())

    # Needs Review: tasks that have bindings but none are accepted yet
    needs_review_task_pks = bound_task_pks - accepted_task_pks

    # For backwards-compat with the standalone review template
    needs_review_high = qs.filter(needs_review=True, confidence__gte=0.95).count()

    return {
        "total": len(physical_pks),
        "auto_accepted": len(accepted_task_pks & physical_pks),
        "needs_review": len(needs_review_task_pks & physical_pks),
        "needs_review_high": needs_review_high,
        "unlinked_tasks": len(physical_pks - bound_task_pks),
        "non_physical_count": len(non_physical_pks),
    }


def _make_row(binding: TaskEntityBinding, ifc_files) -> dict:
    try:
        entity = IFCEntity.objects.only("global_id", "name", "ifc_type").get(
            ifc_file__in=ifc_files, global_id=binding.entity_global_id
        )
        return {
            "binding": binding,
            "entity_name": entity.name or entity.global_id,
            "entity_type": entity.ifc_type,
        }
    except IFCEntity.DoesNotExist:
        return {
            "binding": binding,
            "entity_name": binding.entity_global_id[:14] + "…",
            "entity_type": "",
        }


def _render_link_review(
    request, project, filter_by: str = "all", inline: bool = False
) -> HttpResponse:
    ifc_files = _get_ifc_files(project)

    bindings_qs = (
        TaskEntityBinding.objects.filter(task__project=project)
        .select_related("task")
        .order_by("task__name", "-confidence")
    )
    if filter_by == "needs_review":
        bindings_qs = bindings_qs.filter(needs_review=True)
    elif filter_by in ("auto_accepted", "linked"):
        bindings_qs = bindings_qs.filter(needs_review=False)
    elif filter_by in ("exact", "normalized", "heuristic", "embedding", "manual"):
        bindings_qs = bindings_qs.filter(link_method=filter_by)

    binding_list = list(bindings_qs)
    gids = {b.entity_global_id for b in binding_list}
    entity_name_map = (
        {
            e.global_id: (e.name or e.global_id, e.ifc_type)
            for e in IFCEntity.objects.filter(ifc_file__in=ifc_files, global_id__in=gids).only(
                "global_id", "name", "ifc_type"
            )
        }
        if gids
        else {}
    )

    # Sibling count: how many OTHER tasks share each entity_global_id in this project
    entity_task_counts: dict[str, int] = dict(
        TaskEntityBinding.objects.filter(task__project=project)
        .values("entity_global_id")
        .annotate(cnt=Count("pk"))
        .values_list("entity_global_id", "cnt")
    )

    rows = [
        {
            "binding": b,
            "entity_name": entity_name_map.get(
                b.entity_global_id, (b.entity_global_id[:14] + "…", "")
            )[0],
            "entity_type": entity_name_map.get(b.entity_global_id, ("", ""))[1],
            "siblings": max(0, entity_task_counts.get(b.entity_global_id, 1) - 1),
        }
        for b in binding_list
    ]
    # Group by entity so shared-entity rows are adjacent
    rows.sort(key=lambda r: (r["entity_name"].lower(), r["binding"].task.name.lower()))

    unlinked_tasks = []
    if filter_by in ("all", "unlinked"):
        linked_pks = TaskEntityBinding.objects.filter(task__project=project).values_list(
            "task_id", flat=True
        )
        unlinked_tasks = list(
            Task.objects.filter(project=project, is_non_physical=False)
            .exclude(pk__in=linked_pks)
            .order_by("name")
        )

    non_physical_tasks = []
    if filter_by in ("all", "non_physical"):
        non_physical_tasks = list(
            Task.objects.filter(project=project, is_non_physical=True).order_by("name")
        )

    summary = _build_review_summary(project)
    template = (
        "scheduling/components/fourD_review_partial.html"
        if inline
        else "scheduling/tabs/link_review.html"
    )
    return render(
        request,
        template,
        {
            "project": project,
            "rows": rows,
            "unlinked_tasks": unlinked_tasks,
            "non_physical_tasks": non_physical_tasks,
            "summary": summary,
            "filter_by": filter_by,
        },
    )


class LinkReviewView(ProjectAccessMixin, View):
    """GET — Smart Pipeline binding review tab."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        filter_by = request.GET.get("filter", "all")
        inline = request.GET.get("inline") == "1"
        return _render_link_review(request, project, filter_by, inline=inline)


class BindingAcceptView(ProjectModifyAccessMixin, View):
    """HTMX POST — accept one binding, write M2M, return updated row + OOB summary."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        binding = get_object_or_404(
            TaskEntityBinding, pk=kwargs["binding_pk"], task__project=project
        )
        ifc_files = _get_ifc_files(project)

        binding.needs_review = False
        binding.save(update_fields=["needs_review"])

        try:
            entity = IFCEntity.objects.get(
                ifc_file__in=ifc_files, global_id=binding.entity_global_id
            )
            binding.task.ifc_entities.add(entity)
        except IFCEntity.DoesNotExist:
            pass

        row = _make_row(binding, ifc_files)
        summary = _build_review_summary(project)
        row_html = render_to_string(
            "scheduling/components/link_review_row.html",
            {"row": row, "project": project},
            request=request,
        )
        summary_html = render_to_string(
            "scheduling/components/link_review_summary.html",
            {"summary": summary, "project": project},
            request=request,
        )
        return HttpResponse(
            row_html + f'<div id="lr-summary" hx-swap-oob="true">{summary_html}</div>'
        )


class BindingRemoveView(ProjectModifyAccessMixin, View):
    """HTMX POST — delete one binding, remove M2M, return empty row + OOB summary."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        binding = get_object_or_404(
            TaskEntityBinding, pk=kwargs["binding_pk"], task__project=project
        )
        ifc_files = _get_ifc_files(project)
        binding_pk = str(binding.pk)

        try:
            entity = IFCEntity.objects.get(
                ifc_file__in=ifc_files, global_id=binding.entity_global_id
            )
            binding.task.ifc_entities.remove(entity)
        except IFCEntity.DoesNotExist:
            pass

        binding.delete()
        summary = _build_review_summary(project)
        summary_html = render_to_string(
            "scheduling/components/link_review_summary.html",
            {"summary": summary, "project": project},
            request=request,
        )
        return HttpResponse(
            f'<tr id="binding-row-{binding_pk}" style="display:none"></tr>'
            f'<div id="lr-summary" hx-swap-oob="true">{summary_html}</div>'
        )


class BulkAcceptView(ProjectModifyAccessMixin, View):
    """HTMX POST — accept all bindings with confidence ≥ 0.95, re-render full tab."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_files = _get_ifc_files(project)

        pending = list(
            TaskEntityBinding.objects.filter(
                task__project=project, needs_review=True, confidence__gte=0.95
            ).select_related("task")
        )
        accepted = 0
        for binding in pending:
            try:
                entity = IFCEntity.objects.get(
                    ifc_file__in=ifc_files, global_id=binding.entity_global_id
                )
                binding.task.ifc_entities.add(entity)
                accepted += 1
            except IFCEntity.DoesNotExist:
                pass

        TaskEntityBinding.objects.filter(pk__in=[b.pk for b in pending]).update(needs_review=False)

        response = _render_link_review(request, project, "all")
        return trigger_toast(response, f"Accepted {accepted} binding(s).", "success")


class BindingExportView(ProjectAccessMixin, View):
    """GET — download all bindings as CSV."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        bindings = (
            TaskEntityBinding.objects.filter(task__project=project)
            .select_related("task")
            .order_by("task__name", "-confidence")
        )
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="link_review_{project.pk}.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "Task",
                "Activity Code",
                "Entity GlobalId",
                "Confidence",
                "Method",
                "Needs Review",
            ]
        )
        for b in bindings:
            writer.writerow(
                [
                    b.task.name,
                    b.task.activity_code,
                    b.entity_global_id,
                    f"{b.confidence:.2f}",
                    b.link_method,
                    "Yes" if b.needs_review else "No",
                ]
            )
        return response


class BindingAddView(ProjectModifyAccessMixin, View):
    """HTMX POST — manually create a binding for an unlinked task, re-render full tab."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        task_pk = request.POST.get("task_pk", "").strip()
        entity_global_id = request.POST.get("entity_global_id", "").strip()

        if not task_pk or not entity_global_id:
            return toast_response("Missing task or entity.", "error", status=400)

        task = get_object_or_404(Task, pk=task_pk, project=project)
        ifc_files = _get_ifc_files(project)

        try:
            entity = IFCEntity.objects.get(ifc_file__in=ifc_files, global_id=entity_global_id)
        except IFCEntity.DoesNotExist:
            return toast_response("Entity not found in this project.", "error", status=404)

        TaskEntityBinding.objects.get_or_create(
            task=task,
            entity_global_id=entity_global_id,
            defaults={"confidence": 1.0, "link_method": "exact", "needs_review": False},
        )
        task.ifc_entities.add(entity)

        response = _render_link_review(request, project, "all")
        return trigger_toast(response, f"Linked '{task.name}' manually.", "success")


class TaskToggleNonPhysicalView(ProjectModifyAccessMixin, View):
    """HTMX POST — manually override a task's non-physical classification.

    POST param 'target': 'non_physical' | 'physical'
    Sets non_physical_locked=True so Layer 0 never auto-reverts the choice.
    Re-renders the full review tab.
    """

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        task = get_object_or_404(Task, pk=kwargs["task_pk"], project=project)
        target = request.POST.get("target", "non_physical")
        task.is_non_physical = target == "non_physical"
        task.non_physical_locked = True
        task.save(update_fields=["is_non_physical", "non_physical_locked"])
        label = "non-physical" if task.is_non_physical else "physical"
        response = _render_link_review(request, project, "all")
        return trigger_toast(response, f"'{task.name}' marked as {label}.", "success")


class BindingSearchView(ProjectAccessMixin, View):
    """HTMX GET — entity typeahead for the manual-link panel in the review tab."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        q = request.GET.get("q", "").strip()
        task_pk = request.GET.get("task_pk", "")

        ifc_files = _get_ifc_files(project)
        qs = IFCEntity.objects.filter(ifc_file__in=ifc_files)
        if q:
            qs = qs.filter(name__icontains=q)
        entities = qs.order_by("name")[:10]

        return render(
            request,
            "scheduling/components/binding_search.html",
            {"entities": entities, "project": project, "task_pk": task_pk},
        )


class ScheduleWritebackView(ProjectModifyAccessMixin, View):
    """POST — two-phase schedule modification via the RSAA pipeline.

    Phase 1 (no ``confirm`` key): analyse *message* and return proposed changes.
    Phase 2 (``confirm=true`` + ``proposals`` list): apply confirmed changes.

    Request body (JSON):
      Phase 1: {"message": "delay Casting Columns by one week"}
      Phase 2: {"confirm": true, "proposals": [...]}
    """

    def post(self, request, **kwargs: object) -> JsonResponse:
        from .services.schedule_writeback.modification_service import (
            ModificationProposal,
            ScheduleModificationService,
        )
        from .services.schedule_writeback.slot_extractor import ScheduleSlotExtractor
        from .services.schedule_writeback.task_resolver import TaskResolver
        from .services.schedule_writeback.triage import ScheduleTriageClassifier

        project = self.get_project()
        try:
            body = json.loads(request.body or b"{}")
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if body.get("confirm") and body.get("proposals"):
            svc = ScheduleModificationService()
            proposals = [
                ModificationProposal(
                    task_id=p["task_id"],
                    task_name=p["task_name"],
                    activity_code=p.get("activity_code", ""),
                    changes=p["changes"],
                    action=p["action"],
                )
                for p in body["proposals"]
            ]
            result = svc.apply(proposals)
            return JsonResponse(
                {
                    "status": "applied",
                    "updated": result["updated"],
                    "errors": result["errors"],
                }
            )

        message = (body.get("message") or "").strip()
        if not message:
            return JsonResponse({"error": "No message provided."}, status=400)

        triage = ScheduleTriageClassifier(user=request.user)
        triage_result = triage.classify(message)

        if triage_result.is_unclear:
            return JsonResponse(
                {
                    "type": "unclear",
                    "message": "I couldn't understand the request. Please name the task and describe what should change.",
                }
            )

        if triage_result.is_out_of_scope:
            seg = next((s for s in triage_result.segments if s.kind == "OUT_OF_SCOPE"), None)
            reason = seg.reason if seg else ""
            return JsonResponse(
                {
                    "type": "out_of_scope",
                    "message": f"This type of change is not supported in schedule writeback. {reason}",
                }
            )

        extractor = ScheduleSlotExtractor(user=request.user)
        resolver = TaskResolver()
        svc = ScheduleModificationService()

        all_proposals: list[dict] = []
        warnings: list[str] = []

        for segment in triage_result.segments:
            if segment.kind in ("OUT_OF_SCOPE", "UNCLEAR"):
                continue

            slot_result = extractor.extract(segment, message)
            if not slot_result.ok:
                warnings.extend(slot_result.warnings)
                continue
            if slot_result.warnings:
                warnings.extend(slot_result.warnings)

            resolution = resolver.resolve(segment.target_phrase, project)
            if resolution.is_empty:
                warnings.append(
                    f"Could not find task: '{segment.target_phrase}'. {resolution.diagnostic}"
                )
                continue

            proposals = svc.build_proposals(
                resolution.tasks[:5],
                slot_result.slots,
                segment.kind,
            )
            all_proposals.extend(
                {
                    "task_id": p.task_id,
                    "task_name": p.task_name,
                    "activity_code": p.activity_code,
                    "changes": p.changes,
                    "action": p.action,
                    "description": p.describe(),
                }
                for p in proposals
            )

        if not all_proposals:
            return JsonResponse(
                {
                    "type": "no_matches",
                    "message": "Could not find matching tasks or compute changes. "
                    + ("; ".join(warnings) if warnings else "Please try rephrasing."),
                }
            )

        return JsonResponse(
            {
                "type": "proposals",
                "proposals": all_proposals,
                "warnings": warnings,
            }
        )


class LinkElementView(ProjectModifyAccessMixin, View):
    """POST — manually link a single IFC element globalId to a task."""

    def post(self, request, task_pk: str, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        task = get_object_or_404(Task, pk=task_pk, project=project)

        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        global_id = body.get("global_id", "").strip()
        if not global_id:
            return JsonResponse({"error": "global_id is required"}, status=400)

        binding, created = TaskEntityBinding.objects.get_or_create(
            task=task,
            entity_global_id=global_id,
            defaults={
                "confidence": 1.0,
                "link_method": TaskEntityBinding.LinkMethod.MANUAL,
                "needs_review": False,
            },
        )
        if not created:
            TaskEntityBinding.objects.filter(pk=binding.pk).update(
                confidence=1.0,
                link_method=TaskEntityBinding.LinkMethod.MANUAL,
                needs_review=False,
            )

        ifc_files = _get_ifc_files(project)
        entities = list(IFCEntity.objects.filter(ifc_file__in=ifc_files, global_id=global_id))
        if entities:
            task.ifc_entities.add(*entities)

        status_code = 201 if created else 200
        return JsonResponse({"status": "linked", "binding_id": str(binding.id)}, status=status_code)


class UnlinkAllElementView(ProjectModifyAccessMixin, View):
    """POST — remove all task bindings for a given IFC element globalId across the project.

    Primary store is TaskEntityBinding (covers 100% of links including needs_review rows).
    ifc_entities M2M is a secondary store covering only the ~15% of auto-accepted links;
    cleaned up after the binding rows are confirmed deleted.
    """

    def post(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()

        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        global_id = body.get("global_id", "").strip()
        if not global_id:
            return JsonResponse({"error": "global_id is required"}, status=400)

        # Primary: delete by entity_global_id string — covers all binding types.
        binding_deleted, _ = TaskEntityBinding.objects.filter(
            task__project=project, entity_global_id=global_id
        ).delete()

        if not binding_deleted:
            return JsonResponse({"error": "No bindings found"}, status=404)

        # Secondary: remove from ifc_entities M2M for the auto-accepted subset.
        entities = list(IFCEntity.objects.filter(global_id=global_id))
        if entities:
            for task in Task.objects.filter(
                project=project, ifc_entities__global_id=global_id
            ).distinct():
                task.ifc_entities.remove(*entities)

        return JsonResponse({"status": "unlinked", "deleted": binding_deleted})


class UnlinkElementView(ProjectModifyAccessMixin, View):
    """POST — remove the link between a single IFC element globalId and a task.

    Primary store is TaskEntityBinding (covers 100% of links including needs_review rows).
    ifc_entities M2M is a secondary store covering only the ~15% of auto-accepted links;
    cleaned up after the binding row is confirmed deleted.
    """

    def post(self, request, task_pk: str, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        task = get_object_or_404(Task, pk=task_pk, project=project)

        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        global_id = body.get("global_id", "").strip()
        if not global_id:
            return JsonResponse({"error": "global_id is required"}, status=400)

        # Primary: delete by entity_global_id string — covers all binding types.
        binding_deleted, _ = TaskEntityBinding.objects.filter(
            task=task, entity_global_id=global_id
        ).delete()

        if not binding_deleted:
            return JsonResponse({"error": "Binding not found"}, status=404)

        # Secondary: remove from ifc_entities M2M for the auto-accepted subset.
        entities = list(IFCEntity.objects.filter(global_id=global_id))
        if entities:
            task.ifc_entities.remove(*entities)

        return JsonResponse({"status": "unlinked"})


class TasksForLinkView(ProjectAccessMixin, View):
    """GET — search tasks for the Link-to-Task modal.

    Query params:
      global_id  IFC element globalId — used to flag already-linked tasks.
      q          Search term (required, min 2 chars). No q → empty list.

    Returns at most 50 results to avoid browser-side rendering lag.
    """

    _MAX_RESULTS = 50

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        global_id = request.GET.get("global_id", "").strip()
        q = request.GET.get("q", "").strip()

        if not q:
            return JsonResponse({"tasks": []})

        linked_pks: set = set()
        if global_id:
            linked_pks = set(
                TaskEntityBinding.objects.filter(
                    task__project=project, entity_global_id=global_id
                ).values_list("task_id", flat=True)
            )

        tasks = (
            Task.objects.filter(project=project, is_non_physical=False)
            .filter(Q(name__icontains=q) | Q(activity_code__icontains=q))
            .order_by("name")
            .only("pk", "name", "activity_code", "status", "start_date", "end_date")
        )[: self._MAX_RESULTS]

        data = [
            {
                "pk": str(task.pk),
                "name": task.name,
                "activity_code": task.activity_code or "",
                "status": task.status,
                "start_date": task.start_date.isoformat(),
                "end_date": task.end_date.isoformat(),
                "linked": task.pk in linked_pks,
            }
            for task in tasks
        ]
        return JsonResponse({"tasks": data})


class BulkLinkElementView(ProjectModifyAccessMixin, View):
    """POST — add task bindings for one IFC element in this project.

    Body: {global_id: str, task_pks: [str, ...]}

    Add-only: creates bindings for any task_pks not yet linked.
    Existing links for tasks not in the selection are left untouched — the
    modal uses server-side search so the user can't see the full linked set,
    making removal via this endpoint unsafe. Use UnlinkAllElementView or
    UnlinkElementView to remove specific links.
    """

    def post(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()

        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        global_id = body.get("global_id", "").strip()
        if not global_id:
            return JsonResponse({"error": "global_id is required"}, status=400)

        selected_pks = {str(pk) for pk in body.get("task_pks", [])}
        if not selected_pks:
            return JsonResponse({"status": "ok", "linked": 0})

        current_pks = {
            str(pk)
            for pk in TaskEntityBinding.objects.filter(
                task__project=project, entity_global_id=global_id
            ).values_list("task_id", flat=True)
        }

        to_add = selected_pks - current_pks
        entities = list(IFCEntity.objects.filter(global_id=global_id))

        if to_add:
            tasks_to_link = list(Task.objects.filter(project=project, pk__in=to_add))
            TaskEntityBinding.objects.bulk_create(
                [
                    TaskEntityBinding(
                        task=task,
                        entity_global_id=global_id,
                        confidence=1.0,
                        link_method=TaskEntityBinding.LinkMethod.MANUAL,
                        needs_review=False,
                    )
                    for task in tasks_to_link
                ],
                ignore_conflicts=True,
            )
            if entities:
                for task in tasks_to_link:
                    task.ifc_entities.add(*entities)

        return JsonResponse({"status": "ok", "linked": len(to_add)})


# ---------------------------------------------------------------------------
# Intelligence views — embedded-schedule Q&A (folded from scheduling.services.intelligence)
# ---------------------------------------------------------------------------


class IntelligenceStatusView(ProjectAccessMixin, View):
    """GET — returns embedding coverage stats for the Intelligence panel."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        from scheduling.models import Task, TaskEmbedding

        project = self.get_project()
        total_tasks = (
            Task.objects.filter(project=project, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
            .count()
        )
        embedded = TaskEmbedding.objects.filter(task__project=project).count()
        return JsonResponse(
            {
                "total_tasks": total_tasks,
                "embedded": embedded,
                "ready": embedded > 0,
            }
        )


class IntelligenceEmbedView(ProjectModifyAccessMixin, View):
    """POST — (re)embed all project tasks into TaskEmbedding.

    Accepts optional JSON body: {"force": true} to re-embed even unchanged tasks.
    Returns: {ok, embedded, skipped, errors, total_tasks}
    """

    def post(self, request, **kwargs: object) -> JsonResponse:
        import json

        from scheduling.models import Task
        from scheduling.services.intelligence.embedder import ScheduleEmbedder

        project = self.get_project()

        force = False
        if request.content_type and "json" in request.content_type:
            try:
                body = json.loads(request.body or b"{}")
                force = bool(body.get("force", False))
            except (json.JSONDecodeError, ValueError):
                pass

        try:
            result = ScheduleEmbedder().embed_project(str(project.pk), force=force)
        except Exception as exc:
            logger.exception("embed_project failed for %s", project.pk)
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)

        total_tasks = (
            Task.objects.filter(project=project, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
            .count()
        )

        return JsonResponse(
            {
                "ok": True,
                "embedded": result["embedded"],
                "skipped": result["skipped"],
                "errors": result["errors"],
                "total_tasks": total_tasks,
            }
        )


class IntelligenceAskView(ProjectAccessMixin, View):
    """POST — answer a natural-language question about the project schedule.

    Expects JSON body: {"question": "..."}
    Returns: {answer, tasks_cited, coverage, error}
    """

    def post(self, request, **kwargs: object) -> JsonResponse:
        import json

        from scheduling.services.intelligence.service import ProjectIntelligenceService

        project = self.get_project()

        try:
            body = json.loads(request.body or b"{}")
            question = str(body.get("question", "")).strip()
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not question:
            return JsonResponse({"error": "question is required."}, status=400)

        svc = ProjectIntelligenceService(project, request.user)
        result = svc.ask(question)

        if result.get("error") and not result.get("answer"):
            return JsonResponse(result, status=500)
        return JsonResponse(result)
