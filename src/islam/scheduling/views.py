# islam/scheduling/views.py
"""4D TimeLiner scheduling views — file upload, linking, Gantt, and simulation."""

from __future__ import annotations

import csv
import json
import logging
from datetime import date

from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views import View
from django.views.generic import TemplateView

from core.http import toast_response, trigger_toast
from core.mixins import ProjectAccessMixin, ProjectModifyAccessMixin, ProjectTabMixin
from ifc_processor.models import IFCEntity, IFCFile

from .models import (
    MappingProfile,
    ScheduleSource,
    Task,
    TaskDependency,
    TaskEntityBinding,
)
from .services.autolink import autodetect_stages, run_autolink
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
from .services.linker import apply_matches, param_match_tasks
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
        return ctx


# ---------------------------------------------------------------------------
# Preview endpoint — returns raw columns + suggested mapping + 200 sample rows
# ---------------------------------------------------------------------------


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
                return self._preview_parsed(request, project, uploaded, parse_msp)
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
        request.session[f"schedule_filename_{project.pk}"] = file_obj.name

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
        tasks, raw_deps = parser_fn(file_obj)
        request.session[f"schedule_filename_{project.pk}"] = file_obj.name

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
        _NUMERIC_COLS = {"total_float_days"}
        all_rows = [
            [
                (str(t.get(c)) if t.get(c) is not None else "")
                if c in _NUMERIC_COLS
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
                tasks, raw_deps = parse_xer(uploaded)
                source = "xer"
            elif filename.endswith(".xml"):
                tasks, raw_deps = parse_msp(uploaded)
                source = "msp"
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

        from decimal import Decimal

        from .services.column_mapper import parse_predecessor_string

        try:
            tasks_data = json.loads(raw)
        except json.JSONDecodeError:
            return toast_response("Session data corrupt — re-upload the file.", "error", status=400)

        # Replace mode: wipe existing tasks before saving (cascades deps + bindings)
        replace_mode = request.POST.get("replace") == "true" or bool(
            request.session.pop(f"schedule_replace_{project.pk}", False)
        )
        if replace_mode:
            existing = Task.objects.filter(project=project).count()
            Task.objects.filter(project=project).delete()
            logger.info("Replace mode: cleared %d tasks for project %s", existing, project.pk)

        created = 0
        updated = 0
        unchanged = 0
        touched_pks: list[str] = []  # PKs of tasks created or updated in this import
        xer_id_map: dict[str, str] = {}  # _xer_task_id  → str(task.pk)
        msp_uid_map: dict[str, str] = {}  # _msp_uid      → str(task.pk)
        p6_obj_id_map: dict[str, str] = {}  # _p6_obj_id    → str(task.pk)
        activity_code_map: dict[str, str] = {}  # activity_code → str(task.pk)
        tasks_with_preds: list[tuple[str, str]] = []  # (task_pk, raw_predecessors)

        # Pre-load existing tasks for dedup:
        #   primary key   — activity_code (str)
        #   secondary key — (name, start_date) for tasks without an activity code
        existing_by_code: dict[str, Task] = {}
        existing_by_name_date: dict[tuple, Task] = {}
        cleaned = 0
        if not replace_mode:
            for t in Task.objects.filter(project=project).only(
                "pk", "activity_code", "name", "start_date", "end_date"
            ):
                if t.activity_code:
                    existing_by_code[t.activity_code] = t
                existing_by_name_date[(t.name, str(t.start_date))] = t

            # Clean pre-existing duplicates: same (project, activity_code) — keep first, delete rest
            dup_codes = list(
                Task.objects.filter(project=project)
                .exclude(activity_code="")
                .values("activity_code")
                .annotate(cnt=Count("pk"))
                .filter(cnt__gt=1)
                .values_list("activity_code", flat=True)
            )
            for code in dup_codes:
                tasks_for_code = list(
                    Task.objects.filter(project=project, activity_code=code).order_by(
                        "start_date", "name"
                    )
                )
                to_delete = [t.pk for t in tasks_for_code[1:]]
                Task.objects.filter(pk__in=to_delete).delete()
                cleaned += len(to_delete)
            if cleaned:
                logger.info("Cleaned %d duplicate tasks for project %s", cleaned, project.pk)

        has_p6_cpm = any(
            td.get("total_float_days") is not None or td.get("early_start") for td in tasks_data
        )

        for td in tasks_data:
            try:
                cost_str = td.get("cost") or td.get("budgeted_cost")
                actual_start_raw = td.get("actual_start")
                actual_end_raw = td.get("actual_end")
                early_start_raw = td.get("early_start")
                early_finish_raw = td.get("early_finish")
                late_start_raw = td.get("late_start")
                late_finish_raw = td.get("late_finish")
                total_float_val = td.get("total_float_days")
                activity_code = td.get("activity_code", "")

                task_fields = dict(
                    name=td["name"],
                    description=td.get("description", ""),
                    start_date=date.fromisoformat(td["start_date"]),
                    end_date=date.fromisoformat(td["end_date"]),
                    actual_start=date.fromisoformat(actual_start_raw) if actual_start_raw else None,
                    actual_end=date.fromisoformat(actual_end_raw) if actual_end_raw else None,
                    status=td.get("status", "planned"),
                    source=td.get("source", "excel"),
                    activity_code=activity_code,
                    color=td.get("color", "#3b82f6"),
                    cost=Decimal(cost_str) if cost_str else None,
                    activity_type=td.get("activity_type", ""),
                    early_start=date.fromisoformat(early_start_raw) if early_start_raw else None,
                    early_finish=date.fromisoformat(early_finish_raw) if early_finish_raw else None,
                    late_start=date.fromisoformat(late_start_raw) if late_start_raw else None,
                    late_finish=date.fromisoformat(late_finish_raw) if late_finish_raw else None,
                    total_float=int(total_float_val) if total_float_val is not None else None,
                    is_critical=total_float_val is not None and int(total_float_val) == 0,
                )

                existing = None
                if activity_code and activity_code in existing_by_code:
                    existing = existing_by_code[activity_code]
                else:
                    existing = existing_by_name_date.get(
                        (task_fields["name"], str(task_fields["start_date"]))
                    )

                if existing is not None:
                    dirty = [f for f, v in task_fields.items() if getattr(existing, f) != v]
                    if dirty:
                        for f in dirty:
                            setattr(existing, f, task_fields[f])
                        existing.save(update_fields=dirty)
                        updated += 1
                        touched_pks.append(str(existing.pk))
                    else:
                        unchanged += 1
                    task = existing
                else:
                    task = Task.objects.create(project=project, **task_fields)
                    created += 1
                    touched_pks.append(str(task.pk))

                pk = str(task.pk)
                if td.get("_xer_task_id"):
                    xer_id_map[td["_xer_task_id"]] = pk
                if td.get("_msp_uid"):
                    msp_uid_map[td["_msp_uid"]] = pk
                if td.get("_p6_obj_id"):
                    p6_obj_id_map[td["_p6_obj_id"]] = pk
                if activity_code:
                    activity_code_map[activity_code] = pk
                raw_preds = td.get("_raw_predecessors", "").strip()
                if raw_preds:
                    tasks_with_preds.append((pk, raw_preds))
            except Exception as exc:
                logger.warning("Skipping task row: %s", exc)

        del request.session[session_key]

        # ── Dependency resolution ────────────────────────────────────────
        raw_deps_json = request.session.pop(f"parsed_deps_{project.pk}", None)
        raw_deps: list[dict] = json.loads(raw_deps_json) if raw_deps_json else []

        dep_objects: list[TaskDependency] = []
        dep_set: set[tuple] = set()

        def _add(pred_pk: str, succ_pk: str, dep_type: str, lag_days: int) -> None:
            key = (pred_pk, succ_pk, dep_type)
            if key in dep_set or pred_pk == succ_pk:
                return
            dep_set.add(key)
            dep_objects.append(
                TaskDependency(
                    predecessor_id=pred_pk,
                    successor_id=succ_pk,
                    dep_type=dep_type,
                    lag_days=lag_days,
                )
            )

        for d in raw_deps:
            if "pred_xer_id" in d:
                pred_pk = xer_id_map.get(d["pred_xer_id"])
                succ_pk = xer_id_map.get(d["succ_xer_id"])
            elif "pred_uid" in d:
                pred_pk = msp_uid_map.get(d["pred_uid"])
                succ_pk = msp_uid_map.get(d["succ_uid"])
            elif "pred_p6_obj_id" in d:
                pred_pk = p6_obj_id_map.get(d["pred_p6_obj_id"])
                succ_pk = p6_obj_id_map.get(d["succ_p6_obj_id"])
            else:
                continue
            if pred_pk and succ_pk:
                _add(pred_pk, succ_pk, d.get("dep_type", "FS"), d.get("lag_days", 0))

        for task_pk, raw_preds in tasks_with_preds:
            for ref in parse_predecessor_string(raw_preds):
                pred_pk = activity_code_map.get(ref["activity_code"])
                if pred_pk:
                    _add(pred_pk, task_pk, ref["dep_type"], ref["lag_days"])

        dep_count = 0
        if dep_objects:
            TaskDependency.objects.filter(predecessor__project=project).delete()
            TaskDependency.objects.bulk_create(dep_objects, ignore_conflicts=True)
            dep_count = len(dep_objects)
            logger.info("Dependencies saved: %d for project %s", dep_count, project.pk)
            if not has_p6_cpm:
                try:
                    cpm = compute_critical_path(str(project.pk))
                    logger.info(
                        "CPM computed: %d critical of %d tasks",
                        len(cpm["critical_task_ids"]),
                        len(cpm["task_data"]),
                    )
                except Exception as exc:
                    logger.warning("CPM auto-run failed: %s", exc)

        all_tasks = list(
            Task.objects.filter(project=project).only("pk", "name", "stage", "sub_stage")
        )
        autodetect_stages(all_tasks)

        # Record this import event so the Data Sources tab can show source chips.
        filename = request.session.pop(f"schedule_filename_{project.pk}", "")
        source_format = tasks_data[0].get("source", "excel") if tasks_data else "excel"
        current_source = ScheduleSource.objects.create(
            project=project,
            filename=filename,
            source_format=source_format,
            task_count=created + updated + unchanged,
        )
        if touched_pks:
            Task.objects.filter(pk__in=touched_pks).update(schedule_source=current_source)

        tasks = Task.objects.filter(project=project).prefetch_related("ifc_entities")
        response = render(
            request,
            "scheduling/components/task_list.html",
            {"tasks": tasks, "project": project, "preview_mode": False, "dep_count": dep_count},
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
            msg += (
                f" {dep_count} dependenc{'y' if dep_count == 1 else 'ies'} imported, CPM computed."
            )
        if cleaned:
            msg += f" Cleaned {cleaned} duplicate task{'s' if cleaned != 1 else ''}."
        return trigger_toast(response, msg, "success")


class ScheduleClearView(ProjectModifyAccessMixin, View):
    """POST — delete all tasks and dependencies for this project."""

    def post(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        TaskDependency.objects.filter(predecessor__project=project).delete()
        deleted, _ = Task.objects.filter(project=project).delete()
        ScheduleSource.objects.filter(project=project).delete()
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


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------


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
            return toast_response(
                "No tasks to link — import a schedule first.", "error", status=400
            )

        matches = param_match_tasks(tasks, entities, param_name)
        if matches:
            apply_matches(Task, matches)

        tasks_qs = Task.objects.filter(project=project).prefetch_related("ifc_entities")
        response = render(
            request,
            "scheduling/components/attach_results.html",
            {
                "tasks": tasks_qs,
                "matches": matches,
                "project": project,
                "match_mode": "param",
                "param_name": param_name,
            },
        )
        linked = sum(1 for m in matches if m["entity_ids"])
        return trigger_toast(
            response, f"Parameter '{param_name}' matched {linked} tasks.", "success"
        )


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
    """JSON endpoint — task data for the Gantt chart and Simulate tab."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        tasks = (
            Task.objects.filter(project=project, is_non_physical=False)
            .prefetch_related("ifc_entities")
            .order_by("start_date", "activity_code")
        )
        today = date.today()

        data = []
        for task in tasks:
            entities = list(task.ifc_entities.values("global_id", "name"))
            gids = [e["global_id"] for e in entities]
            data.append(
                {
                    "id": str(task.pk),
                    "name": task.name,
                    "start": task.start_date.isoformat(),
                    "end": task.end_date.isoformat(),
                    "actual_start": task.actual_start.isoformat() if task.actual_start else None,
                    "actual_end": task.actual_end.isoformat() if task.actual_end else None,
                    "progress": _compute_progress(task, today),
                    "stage": task.stage or "",
                    "sub_stage": task.sub_stage or "",
                    "is_critical": task.is_critical,
                    "total_float": task.total_float,
                    "activity_code": task.activity_code or "",
                    "linked_entities": [
                        {"name": e["name"] or e["global_id"], "global_id": e["global_id"]}
                        for e in entities
                    ],
                    # kept for simulate.html backward compatibility
                    "status": task.status,
                    "color": task.color,
                    "link_status": task.link_status,
                    "entity_global_ids": gids,
                }
            )

        return JsonResponse({"tasks": data})


class TaskDetailView(ProjectAccessMixin, View):
    """HTMX GET — task detail side panel for the Gantt chart."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        task = get_object_or_404(Task, pk=kwargs["task_pk"], project=project)
        entities = list(task.ifc_entities.only("global_id", "name", "ifc_type"))
        today = date.today()
        progress = _compute_progress(task, today)

        gids = [e.global_id for e in entities]
        siblings_count = (
            (
                Task.objects.filter(project=project, ifc_entities__global_id__in=gids)
                .exclude(pk=task.pk)
                .distinct()
                .count()
            )
            if gids
            else 0
        )

        return render(
            request,
            "scheduling/components/task_detail.html",
            {
                "task": task,
                "entities": entities,
                "progress": progress,
                "siblings_count": siblings_count,
                "stage_color": _STAGE_COLORS.get(task.stage or "", "#6b7280"),
                "entity_global_ids_json": json.dumps(gids),
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
        project = self.get_project()
        try:
            result = compute_evm(str(project.pk))
        except Exception as exc:
            logger.exception("EVM failed for project %s", project.pk)
            return JsonResponse({"error": str(exc)}, status=500)
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
        return JsonResponse({"stages": stages})


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


# ---------------------------------------------------------------------------
# Auto column detection
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Link Review — binding review tab
# ---------------------------------------------------------------------------


def _get_ifc_files(project):
    return IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)


def _build_review_summary(project) -> dict:
    qs = TaskEntityBinding.objects.filter(task__project=project)
    total = qs.count()
    needs_review = qs.filter(needs_review=True).count()
    needs_review_high = qs.filter(needs_review=True, confidence__gte=0.95).count()
    auto_accepted = qs.filter(needs_review=False).count()
    all_pks = set(Task.objects.filter(project=project).values_list("pk", flat=True))
    linked_pks = set(qs.values_list("task_id", flat=True))
    non_physical_pks = set(
        Task.objects.filter(project=project, is_non_physical=True).values_list("pk", flat=True)
    )
    return {
        "total": total,
        "needs_review": needs_review,
        "needs_review_high": needs_review_high,
        "auto_accepted": auto_accepted,
        "unlinked_tasks": len(all_pks - linked_pks - non_physical_pks),
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


def _render_link_review(request, project, filter_by: str = "all") -> HttpResponse:
    ifc_files = _get_ifc_files(project)

    bindings_qs = (
        TaskEntityBinding.objects.filter(task__project=project)
        .select_related("task")
        .order_by("task__name", "-confidence")
    )
    if filter_by == "needs_review":
        bindings_qs = bindings_qs.filter(needs_review=True)
    elif filter_by == "auto_accepted":
        bindings_qs = bindings_qs.filter(needs_review=False)
    elif filter_by in ("exact", "normalized", "heuristic", "embedding"):
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
    return render(
        request,
        "scheduling/tabs/link_review.html",
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
        return _render_link_review(request, project, filter_by)


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

        # ── Phase 2: apply confirmed proposals ────────────────────────────
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

        # ── Phase 1: analyse and propose ──────────────────────────────────
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
