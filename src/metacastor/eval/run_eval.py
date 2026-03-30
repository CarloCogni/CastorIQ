# metacastor/eval/run_eval.py
"""Evaluation harness for MetaCastor intent classification baseline."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
sys.path.insert(0, str(Path(__file__).parents[2]))  # src/
import django

django.setup()

from writeback.services.intent_classifier import IntentClassifier, IntentParseError  # noqa: E402

CASES_FILE = Path(__file__).parent / "eval_cases.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"
METRICS = ["operation", "tier", "filter_type", "parameters"]
TIER_ORDER = ["TRIVIAL", "STANDARD", "AMBIGUOUS", "ESCALATION", "ADVERSARIAL"]

# Pinned model — eval results are only comparable when run against the same model.
# llama3.1:8b is the minimum-spec baseline: 8K context, runs on consumer hardware.
# Change this only when deliberately measuring a different model.
EVAL_MODEL = "llama3.1:8b"

# Temperature=0 for deterministic, reproducible output.
# Production uses 0.1; eval uses 0 so the same input always yields the same output.
EVAL_TEMPERATURE = 0


def score_case(result: dict | list, truth: dict) -> dict[str, bool]:
    """Compare classifier output against ground truth on four axes."""
    if isinstance(result, list):
        result = result[0]
    # Use `or {}` so that filter: null in ground truth or result doesn't raise AttributeError.
    result_filter = result.get("filter") or {}
    truth_filter = truth.get("filter") or {}
    filter_type_match = result_filter.get("ifc_type") == truth_filter.get("ifc_type")
    params_match = result.get("pset") == truth.get("pset") and result.get("property") == truth.get(
        "property"
    )
    return {
        "operation": result.get("operation") == truth.get("operation"),
        "tier": result.get("tier") == truth.get("tier"),
        "filter_type": filter_type_match,
        "parameters": params_match,
    }


def main() -> None:
    cases = [
        json.loads(line)
        for line in CASES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # Pin the model so results are reproducible regardless of .env or user settings.
    from django.conf import settings as django_settings

    django_settings.OLLAMA_MODEL = EVAL_MODEL
    clf = IntentClassifier(temperature=EVAL_TEMPERATURE)
    print(f"Model: {EVAL_MODEL}  |  Temperature: {EVAL_TEMPERATURE}  |  Cases: {len(cases)}")
    results: dict[str, list[dict]] = {}
    per_case_results: list[dict] = []

    for case in cases:
        difficulty = case["difficulty_tier"]
        results.setdefault(difficulty, [])
        try:
            intent = clf.classify(case["query"], case["entity_context"])
            scores = score_case(intent, case["ground_truth_intent"])
        except (IntentParseError, Exception):
            scores = {m: False for m in METRICS}
        results[difficulty].append(scores)
        per_case_results.append(
            {
                "id": case["id"],
                "difficulty_tier": difficulty,
                "passed": all(scores.values()),
                "scores": scores,
            }
        )

    col_w = 13
    header = f"{'Tier':<14}" + "".join(f"{m:>{col_w}}" for m in METRICS) + "    n"
    print(f"\n{header}")
    print("-" * (14 + col_w * len(METRICS) + 5))

    totals: dict[str, list[bool]] = {m: [] for m in METRICS}
    by_tier: dict[str, dict] = {}
    for difficulty in TIER_ORDER:
        tier_results = results.get(difficulty, [])
        if not tier_results:
            continue
        n = len(tier_results)
        row = {m: sum(r[m] for r in tier_results) / n for m in METRICS}
        for m in METRICS:
            totals[m].extend(r[m] for r in tier_results)
        scores_str = "".join(f"{row[m]:>{col_w}.0%}" for m in METRICS)
        print(f"{difficulty:<14}{scores_str}  {n:>3}")
        by_tier[difficulty] = {m: round(row[m], 4) for m in METRICS}
        by_tier[difficulty]["n"] = n

    n_all = sum(len(v) for v in results.values())
    print("-" * (14 + col_w * len(METRICS) + 5))
    overall = {m: round(sum(totals[m]) / n_all, 4) for m in METRICS}
    overall_str = "".join(f"{overall[m]:>{col_w}.0%}" for m in METRICS)
    print(f"{'OVERALL':<14}{overall_str}  {n_all:>3}\n")

    # ── Save results to file ──────────────────────────────────────────────────
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    model_slug = EVAL_MODEL.replace(":", "-").replace("/", "-")
    filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{model_slug}_T{EVAL_TEMPERATURE}.json"
    output = {
        "timestamp": now.isoformat(timespec="seconds"),
        "model": EVAL_MODEL,
        "temperature": EVAL_TEMPERATURE,
        "cases_file": CASES_FILE.name,
        "n_cases": n_all,
        "by_tier": by_tier,
        "overall": overall,
        "per_case": per_case_results,
    }
    out_path = RESULTS_DIR / filename
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Results saved to: {out_path}")


if __name__ == "__main__":
    main()
