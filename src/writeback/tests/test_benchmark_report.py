# writeback/tests/test_benchmark_report.py
"""The report renders the V3 columns and diffs two artifacts by pass/fail (spec B-2)."""

import json

import pytest

from writeback.services.benchmark.report import BenchmarkReport, diff_runs, render_report
from writeback.services.benchmark.runner import CaseResult


def _result(case_id, kind="change", **overrides):
    base = dict(
        case_id=case_id,
        section_number="1",
        prompt=f"prompt {case_id}",
        expectation="e",
        kind=kind,
        advisory=False,
        outcome="proposal",
        attempts=1,
        targets_match=True,
        diff_match=True,
        integrity_ok=True,
        duration_seconds=1.0,
    )
    base.update(overrides)
    return CaseResult(**base)


def _report(label="ollama:qwen2.5-coder:7b"):
    return BenchmarkReport(
        model_label=label,
        started_at="2026-09-15T10:00:00",
        results=[
            _result("1.1"),
            _result(
                "1.2", targets_match=False, diff_match=False, attempts=3, duration_seconds=40.0
            ),
            # A change case the model refused: scored false on both columns.
            _result(
                "1.3",
                outcome="rejected",
                targets_match=False,
                diff_match=False,
                integrity_ok=None,
                attempts=3,
            ),
            _result(
                "13.1",
                kind="reject",
                outcome="rejected",
                targets_match=None,
                diff_match=None,
                integrity_ok=None,
            ),
            _result(
                "4.4",
                kind="no_change",
                outcome="rejected",
                targets_match=None,
                diff_match=None,
                integrity_ok=None,
            ),
            _result("2.4", kind="advisory", advisory=True, outcome="rejected"),
            # A model that could not answer: outside every denominator, listed on its own.
            _result(
                "3.1",
                outcome="error",
                error="ModelUnavailableError: timeout",
                targets_match=None,
                diff_match=None,
                integrity_ok=None,
                duration_seconds=240.0,
            ),
        ],
        tokens_in=1000,
        tokens_out=200,
        estimated_cost_usd=0.0,
        notes={"vram": "8GB"},
    )


def test_totals_count_each_column():
    """passed, targets, diff, integrity, reject/no-change, repairs and latency are all there."""
    totals = _report().as_dict()["totals"]

    assert totals["scored"] == 5 and totals["advisory"] == 1
    assert totals["passed"] == 2
    assert (totals["targets_passed"], totals["targets_scored"]) == (1, 3)
    assert (totals["diff_passed"], totals["diff_scored"]) == (1, 3)
    assert (totals["integrity_passed"], totals["integrity_scored"]) == (2, 2)
    assert (totals["reject_passed"], totals["reject_scored"]) == (1, 2)
    assert totals["repairs"] == 4
    assert totals["latency_p90_s"] >= totals["latency_median_s"]
    assert _report().as_dict()["notes"] == {"vram": "8GB"}


def test_an_errored_case_is_outside_every_denominator_and_the_latency_sample():
    """A harness error is listed on its own; it is not a failure and its 240 s is not a request."""
    report = _report()
    errored = [r for r in report.errored]

    assert [r.case_id for r in errored] == ["3.1"]
    assert all(r.error == "" for r in report.scored)
    assert all(r.error == "" for r in report.failures)
    assert report.latency()[1] < 240.0
    assert "ERROR   3.1" in render_report([report])


@pytest.mark.parametrize(
    ("durations", "p90"),
    [
        ([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 6.0),  # n=6: ceil(5.4)=6 → the 6th value
        ([float(i) for i in range(1, 11)], 9.0),  # n=10: the 9th value
        ([7.0], 7.0),
    ],
)
def test_p90_is_the_nearest_rank(durations, p90):
    """The smallest value at or above 90% of the sample, no banker's rounding."""
    report = BenchmarkReport(
        model_label="m",
        started_at="t",
        results=[_result(str(i), duration_seconds=d) for i, d in enumerate(durations)],
    )
    assert report.latency()[1] == p90


def test_render_report_shows_the_new_columns_and_the_failures():
    """The table has the V3 rows and each failure names its expectation and outcome."""
    text = render_report([_report(), _report("anthropic:claude-sonnet-4-6")])

    for label in (
        "targets match",
        "diff match",
        "integrity",
        "reject / no-change",
        "repairs used",
        "latency p90",
    ):
        assert label in text
    assert "FAIL    1.2" in text
    assert "FAIL    4.4" in text
    assert "anthropic:claude-sonnet-4-6" in text


def test_diff_runs_reports_regressed_and_fixed_by_pass_state(tmp_path):
    """A case that passed in the baseline and fails now is REGRESSED; the reverse is FIXED."""
    baseline = _report()
    baseline_dict = json.loads(json.dumps(baseline.as_dict()))
    current = _report()
    current.results[0] = _result("1.1", targets_match=False)
    current.results[1] = _result("1.2")

    text = diff_runs(baseline_dict, current)

    assert "REGRESSED    1.1" in text
    assert "FIXED        1.2" in text


def test_write_json_round_trips(tmp_path):
    """The artifact is valid JSON with the cases inside."""
    path = _report().write_json(tmp_path / "runs" / "x.json")
    data = json.loads(path.read_text())
    assert data["model"] == "ollama:qwen2.5-coder:7b"
    assert len(data["cases"]) == 7
    assert data["cases"][0]["passed"] is True
    assert data["cases"][0]["runs"] == 1
