# scheduling/tests/test_evm_series_equivalence.py
"""DF-B2.1 PV fast-path equivalence vs legacy linear calendar semantics."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scheduling.services.evm import _planned_pct_at_dates
from scheduling.services.evm_series import cumulative_linear_calendar, pv_entries_from_tasks


def _legacy_pv_at(entries: list[tuple[date, date, float]], d: date) -> float:
    return sum(_planned_pct_at_dates(ps, pf, d, None) * w for ps, pf, w in entries)


@pytest.mark.parametrize(
    "starts,ends,weights,spine_days",
    [
        ([date(2025, 1, 1)], [date(2025, 6, 1)], [100.0], 20),
        (
            [date(2025, 1, 1), date(2025, 3, 1)],
            [date(2025, 12, 1), date(2025, 8, 1)],
            [50.0, 75.0],
            40,
        ),
    ],
)
def test_cumulative_linear_calendar_matches_legacy(starts, ends, weights, spine_days):
    entries = list(zip(starts, ends, weights, strict=True))
    spine = [date(2025, 1, 1) + timedelta(weeks=i) for i in range(spine_days)]
    fast = cumulative_linear_calendar(entries, spine)
    for i, d in enumerate(spine):
        legacy = _legacy_pv_at(entries, d)
        assert fast[i] == pytest.approx(legacy, rel=1e-9, abs=1e-6)


@pytest.mark.django_db
def test_pv_entries_from_tasks_matches_task_dates():
    from environments.tests.factories import ProjectFactory
    from scheduling.tests.factories import TaskFactory

    project = ProjectFactory()
    t = TaskFactory(
        project=project,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 3, 1),
        cost=200,
    )
    values = {str(t.pk): 200.0}
    rows = pv_entries_from_tasks([t], values)
    assert rows == [(date(2025, 1, 1), date(2025, 3, 1), 200.0)]
