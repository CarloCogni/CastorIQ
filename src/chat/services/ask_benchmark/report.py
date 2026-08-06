# chat/services/ask_benchmark/report.py
"""Render and persist Ask benchmark runs.

The JSON artifact keeps the raw answers so later runs (after description or
retrieval changes) can be diffed qualitatively, not just by pass rate.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from chat.services.ask_benchmark.scoring import CaseResult


@dataclass
class AskBenchmarkReport:
    """All results for one fixture under one model."""

    fixture: str
    model_label: str
    started_at: str
    results: list[CaseResult] = field(default_factory=list)

    def _scored(self, tier: int) -> list[CaseResult]:
        return [r for r in self.results if r.tier == tier and not r.skipped]

    def tier_score(self, tier: int) -> tuple[int, int]:
        """(passed, scored) for a tier, skips excluded."""
        scored = self._scored(tier)
        return sum(r.passed for r in scored), len(scored)

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fixture": self.fixture,
            "model_label": self.model_label,
            "started_at": self.started_at,
            "results": [asdict(r) for r in self.results],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def render_report(reports: list[AskBenchmarkReport]) -> str:
    """Plain-text summary table across fixtures."""
    lines: list[str] = [""]
    header = f"{'fixture':<14} {'tier1':>12} {'tier2':>12}  case detail"
    lines.append(header)
    lines.append("-" * len(header))

    for report in reports:
        t1_pass, t1_total = report.tier_score(1)
        t2_pass, t2_total = report.tier_score(2)
        marks = " ".join(f"{r.case_id}:{r.mark}" for r in report.results)
        lines.append(
            f"{report.fixture:<14} {t1_pass:>5}/{t1_total:<6} {t2_pass:>5}/{t2_total:<6}  {marks}"
        )

    total_t1 = [n for r in reports for n in r.tier_score(1)]
    total_t2 = [n for r in reports for n in r.tier_score(2)]
    lines.append("-" * len(header))
    lines.append(
        f"{'TOTAL':<14} {sum(total_t1[::2]):>5}/{sum(total_t1[1::2]):<6} "
        f"{sum(total_t2[::2]):>5}/{sum(total_t2[1::2]):<6}"
    )

    failures = [
        (report.fixture, result)
        for report in reports
        for result in report.results
        if not result.passed and not result.skipped
    ]
    if failures:
        lines.append("\nFailures:")
        for fixture, result in failures:
            note = "; ".join(result.notes)
            lines.append(f"  {fixture}/{result.case_id}  expected: {result.expected}  {note}")
            lines.append(f"    answer: {result.answer[:160].replace(chr(10), ' ')}")
    return "\n".join(lines)
