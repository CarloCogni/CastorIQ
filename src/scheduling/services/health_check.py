# castor/scheduling/services/health_check.py
"""Schedule health checks — rule-based quality assertions on persisted tasks."""

from __future__ import annotations

import logging
from collections import Counter

logger = logging.getLogger(__name__)

_SCORE_LABELS = [
    (90, "Excellent"),
    (70, "Good"),
    (50, "Fair"),
    (0, "Poor"),
]


def _score_label(score: int) -> str:
    for threshold, label in _SCORE_LABELS:
        if score >= threshold:
            return label
    return "Poor"


def run_health_check(project) -> dict:
    """Run all health checks for a project's schedule tasks.

    Returns:
        dict with keys:
          total_tasks  – int
          score        – int 0–100, or None if no tasks
          label        – str: "Excellent" / "Good" / "Fair" / "Poor"
          issues       – list of failed checks, each: {severity, code, label, description, count, sample}
          passed       – list of passed checks, each: {code, label}
    """
    from ..models import Task
    from .link_resolver import entity_gids_by_task

    tasks = list(Task.objects.filter(project=project))
    link_map = entity_gids_by_task(project.pk, [t.pk for t in tasks]) if tasks else {}
    total = len(tasks)

    if total == 0:
        return {
            "total_tasks": 0,
            "score": None,
            "label": "—",
            "issues": [],
            "passed": [],
        }

    all_checks = [
        _check_inverted_dates(tasks),
        _check_duplicate_codes(tasks),
        _check_actual_anomaly(tasks),
        _check_missing_codes(tasks),
        _check_long_tasks(tasks),
        _check_unlinked_physical(tasks, link_map),
    ]

    issues = [c for c in all_checks if c["count"] > 0]
    passed = [{"code": c["code"], "label": c["label"]} for c in all_checks if c["count"] == 0]

    error_hits = sum(1 for c in issues if c["severity"] == "error")
    warning_hits = sum(1 for c in issues if c["severity"] == "warning")
    score = max(0, 100 - error_hits * 20 - warning_hits * 5)

    return {
        "total_tasks": total,
        "score": score,
        "label": _score_label(score),
        "issues": issues,
        "passed": passed,
    }


def _check_inverted_dates(tasks: list) -> dict:
    hits = [t.name for t in tasks if t.end_date and t.start_date and t.end_date < t.start_date]
    return {
        "code": "inverted_dates",
        "label": "End before Start",
        "description": "Tasks where the end date is earlier than the start date.",
        "count": len(hits),
        "severity": "error",
        "sample": hits[:3],
    }


def _check_duplicate_codes(tasks: list) -> dict:
    codes = [t.activity_code for t in tasks if t.activity_code]
    dupes = [code for code, cnt in Counter(codes).items() if cnt > 1]
    return {
        "code": "duplicate_codes",
        "label": "Duplicate Activity Codes",
        "description": "Multiple tasks share the same activity code — IFC linking will be ambiguous.",
        "count": len(dupes),
        "severity": "error",
        "sample": dupes[:3],
    }


def _check_actual_anomaly(tasks: list) -> dict:
    hits = [
        t.name for t in tasks if t.actual_start and t.actual_end and t.actual_end < t.actual_start
    ]
    return {
        "code": "actual_anomaly",
        "label": "Actual End before Actual Start",
        "description": "Tasks where the recorded actual end date precedes the actual start.",
        "count": len(hits),
        "severity": "error",
        "sample": hits[:3],
    }


def _check_missing_codes(tasks: list) -> dict:
    hits = [t.name for t in tasks if not t.activity_code]
    return {
        "code": "missing_codes",
        "label": "Missing Activity Codes",
        "description": "Tasks without an activity code cannot be linked to IFC elements.",
        "count": len(hits),
        "severity": "warning",
        "sample": hits[:3],
    }


def _check_long_tasks(tasks: list) -> dict:
    threshold = 730
    hits = [
        t.name
        for t in tasks
        if t.start_date and t.end_date and (t.end_date - t.start_date).days > threshold
    ]
    return {
        "code": "long_tasks",
        "label": "Suspiciously Long Tasks",
        "description": f"Tasks spanning more than {threshold} days — may indicate a data error.",
        "count": len(hits),
        "severity": "warning",
        "sample": hits[:3],
    }


def _check_unlinked_physical(tasks: list, link_map: dict[str, list[str]]) -> dict:
    hits = [t.name for t in tasks if not t.is_non_physical and not link_map.get(str(t.pk))]
    return {
        "code": "unlinked_physical",
        "label": "Unlinked Physical Tasks",
        "description": "Physical tasks with no IFC element links. Run Smart Link or review bindings.",
        "count": len(hits),
        "severity": "info",
        "sample": hits[:3],
    }
