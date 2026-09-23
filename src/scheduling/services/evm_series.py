# scheduling/services/evm_series.py
"""Optimized EVM spine series helpers — calendar-day linear fast path (DF-B2.1)."""

from __future__ import annotations

from datetime import date


def cumulative_linear_calendar(
    entries: list[tuple[date, date, float]],
    spine: list[date],
) -> list[float]:
    """Cumulative value at spine dates using calendar-day linear planned semantics.

    Matches repeated ``_planned_pct_at_dates(start, end, d, cal=None) * weight`` summation.
    """
    if not entries or not spine:
        return [0.0] * len(spine)
    min_start = min(s for s, _, _ in entries)
    max_spine = max(spine)
    span = max(1, (max_spine - min_start).days + 1)
    daily = [0.0] * span
    for start, end, weight in entries:
        if start is None or end is None or weight == 0:
            continue
        dur = max((end - start).days, 1)
        rate = weight / dur
        lo = max(0, (start - min_start).days)
        hi = min(span, (end - min_start).days)
        for i in range(lo, hi):
            daily[i] += rate
    pref = [0.0]
    for value in daily:
        pref.append(pref[-1] + value)
    out: list[float] = []
    for d in spine:
        if d < min_start:
            out.append(0.0)
            continue
        idx = min((d - min_start).days, span)
        out.append(pref[idx])
    return out


def pv_entries_from_tasks(
    calc_tasks,
    values: dict[str, float],
    *,
    pv_slices: list[tuple] | None = None,
    use_baseline_planned: bool = False,
) -> list[tuple[date, date, float]]:
    """Build (start, end, weight) rows for planned-value spine computation."""
    if use_baseline_planned and pv_slices:
        rows: list[tuple[date, date, float]] = []
        for task, ps, pf, _cal in pv_slices:
            rows.append((ps, pf, values[str(task.pk)]))
        return rows
    return [
        (t.start_date, t.end_date, values[str(t.pk)])
        for t in calc_tasks
        if t.start_date and t.end_date
    ]


def earned_value_at_date(task, d: date, weight: float, cal=None) -> float:
    """Single-task earned value — delegates to evm._earned_pct_at."""
    from scheduling.services.evm import _earned_pct_at

    return _earned_pct_at(task, d, cal) * weight


def cumulative_earned_at_spine(
    ev_items: list[tuple[object, float, object | None]],
    spine: list[date],
    today: date,
) -> list[float]:
    """Earned cumulative at spine dates (per-task _earned_pct_at, d <= today only)."""
    out: list[float] = []
    for d in spine:
        if d > today:
            break
        total = 0.0
        for task, weight, cal in ev_items:
            total += earned_value_at_date(task, d, weight, cal)
        out.append(total)
    return out
