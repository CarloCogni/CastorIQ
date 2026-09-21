# takeoff/services/link_analysis.py
"""4D Link Analysis — schedule task ↔ model element link diagnostics.

Read-only aggregates from Tasks, trusted TaskEntityBinding rows
(needs_review=False), and indexed IFCEntity records. No QTO, BOQ, cost,
or quantity logic.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from django.utils import timezone

from ifc_processor.models import IFCEntity, IFCFile
from takeoff.services.trusted_links import (
    linked_entity_gids_for_project,
    trusted_counts,
    trusted_fanout_sizes,
)

logger = logging.getLogger(__name__)

FANOUT_OUTLIER_THRESHOLD = 100
HISTOGRAM_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("1–10", 1, 10),
    ("11–30", 11, 30),
    ("31–50", 31, 50),
    ("51–100", 51, 100),
    ("100+", 101, None),
)
TASK_PAGE_SIZE = 50
ELEMENT_PAGE_SIZE = 50
EXPANSION_PREVIEW_LIMIT = 8
CLASS_CHART_LIMIT = 12

# Task Review Breakdown chart — mutually exclusive attention buckets (review-first).
_DISTRIBUTION_KEYS = (
    ("review", "Review pending"),
    ("broad_link", "Broad linked"),
    ("partial", "Partial"),
    ("ok", "OK linked"),
    ("unlinked", "Unlinked/non-model"),
)
# Actionable Link Review excludes unlinked/non-model (those stay in Schedule Coverage).
_ACTIONABLE_REVIEW_KEYS = ("review", "broad_link", "partial")


def _share(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * float(part) / float(total), 2)


def _level_label(spatial_type: str | None, entity_name: str | None) -> str:
    if spatial_type is None and not (entity_name or "").strip():
        return "Unavailable"
    name = (entity_name or "").strip()
    return name or "(unnamed)"


def _attention_for_task(*, linked_count: int, pending_review: int) -> str:
    """Return Attention flag from link counts + review bindings only.

    Not an issue-tracker risk model. First matching rule wins:

    - ``unlinked`` — zero applied/confirmed links and no pending-review bindings
    - ``review`` — zero confirmed links, but needs_review bindings exist
    - ``broad_link`` — confirmed linked-element count >= FANOUT_OUTLIER_THRESHOLD (100)
    - ``partial`` — confirmed links exist and needs_review bindings also exist
    - ``ok`` — confirmed links in a normal range, no pending-review bindings
    """
    if linked_count <= 0:
        return "review" if pending_review > 0 else "unlinked"
    if linked_count >= FANOUT_OUTLIER_THRESHOLD:
        return "broad_link"
    if pending_review > 0:
        return "partial"
    return "ok"


_ATTENTION_LABELS = {
    "ok": "OK",
    "unlinked": "Unlinked",
    "review": "Review",
    "broad_link": "Broad link",
    "partial": "Partial",
}


def _status_for_task(*, linked_count: int, pending_review: int) -> str:
    if linked_count <= 0:
        return "unlinked"
    if pending_review > 0:
        return "partial"
    return "linked"


class LinkAnalysisService:
    """Build the 4D Link Analysis page payload (read-only)."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def build(
        self,
        *,
        task_page: int = 1,
        element_page: int = 1,
        search: str = "",
        last_diagnostic_run: str | None = None,
    ) -> dict[str, Any]:
        """Return KPIs, charts, and table payloads for the analysis screen."""
        from scheduling.models import Task, TaskEntityBinding

        ifc_file = self._completed_ifc_file()
        ifc_name = ifc_file.name if ifc_file else None
        elements_total = (
            IFCEntity.objects.filter(ifc_file=ifc_file).count() if ifc_file is not None else 0
        )

        trusted_gids = linked_entity_gids_for_project(self.project_id)
        counts = trusted_counts(self.project_id)
        linked_elements = int(counts.get("trusted_entities") or 0)
        linked_tasks = int(counts.get("trusted_tasks") or 0)
        tasks_total = Task.objects.filter(project=self.project).count()
        unlinked_elements = max(0, elements_total - linked_elements)
        fan_sizes = trusted_fanout_sizes(self.project_id)
        largest = fan_sizes[-1] if fan_sizes else 0

        # Bindings → task maps (trusted + pending for Partial / Review attention)
        trusted_by_task: dict[str, list[str]] = defaultdict(list)
        pending_by_task: Counter[str] = Counter()
        for task_id, gid, needs_review in TaskEntityBinding.objects.filter(
            task__project_id=self.project_id
        ).values_list("task_id", "entity_global_id", "needs_review"):
            tid = str(task_id)
            if needs_review:
                pending_by_task[tid] += 1
            else:
                trusted_by_task[tid].append(str(gid))

        largest_task_code = ""
        largest_task_name = ""
        if largest > 0 and trusted_by_task:
            top_tid = max(trusted_by_task.items(), key=lambda item: len(item[1]))[0]
            top_task = Task.objects.filter(pk=top_tid).only("activity_code", "name").first()
            if top_task is not None:
                largest_task_code = (top_task.activity_code or "").strip()
                largest_task_name = (top_task.name or "").strip()

        entity_meta = self._entity_meta(ifc_file, trusted_gids) if ifc_file is not None else {}

        by_class = Counter()
        for gid in trusted_gids:
            meta = entity_meta.get(gid)
            if meta:
                by_class[meta["ifc_class"]] += 1
        class_rows = by_class.most_common(CLASS_CHART_LIMIT)
        class_max = class_rows[0][1] if class_rows else 1

        hist = self._histogram(fan_sizes)

        attention_counts = self._attention_counts(
            project=self.project,
            trusted_by_task=trusted_by_task,
            pending_by_task=pending_by_task,
        )
        # Mutually exclusive Attention buckets (OK + Broad + Review + Unlinked + Partial).
        distribution = {
            "ok": attention_counts.get("ok", 0),
            "unlinked": attention_counts.get("unlinked", 0),
            "review": attention_counts.get("review", 0),
            "broad_link": attention_counts.get("broad_link", 0),
            "partial": attention_counts.get("partial", 0),
        }
        actionable_review = sum(attention_counts.get(key, 0) for key in _ACTIONABLE_REVIEW_KEYS)

        time_view = self._time_view_readiness(
            trusted_by_task=trusted_by_task,
            linked_tasks=linked_tasks,
        )

        task_pct = _share(linked_tasks, tasks_total)
        element_pct = _share(linked_elements, elements_total)
        unlinked_tasks = max(0, tasks_total - linked_tasks)
        kpis = {
            "linked_tasks": linked_tasks,
            "tasks_total": tasks_total,
            "unlinked_tasks": unlinked_tasks,
            "task_link_pct": task_pct,
            "linked_elements": linked_elements,
            "elements_total": elements_total,
            "element_link_pct": element_pct,
            "unlinked_elements": unlinked_elements,
            "actionable_review": actionable_review,
            "actionable_review_breakdown": {
                "review": distribution["review"],
                "broad_link": distribution["broad_link"],
                "partial": distribution["partial"],
            },
            # Legacy alias — same as actionable_review (excludes unlinked).
            "attention_queue": actionable_review,
            "attention_breakdown": {
                "unlinked": distribution["unlinked"],
                "review": distribution["review"],
                "broad_link": distribution["broad_link"],
                "partial": distribution["partial"],
                "ok": distribution["ok"],
            },
            "broad_link_threshold": FANOUT_OUTLIER_THRESHOLD,
            "time_view_ready": time_view["count"],
            "time_view_linked": time_view["linked_total"],
            "time_view_available": time_view["available"],
            "time_view_reason": time_view["reason"],
            # Legacy keys kept for any callers that still read them.
            "largest_task_link": largest,
            "largest_task_code": largest_task_code,
            "largest_task_name": largest_task_name,
            "largest_is_broad": largest >= FANOUT_OUTLIER_THRESHOLD,
        }

        dist_rows = []
        for key, label in _DISTRIBUTION_KEYS:
            n = distribution[key]
            dist_rows.append(
                {
                    "key": key,
                    "label": label,
                    "n": n,
                    "pct": _share(n, tasks_total),
                    "bar_pct": _share(n, tasks_total),
                }
            )

        charts = {
            "schedule_distribution": {
                "rows": dist_rows,
                "tasks_total": tasks_total,
                "linked_total": linked_tasks,
                "linked_pct": task_pct,
                "actionable_review": actionable_review,
                "attention_queue": actionable_review,
            },
            # Back-compat alias for older templates/tests during transition.
            "task_coverage": {
                "linked": linked_tasks,
                "unlinked": unlinked_tasks,
                "linked_pct": task_pct,
                "unlinked_pct": _share(unlinked_tasks, tasks_total),
                "linked_bar_pct": task_pct,
                "unlinked_bar_pct": _share(unlinked_tasks, tasks_total),
            },
            "fanout_histogram": hist,
            "outlier_max": largest,
            "outlier_threshold": FANOUT_OUTLIER_THRESHOLD,
            "show_outlier": largest >= FANOUT_OUTLIER_THRESHOLD,
            "by_ifc_class": [
                {
                    "ifc_class": name,
                    "count": n,
                    "bar_pct": round(100.0 * n / class_max, 1) if class_max else 0,
                }
                for name, n in class_rows
            ],
        }

        task_table = self._task_rows(
            trusted_by_task=trusted_by_task,
            pending_by_task=pending_by_task,
            entity_meta=entity_meta,
            page=task_page,
            search=search,
        )
        element_table = self._element_rows(
            ifc_file=ifc_file,
            trusted_gids=trusted_gids,
            trusted_by_task=trusted_by_task,
            entity_meta=entity_meta,
            page=element_page,
            search=search,
        )
        grouped = self._grouped_rows(by_class=by_class, elements_total=elements_total)

        analyzed = linked_tasks if linked_tasks else tasks_total
        diagnostics = {
            "tasks_analyzed": analyzed,
            "tasks_total": tasks_total,
            "last_run": last_diagnostic_run,
            "last_run_label": last_diagnostic_run or "unavailable",
        }

        return {
            "has_ifc": ifc_file is not None,
            "ifc_file_name": ifc_name,
            "kpis": kpis,
            "charts": charts,
            "task_table": task_table,
            "element_table": element_table,
            "grouped_rows": grouped,
            "grouped_count": len(grouped),
            "diagnostics": diagnostics,
            "honesty": {
                "links_only": True,
                "not_qto": True,
                "not_boq": True,
                "not_cost": True,
            },
        }

    def run_diagnostics(self) -> dict[str, Any]:
        """Recompute analysis snapshot metadata (no link/model mutations)."""
        payload = self.build()
        stamp = timezone.now().strftime("%d %b %Y %H:%M UTC")
        return {
            "tasks_analyzed": payload["diagnostics"]["tasks_analyzed"],
            "tasks_total": payload["diagnostics"]["tasks_total"],
            "last_run": stamp,
            "kpis": payload["kpis"],
        }

    def _completed_ifc_file(self) -> IFCFile | None:
        return (
            IFCFile.objects.filter(project=self.project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )

    def _entity_meta(self, ifc_file: IFCFile, gids: set[str]) -> dict[str, dict[str, Any]]:
        if not gids:
            return {}
        meta: dict[str, dict[str, Any]] = {}
        qs = IFCEntity.objects.filter(ifc_file=ifc_file, global_id__in=gids).values_list(
            "global_id",
            "name",
            "tag",
            "ifc_type",
            "spatial_container__spatial_type",
            "spatial_container__entity__name",
        )
        for gid, name, tag, ifc_type, sc_type, sc_name in qs.iterator(chunk_size=2000):
            display = (name or "").strip() or (tag or "").strip() or (ifc_type or "Unknown")
            meta[str(gid)] = {
                "global_id": str(gid),
                "display_name": display,
                "ifc_class": ifc_type or "Unknown",
                "level_label": _level_label(sc_type, sc_name),
            }
        return meta

    @staticmethod
    def _histogram(fan_sizes: list[int]) -> list[dict[str, Any]]:
        counts = []
        for label, lo, hi in HISTOGRAM_BUCKETS:
            if hi is None:
                n = sum(1 for v in fan_sizes if v >= lo)
            else:
                n = sum(1 for v in fan_sizes if lo <= v <= hi)
            counts.append(
                {
                    "label": label,
                    "n": n,
                    "is_outlier_bucket": hi is None or (lo >= FANOUT_OUTLIER_THRESHOLD),
                }
            )
        peak = max((row["n"] for row in counts), default=0) or 1
        for row in counts:
            row["bar_pct"] = round(100.0 * row["n"] / peak, 1)
        return counts

    @staticmethod
    def _attention_counts(
        *,
        project,
        trusted_by_task: dict[str, list[str]],
        pending_by_task: Counter[str],
    ) -> Counter[str]:
        """Count Attention flags across every project task (4D link signal only)."""
        from scheduling.models import Task

        counts: Counter[str] = Counter()
        for tid in (
            Task.objects.filter(project=project)
            .values_list("id", flat=True)
            .iterator(chunk_size=2000)
        ):
            tid_s = str(tid)
            flag = _attention_for_task(
                linked_count=len(trusted_by_task.get(tid_s, [])),
                pending_review=int(pending_by_task.get(tid_s, 0)),
            )
            counts[flag] += 1
        return counts

    @staticmethod
    def _time_view_readiness(
        *,
        trusted_by_task: dict[str, list[str]],
        linked_tasks: int,
    ) -> dict[str, Any]:
        """Count confirmed-linked tasks that have both start and finish dates.

        Unavailable when there are no confirmed links to evaluate against dates.
        """
        from scheduling.models import Task

        if linked_tasks <= 0:
            return {
                "available": False,
                "count": None,
                "linked_total": None,
                "reason": "Schedule dates or confirmed links required",
            }

        linked_ids = [tid for tid, gids in trusted_by_task.items() if gids]
        if not linked_ids:
            return {
                "available": False,
                "count": None,
                "linked_total": None,
                "reason": "Schedule dates or confirmed links required",
            }

        linked_total = len(linked_ids)
        ready = Task.objects.filter(
            pk__in=linked_ids,
            start_date__isnull=False,
            end_date__isnull=False,
        ).count()
        return {
            "available": True,
            "count": ready,
            "linked_total": linked_total,
            "reason": "",
        }

    def _task_rows(
        self,
        *,
        trusted_by_task: dict[str, list[str]],
        pending_by_task: Counter[str],
        entity_meta: dict[str, dict[str, Any]],
        page: int,
        search: str,
    ) -> dict[str, Any]:
        from scheduling.models import Task

        page = max(1, int(page or 1))
        q = (search or "").strip().lower()
        qs = Task.objects.filter(project=self.project).only(
            "id", "name", "description", "activity_code", "start_date", "end_date"
        )
        if q:
            from django.db.models import Q

            qs = qs.filter(
                Q(name__icontains=q) | Q(activity_code__icontains=q) | Q(description__icontains=q)
            )

        # Prefer linked tasks first for review density.
        all_ids = list(qs.values_list("id", flat=True))
        linked_ids = [tid for tid in all_ids if str(tid) in trusted_by_task]
        unlinked_ids = [tid for tid in all_ids if str(tid) not in trusted_by_task]
        ordered_ids = linked_ids + unlinked_ids
        total = len(ordered_ids)
        total_pages = max(1, (total + TASK_PAGE_SIZE - 1) // TASK_PAGE_SIZE) if total else 1
        if page > total_pages:
            page = total_pages
        start = (page - 1) * TASK_PAGE_SIZE
        page_ids = ordered_ids[start : start + TASK_PAGE_SIZE]
        tasks = {str(t.pk): t for t in Task.objects.filter(pk__in=page_ids)}

        rows: list[dict[str, Any]] = []
        for tid in page_ids:
            task = tasks.get(str(tid))
            if task is None:
                continue
            gids = trusted_by_task.get(str(tid), [])
            linked_count = len(gids)
            classes: list[str] = []
            levels: list[str] = []
            seen_c: set[str] = set()
            seen_l: set[str] = set()
            for gid in gids:
                m = entity_meta.get(gid)
                if not m:
                    continue
                if m["ifc_class"] not in seen_c:
                    seen_c.add(m["ifc_class"])
                    classes.append(m["ifc_class"])
                if m["level_label"] not in seen_l:
                    seen_l.add(m["level_label"])
                    levels.append(m["level_label"])
            status = _status_for_task(
                linked_count=linked_count, pending_review=pending_by_task.get(str(tid), 0)
            )
            attention = _attention_for_task(
                linked_count=linked_count, pending_review=pending_by_task.get(str(tid), 0)
            )
            preview = []
            for gid in gids[:EXPANSION_PREVIEW_LIMIT]:
                m = entity_meta.get(gid)
                if not m:
                    preview.append(
                        {
                            "global_id": gid,
                            "display_name": gid[:12] + "…",
                            "ifc_class": "Unavailable",
                            "level_label": "Unavailable",
                            "link_status": "linked",
                        }
                    )
                    continue
                preview.append(
                    {
                        "global_id": m["global_id"],
                        "display_name": m["display_name"],
                        "ifc_class": m["ifc_class"],
                        "level_label": m["level_label"],
                        "link_status": "linked",
                    }
                )
            code = (task.activity_code or "").strip() or str(task.pk)[:8]
            rows.append(
                {
                    "id": str(task.pk),
                    "task_code": code,
                    "description": (task.description or "").strip() or task.name,
                    "start_date": task.start_date.isoformat() if task.start_date else "—",
                    "finish_date": task.end_date.isoformat() if task.end_date else "—",
                    "linked_elements": linked_count,
                    "ifc_class_mix": ", ".join(classes[:4]) if classes else "None",
                    "level_mix": ", ".join(levels[:4]) if levels else "None",
                    "status": status,
                    "status_label": status.title(),
                    "attention": attention,
                    "attention_label": _ATTENTION_LABELS.get(attention, attention.title()),
                    "expansion": preview,
                    "expansion_more": max(0, linked_count - len(preview)),
                }
            )

        return {
            "rows": rows,
            "page": page,
            "page_size": TASK_PAGE_SIZE,
            "total": total,
            "total_pages": total_pages,
            "linked_count": len(linked_ids),
            "has_prev": page > 1,
            "has_next": page < total_pages,
        }

    def _element_rows(
        self,
        *,
        ifc_file: IFCFile | None,
        trusted_gids: set[str],
        trusted_by_task: dict[str, list[str]],
        entity_meta: dict[str, dict[str, Any]],
        page: int,
        search: str,
    ) -> dict[str, Any]:
        from scheduling.models import Task

        page = max(1, int(page or 1))
        q = (search or "").strip().lower()

        # Invert task→gids for first linked task label
        gid_to_task: dict[str, str] = {}
        for tid, gids in trusted_by_task.items():
            for gid in gids:
                gid_to_task.setdefault(gid, tid)

        task_labels: dict[str, str] = {}
        if gid_to_task:
            for t in Task.objects.filter(pk__in=set(gid_to_task.values())).only(
                "id", "activity_code", "name"
            ):
                task_labels[str(t.pk)] = (t.activity_code or "").strip() or t.name

        # Linked first (from meta), then sample unlinked from DB if needed
        linked_list = sorted(trusted_gids)
        if q:
            linked_list = [
                gid
                for gid in linked_list
                if q in gid.lower()
                or q in (entity_meta.get(gid) or {}).get("display_name", "").lower()
                or q in (entity_meta.get(gid) or {}).get("ifc_class", "").lower()
            ]

        total_linked = len(linked_list)
        # Element Rows badge focuses on linked elements (Figma 6472).
        total = total_linked
        total_pages = max(1, (total + ELEMENT_PAGE_SIZE - 1) // ELEMENT_PAGE_SIZE) if total else 1
        if page > total_pages:
            page = total_pages
        start = (page - 1) * ELEMENT_PAGE_SIZE
        page_gids = linked_list[start : start + ELEMENT_PAGE_SIZE]

        rows: list[dict[str, Any]] = []
        for gid in page_gids:
            m = entity_meta.get(gid) or {
                "global_id": gid,
                "display_name": gid[:16],
                "ifc_class": "Unavailable",
                "level_label": "Unavailable",
            }
            tid = gid_to_task.get(gid)
            rows.append(
                {
                    "element_id": m["global_id"],
                    "display_name": m["display_name"],
                    "ifc_class": m["ifc_class"],
                    "level_label": m["level_label"],
                    "linked_task": task_labels.get(tid or "", "Unavailable"),
                    "link_status": "linked",
                    "link_status_label": "Linked",
                }
            )

        # Optional unlinked peek when no linked rows and IFC exists
        if not rows and ifc_file is not None and not trusted_gids:
            qs = IFCEntity.objects.filter(ifc_file=ifc_file).values_list(
                "global_id",
                "name",
                "tag",
                "ifc_type",
                "spatial_container__spatial_type",
                "spatial_container__entity__name",
            )[:ELEMENT_PAGE_SIZE]
            for gid, name, tag, ifc_type, sc_type, sc_name in qs:
                display = (name or "").strip() or (tag or "").strip() or (ifc_type or "Unknown")
                rows.append(
                    {
                        "element_id": str(gid),
                        "display_name": display,
                        "ifc_class": ifc_type or "Unknown",
                        "level_label": _level_label(sc_type, sc_name),
                        "linked_task": "—",
                        "link_status": "unlinked",
                        "link_status_label": "Unlinked",
                    }
                )
            total = IFCEntity.objects.filter(ifc_file=ifc_file).count()

        return {
            "rows": rows,
            "page": page,
            "page_size": ELEMENT_PAGE_SIZE,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        }

    @staticmethod
    def _grouped_rows(*, by_class: Counter, elements_total: int) -> list[dict[str, Any]]:
        rows = []
        for ifc_class, n in by_class.most_common(40):
            rows.append(
                {
                    "group_key": ifc_class,
                    "group_dimension": "IFC class",
                    "linked_elements": n,
                    "share_pct": _share(n, elements_total) if elements_total else 0.0,
                    "status": "linked",
                    "status_label": "Linked",
                }
            )
        return rows
