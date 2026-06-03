# castor/scheduling/services/calendar_utils.py
"""Project-calendar arithmetic shared across scheduling analytics.

Builds cal dicts from P6Calendar records ({working_day_names, holiday_dates}) and
provides working-day arithmetic: load_project_calendars(), task_cal(),
count_working_days(), working_day_diff(). Falls back to _DEFAULT_CAL (all days
working, no holidays) when no P6 calendar data is available.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


_DEFAULT_CAL: dict = {
    "working_day_names": {
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    },
    "holiday_dates": frozenset(),
}


def _is_working_day(d: date, cal: dict) -> bool:
    return d.strftime("%A") in cal["working_day_names"] and d not in cal["holiday_dates"]


def _next_working_day(d: date, cal: dict) -> date:
    """Return *d* if it is a working day, otherwise advance to the next working day."""
    while not _is_working_day(d, cal):
        d += timedelta(days=1)
    return d


def _prev_working_day(d: date, cal: dict) -> date:
    """Return *d* if it is a working day, otherwise step back to the prior working day."""
    while not _is_working_day(d, cal):
        d -= timedelta(days=1)
    return d


def _add_working_days(start: date, n: int, cal: dict) -> date:
    """Advance *n* working days from *start* (0 means same day if working)."""
    if n == 0:
        return _next_working_day(start, cal)
    d = start
    remaining = n
    while remaining > 0:
        d += timedelta(days=1)
        if _is_working_day(d, cal):
            remaining -= 1
    return d


def _subtract_working_days(start: date, n: int, cal: dict) -> date:
    """Step back *n* working days from *start*."""
    if n == 0:
        return _prev_working_day(start, cal)
    d = start
    remaining = n
    while remaining > 0:
        d -= timedelta(days=1)
        if _is_working_day(d, cal):
            remaining -= 1
    return d


def _count_working_days(start: date, end: date, cal: dict) -> int:
    """Count working days in the inclusive range [start, end]."""
    if end < start:
        return 1
    # Fast path: 7-day no-holiday calendar degenerates to calendar days.
    if len(cal["working_day_names"]) == 7 and not cal["holiday_dates"]:
        return (end - start).days + 1
    count = 0
    d = start
    while d <= end:
        if _is_working_day(d, cal):
            count += 1
        d += timedelta(days=1)
    return max(count, 1)


def count_working_days(start: date, end: date, cal: dict) -> int:
    """Count working days in the inclusive range [start, end].

    Equivalent to a task duration in P6 (both endpoints count as day-1 and day-N).
    Returns at least 1 even when start == end.
    """
    return _count_working_days(start, end, cal)


def working_day_diff(d1: date, d2: date, cal: dict) -> int:
    """Signed working-day count in the half-open range (d1, d2].

    Positive when d2 > d1 (d2 is later), negative when d2 < d1, zero when equal.

    This is the working-day analogue of ``(d2 - d1).days``.  Use it for finish
    variance, elapsed-vs-planned, and any other "days between two dates" metric
    where d1 is the baseline and d2 is the actual/forecast value.

    For a 7-day no-holiday calendar the result equals (d2 - d1).days exactly.
    """
    if d1 == d2:
        return 0
    if d2 > d1:
        # Fast path: 7-day no-holiday calendar
        if len(cal["working_day_names"]) == 7 and not cal["holiday_dates"]:
            return (d2 - d1).days
        count = 0
        d = d1 + timedelta(days=1)
        while d <= d2:
            if _is_working_day(d, cal):
                count += 1
            d += timedelta(days=1)
        return count
    # d2 < d1: negate
    return -working_day_diff(d2, d1, cal)


def load_project_calendars(project_id: str) -> dict[str, dict]:
    """Return a ``{p6_calendar_id: cal_dict}`` map for the project.

    Returns an empty dict when no P6Calendar records exist (non-P6 imports,
    or imports that pre-date calendar parsing).  Callers fall back to
    ``_DEFAULT_CAL`` via ``task_cal()``.
    """
    try:
        from castor.scheduling.models import P6Calendar

        cals: dict[str, dict] = {}
        for cal in P6Calendar.objects.filter(project_id=project_id, is_pending=False):
            cals[cal.p6_calendar_id] = {
                "working_day_names": set(cal.working_days or []),
                "holiday_dates": frozenset(
                    date.fromisoformat(d) for d in (cal.holidays or []) if len(d) == 10
                ),
            }
        return cals
    except Exception as exc:
        logger.debug("Calendar load skipped: %s", exc)
        return {}


_load_project_calendars = load_project_calendars


def task_cal(task, cal_map: dict[str, dict]) -> dict:
    """Return the runtime cal dict for *task*, falling back to ``_DEFAULT_CAL``.

    Uses ``task.calendar_object_id`` as the lookup key.
    """
    if cal_map and task.calendar_object_id:
        return cal_map.get(task.calendar_object_id, _DEFAULT_CAL)
    return _DEFAULT_CAL


def calendar_basis_note(cal_map: dict[str, dict]) -> str:
    """Human-readable basis note for API responses and UI labels."""
    if not cal_map:
        return "calendar days (no P6 calendar imported)"
        # (full description is surfaced separately via the calendar card)
    return "working days per project calendar"
