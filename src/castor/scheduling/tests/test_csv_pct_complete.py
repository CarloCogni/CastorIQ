# castor/scheduling/tests/test_csv_pct_complete.py
"""Phase-3 tests: CSV %-complete support (S2) and freshness disclosures (S3).

Covers:
  - _parse_pct  normalisation (0-100 and 0-1 inputs)
  - apply_mapping  emits _csv_pct_complete
  - _earned_pct_at  uses real pct instead of linear when present
  - compute_evm  overdue_linear_capped count
  - compute_evm  SPI difference with vs. without pct data
"""

from __future__ import annotations

import datetime

import pytest

from castor.scheduling.services.column_mapper import _parse_pct, apply_mapping
from castor.scheduling.services.evm import _earned_pct_at, compute_evm
from castor.scheduling.tests.factories import TaskFactory
from environments.tests.factories import ProjectFactory

# ---------------------------------------------------------------------------
# _parse_pct — no DB
# ---------------------------------------------------------------------------

PARSE_PCT_CASES = [
    # (input_str, expected)
    ("75", 0.75),
    ("75%", 0.75),
    ("75.5", 0.755),
    ("100", 1.0),
    ("100%", 1.0),
    ("0", 0.0),
    ("0%", 0.0),
    ("0.5", 0.5),  # 0-1 scale kept as-is
    ("1", 1.0),  # 1 ≤ 1 → kept as 1.0 (100%)
    ("0.0", 0.0),
    ("1.0", 1.0),
    ("", None),
    ("abc", None),
    ("-5", 0.0),  # negative clamped to 0
    ("101", 1.0),  # capped to 100% via normalize_pct_complete
    (" 50 ", 0.5),  # whitespace stripped
    ("50.0%", 0.5),
]


@pytest.mark.parametrize("raw, expected", PARSE_PCT_CASES)
def test_parse_pct_normalization(raw, expected):
    result = _parse_pct(raw)
    if expected is None:
        assert result is None, f"Expected None for {raw!r}, got {result}"
    else:
        assert result == pytest.approx(expected, abs=1e-4), (
            f"_parse_pct({raw!r}) → {result}, expected {expected}"
        )


# ---------------------------------------------------------------------------
# apply_mapping — no DB
# ---------------------------------------------------------------------------

_HEADERS = ["Task", "Start", "Finish", "% Complete"]
_ROWS_WITH_PCT = [["Foundation", "2025-01-01", "2025-03-31", "60"]]
_ROWS_NO_PCT = [["Foundation", "2025-01-01", "2025-03-31", ""]]
_MAPPING_WITH = {
    "name": "Task",
    "start_date": "Start",
    "end_date": "Finish",
    "percent_complete": "% Complete",
}
_MAPPING_WITHOUT = {"name": "Task", "start_date": "Start", "end_date": "Finish"}


def test_apply_mapping_with_pct_column_populates_csv_pct():
    """When a % Complete column is mapped, the task dict carries _csv_pct_complete."""
    tasks = apply_mapping(_HEADERS, _ROWS_WITH_PCT, _MAPPING_WITH, "csv")
    assert len(tasks) == 1
    assert tasks[0]["_csv_pct_complete"] == pytest.approx(0.6, abs=1e-4)


def test_apply_mapping_without_pct_column_is_none():
    """Without a % Complete mapping, _csv_pct_complete is None."""
    tasks = apply_mapping(_HEADERS, _ROWS_WITH_PCT, _MAPPING_WITHOUT, "csv")
    assert len(tasks) == 1
    assert tasks[0]["_csv_pct_complete"] is None


def test_apply_mapping_empty_pct_cell_is_none():
    """An empty % Complete cell also yields None (not 0.0)."""
    tasks = apply_mapping(_HEADERS, _ROWS_NO_PCT, _MAPPING_WITH, "csv")
    assert tasks[0]["_csv_pct_complete"] is None


def test_apply_mapping_0_to_100_vs_0_to_1():
    """A 0-100 value and its 0-1 equivalent both normalise to the same stored value."""
    rows_100 = [["T", "2025-01-01", "2025-03-31", "50"]]
    rows_01 = [["T", "2025-01-01", "2025-03-31", "0.5"]]
    mapping = {
        "name": "Task",
        "start_date": "Start",
        "end_date": "Finish",
        "percent_complete": "% Complete",
    }
    assert apply_mapping(_HEADERS, rows_100, mapping, "csv")[0][
        "_csv_pct_complete"
    ] == pytest.approx(0.5, abs=1e-4)
    assert apply_mapping(_HEADERS, rows_01, mapping, "csv")[0][
        "_csv_pct_complete"
    ] == pytest.approx(0.5, abs=1e-4)


# ---------------------------------------------------------------------------
# _earned_pct_at — unit, no DB
# ---------------------------------------------------------------------------


class _FakeTask:
    """Minimal Task stand-in for _earned_pct_at unit tests."""

    def __init__(
        self,
        *,
        actual_start=None,
        actual_end=None,
        end_date,
        status="active",
        physical_percent_complete=None,
        duration_percent_complete=None,
        start_date=None,
    ):
        self.actual_start = actual_start
        self.actual_end = actual_end
        self.end_date = end_date
        self.start_date = start_date or end_date
        self.status = status
        self.physical_percent_complete = physical_percent_complete
        self.duration_percent_complete = duration_percent_complete


