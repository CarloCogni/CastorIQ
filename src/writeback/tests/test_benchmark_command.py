# writeback/tests/test_benchmark_command.py
"""``--repeat`` keeps the worst scored run (spec B-3), with no LLM: the runner is scripted."""

from unittest.mock import MagicMock

from writeback.management.commands.benchmark_writeback import Command
from writeback.services.benchmark.runner import CaseResult


def _result(passed: bool, error: str = "", advisory: bool = False) -> CaseResult:
    return CaseResult(
        case_id="1.1",
        section_number="1",
        prompt="p",
        expectation="e",
        kind="advisory" if advisory else "change",
        advisory=advisory,
        outcome="error" if error else "proposal",
        targets_match=None if error else passed,
        error=error,
    )


def _runner(*results: CaseResult) -> MagicMock:
    runner = MagicMock()
    runner.run_case.side_effect = list(results)
    return runner


def _case(advisory: bool = False) -> MagicMock:
    return MagicMock(advisory=advisory)


def test_a_passing_case_is_repeated_and_the_first_failure_wins():
    runner = _runner(_result(True), _result(False), _result(True))
    result = Command._run_case_repeated(runner, _case(), repeats=3)
    assert result.passed is False and result.runs == 2
    assert runner.run_case.call_count == 2


def test_a_case_that_already_failed_is_not_run_again():
    runner = _runner(_result(False), _result(True))
    result = Command._run_case_repeated(runner, _case(), repeats=3)
    assert result.passed is False and result.runs == 1
    assert runner.run_case.call_count == 1


def test_an_advisory_case_runs_once():
    runner = _runner(_result(True, advisory=True), _result(True, advisory=True))
    result = Command._run_case_repeated(runner, _case(advisory=True), repeats=2)
    assert result.runs == 1 and runner.run_case.call_count == 1


def test_a_run_the_model_could_not_answer_never_replaces_a_scored_run():
    first = _result(True)
    runner = _runner(first, _result(False, error="ModelUnavailableError: timeout"), _result(True))
    result = Command._run_case_repeated(runner, _case(), repeats=3)
    assert result is first and result.passed is True and result.runs == 3


def test_all_runs_pass_keeps_the_first_and_counts_them():
    first = _result(True)
    runner = _runner(first, _result(True), _result(True))
    result = Command._run_case_repeated(runner, _case(), repeats=3)
    assert result is first and result.runs == 3


def test_an_errored_first_run_is_retried_and_a_scored_run_replaces_it():
    """An error is neither a failure nor a score: the budget is spent getting a score."""
    runner = _runner(
        _result(False, error="ModelUnavailableError: timeout"), _result(True), _result(True)
    )
    result = Command._run_case_repeated(runner, _case(), repeats=3)
    assert result.error == "" and result.passed is True and result.runs == 3


def test_a_case_that_errors_every_time_stays_an_error():
    runner = _runner(*[_result(False, error="ModelUnavailableError: down")] * 3)
    result = Command._run_case_repeated(runner, _case(), repeats=3)
    assert result.error and result.runs == 3 and runner.run_case.call_count == 3
