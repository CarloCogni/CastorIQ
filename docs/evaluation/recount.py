# docs/evaluation/recount.py
"""Recount the memory's evaluation tables from the committed run artifacts.

No database, no model, no GPU: every figure in Tables 5.2 to 5.4 of the final
memory is recomputed from the JSON files under ``runs/`` and printed next to
the row it belongs to. ``docs/fmp-delivery/tools/rewrite_memory.py`` imports
``figures()`` and refuses to build the memory if a recounted value is missing
from its table row.

    uv run python docs/evaluation/recount.py

Two rules are applied here rather than read from the artifacts, and both are
stated in the bake-off record:

* Modify targets and diff are scored over every case whose expectation names
  targets (64) or a diff (66); a refused case counts as a miss. The Claude artifact predates that rule and stores 46 and 47 as
  denominators; the recount derives the corrected ones from its cases.
* The Claude row's integrity column is not reported: the independent re-read
  of the written copy was added to the harness after that row ran.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path

RUNS = Path(__file__).resolve().parents[2] / "runs"
MODIFY_LOCAL = "writeback-v3-2026-09-15-qwen2.5-coder-7b-review5-repeat2.json"
MODIFY_CLOUD = "writeback-v3-2026-09-15-claude-sonnet-4-6.json"
RAV = {
    ("llama3.1:8b", "before"): "rav-2026-09-15-before-llama3.1-8b-repeat3.json",
    ("llama3.1:8b", "after"): "rav-2026-09-15-after-llama3.1-8b-repeat3.json",
    ("qwen2.5-coder:7b", "before"): "rav-2026-09-15-before-qwen2.5-coder-7b-repeat3.json",
    ("qwen2.5-coder:7b", "after"): "rav-2026-09-15-after-qwen2.5-coder-7b-repeat3.json",
}
COVERAGE = "rav-2026-09-16-coverage.json"
ASK = ("open-house", "duplex", "fzk-haus", "office-a")


@dataclass(frozen=True)
class Figure:
    """One recounted value and the table row it must appear in."""

    table: str
    row: str
    value: str
    source: str


def _load(name: str) -> dict:
    return json.loads((RUNS / name).read_text(encoding="utf-8"))


def _fraction(hits: int, total: int) -> str:
    return f"{hits} / {total}"


# --- Table 5.2 -----------------------------------------------------------------


def modify_figures(name: str, *, integrity: bool) -> list[tuple[str, str]]:
    """(row, value) pairs for one column of Table 5.2."""
    data = _load(name)
    cases = [c for c in data["cases"] if not c["advisory"]]
    selection = [c for c in cases if "targets:" in c["expectation"]]
    change = [c for c in cases if "diff:" in c["expectation"]]
    rejects = [c for c in cases if c["kind"] in ("reject", "no_change")]
    totals = data["totals"]
    rows = [
        ("passed (scored)", _fraction(sum(c["passed"] is True for c in cases), len(cases))),
        (
            "targets match",
            _fraction(sum(c["targets_match"] is True for c in selection), len(selection)),
        ),
        ("diff match", _fraction(sum(c["diff_match"] is True for c in change), len(change))),
        (
            "reject / no-change",
            _fraction(sum(c["passed"] is True for c in rejects), len(rejects)),
        ),
        ("repairs used", str(totals["repairs"])),
        (
            "latency median",
            f"{totals['latency_median_s']:.1f} s / {totals['latency_p90_s']:.1f} s",
        ),
        ("tokens in / out", f"{totals['tokens_in']:,} / {totals['tokens_out']:,}"),
    ]
    if integrity:
        checked = [c for c in cases if c["integrity_ok"] is not None]
        rows.append(
            ("integrity", _fraction(sum(c["integrity_ok"] is True for c in checked), len(checked)))
        )
    if totals.get("estimated_cost_usd"):
        rows.append(("tokens in / out", f"${totals['estimated_cost_usd']:.2f}"))
    return rows


# --- Table 5.3 -----------------------------------------------------------------


def _runs(name: str) -> list[dict]:
    return [r["scores"] for r in _load(name)["runs"] if r["label"] == "default"]


def _spread(values: list[float]) -> str:
    """Mean [min–max] as the harness aggregates it (four decimals, then shown to two)."""
    mean = round(statistics.mean(values), 4)
    return f"{mean:.2f} [{round(min(values), 4):.2f}–{round(max(values), 4):.2f}]"


def rav_figures() -> list[tuple[str, str]]:
    """(row, value) pairs for Table 5.3."""
    rows: list[tuple[str, str]] = []
    worst: dict[tuple[str, str], tuple[int, int]] = {}
    for model in ("llama3.1:8b", "qwen2.5-coder:7b"):
        before, after = _runs(RAV[(model, "before")]), _runs(RAV[(model, "after")])
        for metric in ("precision", "recall", "f1"):
            label = "F1" if metric == "f1" else metric
            b = [s[metric] for s in before]
            a = [s[metric] for s in after]
            row = f"{model} {label}"
            rows.append((row, _spread(b)))
            rows.append((row, _spread(a)))
            # As the harness does (rav/report.py, diff_rav_aggregates): means and
            # ranges rounded to four decimals, then subtracted, then shown to two.
            delta = round(statistics.mean(a), 4) - round(statistics.mean(b), 4)
            rows.append((row, f"{delta:+.2f}"))
            rows.append((row, f"{round(max(b), 4) - round(min(b), 4):.2f}"))
        for phase, runs in (("before", before), ("after", after)):
            clear = min(s["recall_by_severity"]["clear"]["hits"] for s in runs)
            of = runs[0]["recall_by_severity"]["clear"]["of"]
            held = min(s["negatives_held"] for s in runs)
            worst[(model, phase)] = (clear, held)
            short = "llama" if model.startswith("llama") else "coder"
            if phase == "after":
                rows.append(("recall on clear conflicts", f"{_fraction(clear, of)} ({short})"))
            rows.append(
                (
                    "aligned requirements left alone",
                    f"{_fraction(held, runs[0]['negatives_total'])} ({short})",
                )
            )
    if worst[("llama3.1:8b", "before")][0] == worst[("qwen2.5-coder:7b", "before")][0]:
        rows.append(
            (
                "recall on clear conflicts",
                f"{_fraction(worst[('llama3.1:8b', 'before')][0], 11)} on both models",
            )
        )
    coverage = {s["setting"]: s for s in _load(COVERAGE)["settings"]}
    for setting in ("embedding-only", "entity-first"):
        s = coverage[setting]
        rows.append(
            ("reached by any requirement chunk", _fraction(s["reached"], s["key_entities"]))
        )
        rows.append(
            (
                "reached by every document that constrains",
                _fraction(s["reached_by_every_constraining_document"], s["key_entities"]),
            )
        )
    return rows


# --- Table 5.4 -----------------------------------------------------------------


def ask_figures() -> list[tuple[str, str]]:
    """(row, value) pairs for Table 5.4."""
    rows: list[tuple[str, str]] = []
    totals = {1: [0, 0], 2: [0, 0]}
    for fixture in ASK:
        results = _load(f"ask-2026-09-15.{fixture}.json")["results"]
        scored = [r for r in results if not r["skipped"]]
        for tier in (1, 2):
            tier_scored = [r for r in scored if r["tier"] == tier]
            passed = sum(r["passed"] for r in tier_scored)
            totals[tier][0] += passed
            totals[tier][1] += len(tier_scored)
            rows.append((fixture, f"{passed}/{len(tier_scored)}"))
        rows.append((fixture, f"| {len(results) - len(scored)} |"))
        latency = statistics.median(r["latency_s"] for r in scored)
        rows.append((fixture, f"{latency:.1f} s"))
    rows.append(("total", _fraction(*totals[1])))
    rows.append(("total", _fraction(*totals[2])))
    return rows


def figures() -> list[Figure]:
    """Every recounted figure with the table row it belongs to."""
    out = [
        Figure("5.2", row, value, MODIFY_LOCAL)
        for row, value in modify_figures(MODIFY_LOCAL, integrity=True)
    ]
    out += [
        Figure("5.2", row, value, MODIFY_CLOUD)
        for row, value in modify_figures(MODIFY_CLOUD, integrity=False)
    ]
    out += [
        Figure("5.3", row, value, "rav-2026-09-15-*, " + COVERAGE) for row, value in rav_figures()
    ]
    out += [Figure("5.4", row, value, "ask-2026-09-15.*.json") for row, value in ask_figures()]
    return out


def missing_from(markdown: str) -> list[Figure]:
    """Figures whose value does not appear on a table line naming their row."""
    lines = [line for line in markdown.splitlines() if line.startswith("|")]
    return [f for f in figures() if not any(f.row in line and f.value in line for line in lines)]


def main() -> None:
    """Print every recounted figure, grouped by table."""
    current = ""
    for figure in figures():
        if figure.table != current:
            current = figure.table
            print(f"\nTable {current}")
        print(f"  {figure.row:<45} {figure.value:<22} {figure.source}")


if __name__ == "__main__":
    main()