_TODAY = datetime.date(2025, 6, 1)
_PAST_START = datetime.date(2025, 1, 1)
_PAST_END = datetime.date(2025, 3, 31)  # < _TODAY → overdue


def test_earned_pct_uses_physical_pct_not_linear():
    """physical_percent_complete=0.4 on an overdue task returns 0.4, not 1.0."""
    task = _FakeTask(
        actual_start=_PAST_START,
        end_date=_PAST_END,
        status="active",
        physical_percent_complete=0.4,
    )
    result = _earned_pct_at(task, _TODAY)
    assert result == pytest.approx(0.4, abs=1e-6), (
        f"Expected EV=0.4 (from physical pct), got {result}"
    )


def test_earned_pct_uses_duration_pct_when_physical_absent():
    """duration_percent_complete=0.6 used when physical is absent."""
    task = _FakeTask(
        actual_start=_PAST_START,
        end_date=_PAST_END,
        status="active",
        physical_percent_complete=None,
        duration_percent_complete=0.6,
    )
    result = _earned_pct_at(task, _TODAY)
    assert result == pytest.approx(0.6, abs=1e-6)


def test_earned_pct_linear_cap_overdue_no_data():
    """No pct data on an overdue task: linear clamp returns 1.0."""
    task = _FakeTask(
        actual_start=_PAST_START,
        end_date=_PAST_END,
        status="active",
        physical_percent_complete=None,
        duration_percent_complete=None,
    )
    result = _earned_pct_at(task, _TODAY)
    assert result == pytest.approx(1.0, abs=1e-6), (
        "Overdue task with no pct data should be capped at 1.0 by linear fallback"
    )


# ---------------------------------------------------------------------------
# compute_evm — DB-backed integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_compute_evm_overdue_linear_capped_count():
    """overdue_linear_capped counts only active overdue tasks with no pct data."""
    project = ProjectFactory()
    today = datetime.date.today()
    long_past = today - datetime.timedelta(days=90)
    past_end = today - datetime.timedelta(days=10)

    # Task 1: overdue, no pct data → should be counted
    TaskFactory(
        project=project,
        status="active",
        actual_start=long_past,
        end_date=past_end,
        physical_percent_complete=None,
        duration_percent_complete=None,
    )
    # Task 2: overdue BUT has physical pct → NOT counted
    TaskFactory(
        project=project,
        status="active",
        actual_start=long_past,
        end_date=past_end,
        physical_percent_complete=0.5,
        duration_percent_complete=None,
    )
    # Task 3: overdue, no pct, but status=complete → NOT counted
    TaskFactory(
        project=project,
        status="complete",
        actual_start=long_past,
        end_date=past_end,
        physical_percent_complete=None,
        duration_percent_complete=None,
    )
    # Task 4: NOT overdue (end_date in future) → NOT counted
    TaskFactory(
        project=project,
        status="active",
        actual_start=long_past,
        end_date=today + datetime.timedelta(days=30),
        physical_percent_complete=None,
        duration_percent_complete=None,
    )

    result = compute_evm(str(project.pk), as_of_date=today)
    assert result["has_data"] is True
    assert result["overdue_linear_capped"] == 1, (
        f"Expected 1 overdue-capped task, got {result['overdue_linear_capped']}"
    )


@pytest.mark.django_db
def test_compute_evm_spi_higher_without_pct_data():
    """Without pct data, overdue active tasks inflate SPI.  With pct=0.4, SPI is lower."""
    today = datetime.date.today()
    long_past = today - datetime.timedelta(days=90)
    past_end = today - datetime.timedelta(days=10)
    future_end = today + datetime.timedelta(days=30)

    # --- Project A: overdue active task with NO pct data ---
    project_a = ProjectFactory()
    TaskFactory(
        project=project_a,
        status="active",
        actual_start=long_past,
        end_date=past_end,
        physical_percent_complete=None,
        duration_percent_complete=None,
    )
    TaskFactory(project=project_a, status="planned", end_date=future_end)

    # --- Project B: identical except the overdue task has pct=0.4 ---
    project_b = ProjectFactory()
    TaskFactory(
        project=project_b,
        status="active",
        actual_start=long_past,
        end_date=past_end,
        physical_percent_complete=0.4,
        duration_percent_complete=None,
    )
    TaskFactory(project=project_b, status="planned", end_date=future_end)

    result_a = compute_evm(str(project_a.pk), as_of_date=today)
    result_b = compute_evm(str(project_b.pk), as_of_date=today)

    assert result_a["has_data"] is True
    assert result_b["has_data"] is True

    # SPI without pct (linear cap at 100%) should be ≥ SPI with pct=0.4
    assert result_a["spi"] > result_b["spi"], (
        f"Expected SPI to be lower when real pct data is present "
        f"(A={result_a['spi']}, B={result_b['spi']})"
    )
    # Project A has 0 overdue-capped; project B has 1
    assert result_b["overdue_linear_capped"] == 0
    assert result_a["overdue_linear_capped"] == 1
