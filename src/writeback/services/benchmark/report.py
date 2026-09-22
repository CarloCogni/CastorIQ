# writeback/services/benchmark/report.py
"""Aggregate case results into a run artifact, a table, and a baseline diff (spec B-2).

Three outputs, one shape:

* a human table for reading a run at a glance;
* a JSON artifact so runs can be compared later or across machines;
* a diff of two artifacts — REGRESSED and FIXED — the regression-guard mode.

Latency is reported as median and p90 rather than a mean: LLM calls have a long
tail, and one slow outlier drags a mean somewhere that describes no real request.
"""

from __future__ import annotations

import json
import math
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
    repeats: int = 1
    notes: dict = field(default_factory=dict)

    # ── Scores ─────────────────────────────────────────────

    @property
    def scored(self) -> list[CaseResult]:
        """Cases that count: advisory ones are reported but never scored, and a
        case the model could not answer (a harness error) is outside every
        denominator and listed on its own (spec B-2)."""
        return [r for r in self.results if not r.advisory and not r.error]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.scored if r.passed)

    @property
    def targets_scored(self) -> list[CaseResult]:
        return [r for r in self.scored if r.kind == "change" and r.targets_match is not None]

    @property
    def targets_passed(self) -> int:
        return sum(1 for r in self.targets_scored if r.targets_match)

    @property
    def diff_scored(self) -> list[CaseResult]:
        return [r for r in self.scored if r.kind == "change" and r.diff_match is not None]

    @property
    def diff_passed(self) -> int:
        return sum(1 for r in self.diff_scored if r.diff_match)

    @property
    def integrity_scored(self) -> list[CaseResult]:
        return [r for r in self.scored if r.integrity_ok is not None]

    @property
    def integrity_passed(self) -> int:
        return sum(1 for r in self.integrity_scored if r.integrity_ok)

    @property
    def reject_scored(self) -> list[CaseResult]:
        return [r for r in self.scored if r.kind in ("reject", "no_change")]

    @property
    def reject_passed(self) -> int:
        return sum(1 for r in self.reject_scored if r.passed)

    @property
    def change_scored(self) -> list[CaseResult]:
        return [r for r in self.scored if r.kind == "change"]

    @property
    def change_passed(self) -> int:
        return sum(1 for r in self.change_scored if r.passed)

    @property
    def repairs(self) -> int:
        return sum(r.repairs for r in self.results)

    @property
    def failures(self) -> list[CaseResult]:
        return [r for r in self.scored if not r.passed]

    @property
    def errored(self) -> list[CaseResult]:
        return [r for r in self.results if r.error]

    def latency(self) -> tuple[float, float]:
        """(median, p90) seconds per scored case; a timed-out call is not a request that happened."""
        durations = sorted(r.duration_seconds for r in self.scored if r.duration_seconds)
        if not durations:
            return 0.0, 0.0
        median = statistics.median(durations)
        # Nearest-rank p90: the smallest value at or above 90% of the sample.
        index = max(0, min(len(durations) - 1, math.ceil(0.9 * len(durations)) - 1))
        return median, durations[index]

    def as_dict(self) -> dict:
        median, p90 = self.latency()
        return {
            "model": self.model_label,
            "started_at": self.started_at,
            "repeats": self.repeats,
            "notes": self.notes,
            "totals": {
                "cases": len(self.results),
                "scored": len(self.scored),
                "advisory": sum(1 for r in self.results if r.advisory),
                "passed": self.passed,
                "change_scored": len(self.change_scored),
                "change_passed": self.change_passed,
                "targets_scored": len(self.targets_scored),
                "targets_passed": self.targets_passed,
                "diff_scored": len(self.diff_scored),
                "diff_passed": self.diff_passed,
                "integrity_scored": len(self.integrity_scored),
                "integrity_passed": self.integrity_passed,
                "reject_scored": len(self.reject_scored),
                "reject_passed": self.reject_passed,
                "repairs": self.repairs,
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


def render_report(reports: list[BenchmarkReport]) -> str:
    """Render one or more model passes as a text report."""
    lines: list[str] = []
    for report in reports:
        lines.extend(_render_failures(report))
    lines.append("")
    lines.extend(_render_summary(reports))
    return "\n".join(lines)


def _render_failures(report: BenchmarkReport) -> list[str]:
    lines = ["", f"-- {report.model_label} " + "-" * max(0, 60 - len(report.model_label))]
    failures = report.failures
    if not failures:
        lines.append("  no failures")
    for result in failures:
        lines.append(f"  FAIL {result.case_id:>6}  {result.prompt[:64]}")
        lines.append(f"              expected {result.expectation}")
        lines.append(f"              outcome: {_describe(result)}")
    for result in report.errored:
        lines.append(f"  ERROR{result.case_id:>6}  {result.error}")
    return lines


def _describe(result: CaseResult) -> str:
    if result.error:
        return result.error
    if result.outcome in ("rejected", "no_change"):
        detail = result.targets_detail or result.rejection_reason[:120]
        return f"{result.outcome}: {detail}"
    parts = [result.targets_detail, result.diff_detail]
    if result.integrity_ok is False:
        parts.append(f"integrity: {result.integrity_detail}")
    return "; ".join(p for p in parts if p) or result.outcome


def _render_summary(reports: list[BenchmarkReport]) -> list[str]:
    label_width = max((len(r.model_label) for r in reports), default=10)
    column = max(14, label_width + 2)

    def row(name: str, values: list[str]) -> str:
        return f"  {name:<22}" + "".join(f"{v:>{column}}" for v in values)

    def ratio(passed: int, scored: list) -> str:
        return f"{passed}/{len(scored)}" if scored else "n/a"

    lines = ["  " + " " * 22 + "".join(f"{r.model_label:>{column}}" for r in reports)]
    lines.append("  " + "-" * (22 + column * len(reports)))
    lines.append(row("passed", [ratio(r.passed, r.scored) for r in reports]))
    lines.append(row("targets match", [ratio(r.targets_passed, r.targets_scored) for r in reports]))
    lines.append(row("diff match", [ratio(r.diff_passed, r.diff_scored) for r in reports]))
    lines.append(row("integrity", [ratio(r.integrity_passed, r.integrity_scored) for r in reports]))
    lines.append(
        row("reject / no-change", [ratio(r.reject_passed, r.reject_scored) for r in reports])
    )
    lines.append(row("repairs used", [str(r.repairs) for r in reports]))
    lines.append(row("errors", [str(len(r.errored)) for r in reports]))
    lines.append(row("latency median", [f"{r.latency()[0]:.1f}s" for r in reports]))
    lines.append(row("latency p90", [f"{r.latency()[1]:.1f}s" for r in reports]))
    lines.append(row("tokens in/out", [f"{r.tokens_in}/{r.tokens_out}" for r in reports]))
    lines.append(row("est. cost", [f"${r.estimated_cost_usd:.4f}" for r in reports]))
    return lines


# ── Baseline diff ─────────────────────────────────────────────────


def diff_runs(baseline: dict, current: BenchmarkReport) -> str:
    """Compare a previous run artifact against this one; report only what changed."""
    previous = {c["case_id"]: c for c in baseline.get("cases", []) if not c.get("advisory")}
    now = {r.case_id: r for r in current.scored}

    regressed, fixed = [], []
    for case_id, result in now.items():
        before = previous.get(case_id)
        if before is None:
            continue
        was_ok = bool(before.get("passed"))
        if was_ok and not result.passed:
            regressed.append((case_id, result))
        elif not was_ok and result.passed:
            fixed.append((case_id, result))

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
    for case_id, result in regressed:
        lines.append(f"  REGRESSED {case_id:>6}  {result.prompt[:56]}")
        lines.append(f"                    now: {_describe(result)}")
    for case_id, result in fixed:
        lines.append(f"  FIXED     {case_id:>6}  {result.prompt[:56]}")
    if added:
        lines.append(f"  new cases not in baseline: {', '.join(added)}")
    if removed:
        lines.append(f"  baseline cases not run: {', '.join(removed)}")
    return "\n".join(lines)


def load_baseline(path: str | Path) -> dict:
    """Read a previous run artifact."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
