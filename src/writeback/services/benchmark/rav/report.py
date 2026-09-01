# writeback/services/benchmark/rav/report.py
"""Render RAV runs as a table, a JSON artifact, and a diff against a baseline.

The summary table has one column per settings variant so an ablation sweep
reads side by side: default vs no-type-gate vs no-keyword-filter vs a lower
confidence cut. Rows are the numbers the panel will ask for — precision,
recall (overall and by severity), and how many aligned requirements were left
alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .runner import ScanSettings, ScoreSheet, score_findings


@dataclass
class RavReport:
    """One settings variant, scored."""

    settings: ScanSettings
    sheet: ScoreSheet
    run_stats: dict
    started_at: str
    model_label: str = ""

    @property
    def label(self) -> str:
        return self.settings.label()

    def relaxed_sheet(self, corpus) -> ScoreSheet:
        """Re-score this run's findings with the document constraint dropped."""
        return score_findings(corpus, self.sheet.findings, match_document=False)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "model": self.model_label or self.run_stats.get("llm_model", ""),
            "started_at": self.started_at,
            "settings": self.settings.as_dict(),
            "run_stats": self.run_stats,
            "scores": self.sheet.as_dict(),
        }


def write_json(reports: list[RavReport], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({"runs": [r.as_dict() for r in reports]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return destination


def load_baseline(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── Rendering ─────────────────────────────────────────────────────


def render_rav_report(reports: list[RavReport], *, verbose: bool = False) -> str:
    lines: list[str] = []
    for report in reports:
        lines.extend(_render_details(report, verbose=verbose))
    lines.append("")
    lines.extend(_render_summary(reports))
    return "\n".join(lines)


def _render_details(report: RavReport, *, verbose: bool) -> list[str]:
    lines = ["", f"-- {report.label} " + "-" * max(0, 60 - len(report.label))]
    sheet = report.sheet

    for score in sheet.case_scores:
        if score.passed and not verbose:
            continue
        state = "ok  " if score.passed else "FAIL"
        lines.append(
            f"  {state} {score.case.id:<6} {score.case.severity:<9} "
            f"{score.case.ifc_type:<10} {score.case.property:<20} "
            f"hits={len(score.hits)} miss={len(score.misses)} alarms={len(score.false_alarms)}"
        )

    if sheet.unmatched:
        lines.append(f"  {len(sheet.unmatched)} finding(s) matched no key case:")
        for finding in sheet.unmatched[:10]:
            lines.append(
                f"       {finding.ifc_type:<10} {finding.global_id} "
                f"{finding.property:<20} [{finding.document}] "
                f"{finding.ifc_value!r} vs {finding.document_value!r}"
            )
    return lines


def _render_summary(reports: list[RavReport]) -> list[str]:
    column = max(14, max((len(r.label) for r in reports), default=10) + 2)

    def row(name: str, values: list[str]) -> str:
        return f"  {name:<24}" + "".join(f"{v:>{column}}" for v in values)

    lines = ["  " + " " * 24 + "".join(f"{r.label:>{column}}" for r in reports)]
    lines.append("  " + "-" * (24 + column * len(reports)))
    lines.append(row("precision", [f"{r.sheet.precision:.2f}" for r in reports]))
    lines.append(row("recall", [f"{r.sheet.recall:.2f}" for r in reports]))
    lines.append(row("f1", [f"{r.sheet.f1:.2f}" for r in reports]))

    severities = sorted({s for r in reports for s in r.sheet.recall_by_severity()})
    for severity in severities:
        lines.append(
            row(
                f"  recall {severity}",
                [_fraction(r.sheet.recall_by_severity().get(severity)) for r in reports],
            )
        )
    lines.append(row("negatives held", [_fraction(r.sheet.negatives_held()) for r in reports]))
    lines.append(
        row(
            "tp / fp / fn",
            [
                f"{r.sheet.true_positives}/{r.sheet.false_positives}/{r.sheet.false_negatives}"
                for r in reports
            ],
        )
    )
    lines.append(row("findings", [str(len(r.sheet.findings)) for r in reports]))
    lines.append(
        row("entities scanned", [str(r.run_stats.get("entities_scanned", "?")) for r in reports])
    )
    lines.append(
        row("duration", [f"{r.run_stats.get('duration_seconds', 0):.0f}s" for r in reports])
    )
    return lines


def _fraction(pair: tuple[int, int] | None) -> str:
    if not pair:
        return "n/a"
    return f"{pair[0]}/{pair[1]}"


# ── Baseline diff ─────────────────────────────────────────────────


def diff_rav_runs(baseline: dict, current: RavReport) -> str:
    """Per-case FIXED / REGRESSED against the baseline run with the same label."""
    previous = next(
        (run for run in baseline.get("runs", []) if run.get("label") == current.label),
        None,
    )
    header = f"-- baseline diff [{current.label}] " + "-" * 20
    if previous is None:
        return "\n".join(["", header, "  no baseline run with this label"])

    before = {c["id"]: c for c in previous.get("scores", {}).get("cases", [])}
    lines = ["", header]
    changed = False
    for score in current.sheet.case_scores:
        old = before.get(score.case.id)
        if old is None:
            continue
        if old["passed"] and not score.passed:
            changed = True
            lines.append(
                f"  REGRESSED {score.case.id:<6} miss={len(score.misses)} "
                f"alarms={len(score.false_alarms)}"
            )
        elif not old["passed"] and score.passed:
            changed = True
            lines.append(f"  FIXED     {score.case.id:<6}")

    old_scores = previous.get("scores", {})
    lines.append(
        f"  precision {old_scores.get('precision', 0):.2f} -> {current.sheet.precision:.2f}, "
        f"recall {old_scores.get('recall', 0):.2f} -> {current.sheet.recall:.2f}"
    )
    if not changed:
        lines.append("  no per-case change")
    return "\n".join(lines)
