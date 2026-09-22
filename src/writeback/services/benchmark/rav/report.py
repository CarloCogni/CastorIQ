# writeback/services/benchmark/rav/report.py
"""Render RAV runs as a table, a JSON artifact, and a diff against a baseline.

The summary table has one column per settings variant so an ablation sweep
reads side by side: default vs no-type-gate vs no-keyword-filter vs a lower
confidence cut. Rows are the numbers the panel will ask for — precision,
recall (overall and by severity), and how many aligned requirements were left
alone.

With ``--repeat`` every variant runs several times and the aggregate table
reports mean and min–max per label. The spread is the run-to-run variance
floor: a change is only an improvement when its delta against the baseline
exceeds the baseline's own spread, and ``diff_rav_aggregates`` says which.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from .runner import ScanSettings, ScoreSheet, score_findings

AGGREGATE_METRICS = ("precision", "recall", "f1")


@dataclass
class RavReport:
    """One settings variant, scored."""

    settings: ScanSettings
    sheet: ScoreSheet
    run_stats: dict
    started_at: str
    model_label: str = ""
    repeat_index: int = 0

    @property
    def label(self) -> str:
        return self.settings.label()

    @property
    def column(self) -> str:
        """Summary-table header: the label, suffixed with the repeat when there are several."""
        return f"{self.label}#{self.repeat_index + 1}" if self.repeat_index else self.label

    def relaxed_sheet(self, corpus) -> ScoreSheet:
        """Re-score this run's findings with the document constraint dropped."""
        return score_findings(corpus, self.sheet.findings, match_document=False)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "repeat_index": self.repeat_index,
            "model": self.model_label or self.run_stats.get("llm_model", ""),
            "started_at": self.started_at,
            "settings": self.settings.as_dict(),
            "run_stats": self.run_stats,
            "scores": self.sheet.as_dict(),
        }


def write_json(reports: list[RavReport], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "runs": [r.as_dict() for r in reports],
        "aggregate": aggregate_runs([r.as_dict() for r in reports]),
    }
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return destination


def load_baseline(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── Aggregation over repeats ──────────────────────────────────────


def aggregate_runs(runs: list[dict]) -> dict[str, dict]:
    """Per label: n, and mean / min / max of precision, recall and F1.

    Works on the serialised run dicts so a saved artifact (with or without an
    ``aggregate`` block) can be aggregated the same way as a live run.
    """
    by_label: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        by_label[run["label"]].append(run.get("scores", {}))

    aggregate: dict[str, dict] = {}
    for label, sheets in by_label.items():
        entry: dict = {"n": len(sheets)}
        for metric in AGGREGATE_METRICS:
            values = [float(sheet.get(metric, 0.0)) for sheet in sheets]
            entry[metric] = {
                "mean": round(mean(values), 4),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
            }
        entry["true_positives"] = _mean_of(sheets, "true_positives")
        entry["false_positives"] = _mean_of(sheets, "false_positives")
        entry["false_negatives"] = _mean_of(sheets, "false_negatives")
        entry["negatives_held"] = _mean_of(sheets, "negatives_held")
        entry["recall_by_severity"] = _mean_severity(sheets)
        aggregate[label] = entry
    return aggregate


def _mean_of(sheets: list[dict], key: str) -> float:
    return round(mean(float(sheet.get(key, 0)) for sheet in sheets), 2)


def _mean_severity(sheets: list[dict]) -> dict[str, dict]:
    table: dict[str, list[float]] = defaultdict(list)
    of: dict[str, int] = {}
    for sheet in sheets:
        for severity, pair in sheet.get("recall_by_severity", {}).items():
            table[severity].append(float(pair.get("hits", 0)))
            of[severity] = int(pair.get("of", 0))
    return {
        severity: {"hits_mean": round(mean(hits), 2), "of": of[severity]}
        for severity, hits in table.items()
    }


# ── Rendering ─────────────────────────────────────────────────────


def render_rav_report(reports: list[RavReport], *, verbose: bool = False) -> str:
    lines: list[str] = []
    for report in reports:
        lines.extend(_render_details(report, verbose=verbose))
    lines.append("")
    lines.extend(_render_summary(reports))
    aggregate = aggregate_runs([r.as_dict() for r in reports])
    if any(entry["n"] > 1 for entry in aggregate.values()):
        lines.append("")
        lines.extend(_render_aggregate(aggregate))
    return "\n".join(lines)


def _render_aggregate(aggregate: dict[str, dict]) -> list[str]:
    """Mean [min–max] per label; only printed when a label ran more than once."""
    labels = list(aggregate)
    column = max(20, max(len(label) for label in labels) + 2)

    def row(name: str, values: list[str]) -> str:
        return f"  {name:<24}" + "".join(f"{v:>{column}}" for v in values)

    lines = [
        "  aggregate over repeats (mean [min-max])",
        "  " + " " * 24 + "".join(f"{label:>{column}}" for label in labels),
        "  " + "-" * (24 + column * len(labels)),
    ]
    for metric in AGGREGATE_METRICS:
        lines.append(row(metric, [_spread(aggregate[label][metric]) for label in labels]))
    lines.append(row("n", [str(aggregate[label]["n"]) for label in labels]))
    return lines


def _spread(stat: dict) -> str:
    return f"{stat['mean']:.2f} [{stat['min']:.2f}-{stat['max']:.2f}]"


def _render_details(report: RavReport, *, verbose: bool) -> list[str]:
    lines = ["", f"-- {report.column} " + "-" * max(0, 60 - len(report.column))]
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
    column = max(14, max((len(r.column) for r in reports), default=10) + 2)

    def row(name: str, values: list[str]) -> str:
        return f"  {name:<24}" + "".join(f"{v:>{column}}" for v in values)

    lines = ["  " + " " * 24 + "".join(f"{r.column:>{column}}" for r in reports)]
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


def diff_rav_aggregates(baseline: dict, reports: list[RavReport]) -> str:
    """Mean deltas per label against the baseline, judged against the baseline's spread.

    A delta counts as a change only when it exceeds the baseline's own
    min–max range on that metric. With a single-run baseline the spread is
    zero and every delta "exceeds" it: the line says so, because a claim made
    against an unmeasured variance floor is not one the record should make.
    """
    before = baseline.get("aggregate") or aggregate_runs(baseline.get("runs", []))
    after = aggregate_runs([r.as_dict() for r in reports])
    lines = ["", "-- aggregate diff vs baseline " + "-" * 20]
    for label, current in after.items():
        previous = before.get(label)
        if previous is None:
            lines.append(f"  [{label}] no baseline run with this label")
            continue
        lines.append(f"  [{label}] baseline n={previous['n']}, current n={current['n']}")
        for metric in AGGREGATE_METRICS:
            old, new = previous[metric], current[metric]
            delta = new["mean"] - old["mean"]
            spread = old["max"] - old["min"]
            verdict = "exceeds baseline spread" if abs(delta) > spread else "within baseline spread"
            if previous["n"] < 2:
                verdict += " (baseline spread unmeasured: n=1)"
            lines.append(
                f"    {metric:<10} {old['mean']:.2f} -> {new['mean']:.2f} "
                f"({delta:+.2f}; spread {spread:.2f}) {verdict}"
            )
    return "\n".join(lines)
