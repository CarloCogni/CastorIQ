# writeback/services/benchmark/report.py
"""Aggregate case results into a run artifact, a table, and a baseline diff.

Three outputs, one shape:

* a human table for reading a run at a glance;
* a JSON artifact so runs can be compared later or across machines;
* a diff of two artifacts, which is the regression-guard mode — REGRESSED and
  FIXED, rather than two wall-of-text outputs to eyeball.

Latency is reported as median and p90 rather than a mean: LLM calls have a long
tail, and one slow outlier drags a mean somewhere that describes no real request.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .runner import CaseResult


@dataclass
class BenchmarkReport:
    """One model's pass over the corpus."""

    model_label: str
    started_at: str
    results: list[CaseResult] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost_usd: float = 0.0
    executed: bool = True
    repeats: int = 1

    # ── Scores ─────────────────────────────────────────────

    @property
    def scored(self) -> list[CaseResult]:
        """Cases that count — advisory ones are reported but never scored."""
        return [r for r in self.results if not r.advisory]

    @property
    def understanding_passed(self) -> int:
        return sum(1 for r in self.scored if r.understood)

    @property
    def fidelity_scored(self) -> list[CaseResult]:
        return [r for r in self.scored if r.fidelity_ok is not None]

    @property
    def fidelity_passed(self) -> int:
        return sum(1 for r in self.fidelity_scored if r.fidelity_ok)

    @property
    def failures(self) -> list[CaseResult]:
        return [r for r in self.scored if not r.passed]

    @property
    def errored(self) -> list[CaseResult]:
        return [r for r in self.results if r.error]

    def latency(self) -> tuple[float, float]:
        """(median, p90) seconds per case."""
        durations = sorted(r.duration_seconds for r in self.results if r.duration_seconds)
        if not durations:
            return 0.0, 0.0
        median = statistics.median(durations)
        index = max(0, min(len(durations) - 1, int(round(0.9 * (len(durations) - 1)))))
        return median, durations[index]

    def as_dict(self) -> dict:
        median, p90 = self.latency()
        return {
            "model": self.model_label,
            "started_at": self.started_at,
            "executed": self.executed,
            "repeats": self.repeats,
            "totals": {
                "cases": len(self.results),
                "scored": len(self.scored),
                "advisory": len(self.results) - len(self.scored),
                "understanding_passed": self.understanding_passed,
                "fidelity_scored": len(self.fidelity_scored),
                "fidelity_passed": self.fidelity_passed,
                "errored": len(self.errored),
                "latency_median_s": round(median, 3),
                "latency_p90_s": round(p90, 3),
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            },
            "cases": [r.as_dict() for r in self.results],
        }

    def write_json(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return destination


# ── Rendering ─────────────────────────────────────────────────────


def render_report(reports: list[BenchmarkReport], *, verbose: bool = False) -> str:
    """Render one or more model passes as a text report."""
    lines: list[str] = []

    for report in reports:
        lines.extend(_render_failures(report, verbose=verbose))

    lines.append("")
    lines.extend(_render_summary(reports))
    return "\n".join(lines)


def _render_failures(report: BenchmarkReport, *, verbose: bool) -> list[str]:
    lines = [""]
    lines.append(f"-- {report.model_label} " + "-" * max(0, 60 - len(report.model_label)))

    failures = report.failures
    if not failures:
        lines.append("  no failures")
    for result in failures:
        lines.append(f"  FAIL {result.case_id:>6}  {result.prompt[:64]}")
        lines.append(f"              {result.understanding_detail or result.fidelity_detail}")
        if result.fidelity_ok is False and result.understood:
            lines.append(f"              fidelity: {result.fidelity_detail}")

    for result in report.errored:
        lines.append(f"  ERROR{result.case_id:>6}  {result.error}")

    if verbose:
        mismatches = [r for r in report.results if r.slots_match is False]
        for result in mismatches:
            lines.append(f"  slots{result.case_id:>6}  {result.slots_detail}")

    return lines


def _render_summary(reports: list[BenchmarkReport]) -> list[str]:
    label_width = max((len(r.model_label) for r in reports), default=10)
    column = max(12, label_width + 2)

    def row(name: str, values: list[str]) -> str:
        return f"  {name:<22}" + "".join(f"{v:>{column}}" for v in values)

    lines = ["  " + " " * 22 + "".join(f"{r.model_label:>{column}}" for r in reports)]
    lines.append("  " + "-" * (22 + column * len(reports)))

    lines.append(
        row(
            "understanding",
            [f"{r.understanding_passed}/{len(r.scored)}" for r in reports],
        )
    )
    lines.append(
        row(
            "fidelity",
            [
                f"{r.fidelity_passed}/{len(r.fidelity_scored)}" if r.fidelity_scored else "n/a"
                for r in reports
            ],
        )
    )
    lines.append(row("errors", [str(len(r.errored)) for r in reports]))
    lines.append(row("latency median", [f"{r.latency()[0]:.1f}s" for r in reports]))
    lines.append(row("latency p90", [f"{r.latency()[1]:.1f}s" for r in reports]))
    lines.append(row("tokens in/out", [f"{r.tokens_in}/{r.tokens_out}" for r in reports]))
    lines.append(row("est. cost", [f"${r.estimated_cost_usd:.4f}" for r in reports]))
    return lines


# ── Baseline diff ─────────────────────────────────────────────────


def diff_runs(baseline: dict, current: BenchmarkReport) -> str:
    """Compare a previous run artifact against this one.

    Reports only what changed. A case that passed before and passes now is not
    news; the point is to make a regression impossible to miss in a 92-line
    output.
    """
    previous = {
        case["case_id"]: case for case in baseline.get("cases", []) if not case.get("advisory")
    }
    now = {r.case_id: r for r in current.scored}

    regressed, fixed = [], []
    for case_id, result in now.items():
        before = previous.get(case_id)
        if before is None:
            continue
        was_ok = before.get("understood") and before.get("fidelity_ok") is not False
        if was_ok and not result.passed:
            regressed.append((case_id, before, result))
        elif not was_ok and result.passed:
            fixed.append((case_id, before, result))

    added = sorted(set(now) - set(previous))
    removed = sorted(set(previous) - set(now))

    lines = [
        "",
        f"-- baseline diff vs {baseline.get('model', '?')} "
        f"({baseline.get('started_at', '?')}) " + "-" * 12,
    ]
    if not (regressed or fixed or added or removed):
        lines.append("  no change")
        return "\n".join(lines)

    for case_id, before, result in regressed:
        lines.append(f"  REGRESSED {case_id:>6}  {result.prompt[:56]}")
        lines.append(
            f"                    was: {_describe(before)}   now: {result.understanding_detail}"
        )
    for case_id, before, result in fixed:
        lines.append(f"  FIXED     {case_id:>6}  {result.prompt[:56]}")
        lines.append(
            f"                    was: {_describe(before)}   now: {result.understanding_detail}"
        )
    if added:
        lines.append(f"  new cases not in baseline: {', '.join(added)}")
    if removed:
        lines.append(f"  baseline cases not run: {', '.join(removed)}")
    return "\n".join(lines)


def _describe(case: dict) -> str:
    detail = case.get("understanding_detail") or ""
    return detail or f"tier {case.get('actual_tier')}, {case.get('actual_operation')}"


def load_baseline(path: str | Path) -> dict:
    """Read a previous run artifact."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
