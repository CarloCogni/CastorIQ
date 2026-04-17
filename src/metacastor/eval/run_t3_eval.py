# metacastor/eval/run_t3_eval.py
"""
Evaluation harness for MetaCastor Deliverable 5 — certified Tier 3 reuse.

Mirrors the D2 classifier harness (`run_eval.py`) but targets Tier 3 code
generation. For each eval case the runner:

  1. (Optional) Retrieves skill examples — when `--inject` is set, the
     seeded Tier 3 reference bank can surface via the skill retriever.
  2. Calls `Tier3Planner.generate_code(query, entity_context, skill_examples=...)`
     at temperature=0 for deterministic output.
  3. Copies the IFC fixture to a temp path and runs `Tier3Executor.execute()`
     against the copy — the fixture is never mutated.
  4. Scores the outcome on five axes and records timing + injection flag.

A/B comparison: run twice, once without `--inject` (baseline), once with.
Results land in `results/` as timestamped JSON, same convention as D2.

Usage:
    uv run python -m metacastor.eval.run_t3_eval                 # baseline
    uv run manage.py seed_tier3_bank --file metacastor/eval/t3_reference_bank.jsonl
    uv run python -m metacastor.eval.run_t3_eval --inject        # with injection

The two runs should be launched back-to-back against the same Ollama build
so hardware and model state are held constant.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
sys.path.insert(0, str(Path(__file__).parents[2]))  # src/
import django  # noqa: E402

django.setup()

from metacastor.services.skill_retriever import retrieve as retrieve_skill_examples  # noqa: E402
from writeback.services.tier3_executor import Tier3ExecutionError, Tier3Executor  # noqa: E402
from writeback.services.tier3_planner import CodeGenerationError, Tier3Planner  # noqa: E402

CASES_FILE = Path(__file__).parent / "t3_eval_cases.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"

AXES = ["parse_ok", "validation_ok", "confidence_ok", "execution_ok", "post_state_ok"]
TIER_ORDER = ["CREATE", "DELETE", "RELATIONSHIP", "SPATIAL", "OTHER"]

# Pinned for reproducibility, same rationale as run_eval.py.
EVAL_MODEL = "llama3.1:8b"
EVAL_TEMPERATURE = 0
# Tier 3 confidence gate used in production (_propose_tier3 at line 1219).
CONFIDENCE_GATE = 70


def _score_post_state(result: dict, truth: dict) -> bool:
    """
    Soft match the executor's return dict against the ground-truth post state.

    Strict change-by-change diffing is brittle (GUIDs differ run-to-run), so
    we match on aggregate shape:

      - min_changes:     `len(changes) >= truth["min_changes"]`
      - entity_types:    every type in truth["entity_types"] appears in the
                         change list's ifc_type values (case-sensitive — IFC
                         type names are canonical).
      - summary_contains: every substring in truth["summary_contains"]
                         appears in the summary (case-insensitive).
    """
    changes = result.get("changes", []) or []
    summary = (result.get("summary") or "").lower()

    if len(changes) < int(truth.get("min_changes", 0)):
        return False

    expected_types = set(truth.get("entity_types", []) or [])
    observed_types = {c.get("ifc_type", "") for c in changes if isinstance(c, dict)}
    if not expected_types.issubset(observed_types):
        return False

    for needle in truth.get("summary_contains", []) or []:
        if needle.lower() not in summary:
            return False

    return True


def _score_case(
    planner: Tier3Planner,
    case: dict,
    fixtures_root: Path,
    inject: bool,
) -> dict:
    """
    Run one eval case end-to-end and return its score dict.

    Never raises. Every exception path degrades to a False score on the
    relevant axis so one bad case never aborts the run.
    """
    scores = {axis: False for axis in AXES}
    gen_ms: float | None = None
    reference_injected = False
    failure = None
    code_length: int | None = None

    query = case["query"]
    entity_context = case["entity_context"]

    # 1. Retrieve skill examples only when injection is enabled.
    skill_examples = retrieve_skill_examples(query) if inject else []
    # Count tier-3 examples with code — this is the real activation signal.
    qualifying = [
        ex for ex in (skill_examples or []) if ex.get("tier") == 3 and ex.get("generated_code")
    ]
    reference_injected = bool(qualifying)

    # 2. Generate code.
    t0 = time.perf_counter()
    try:
        result = planner.generate_code(query, entity_context, skill_examples=skill_examples or None)
        scores["parse_ok"] = True
        # generate_code() already calls _validate_result + _check_forbidden_patterns,
        # so if we're here both passed.
        scores["validation_ok"] = True
        code_length = len(result.get("code", ""))
        confidence = int(result.get("confidence", 0) or 0)
        scores["confidence_ok"] = confidence >= CONFIDENCE_GATE
    except CodeGenerationError as e:
        failure = f"CodeGenerationError: {e}"
        gen_ms = (time.perf_counter() - t0) * 1000
        return {
            "id": case["id"],
            "difficulty_tier": case.get("difficulty_tier", "OTHER"),
            "scores": scores,
            "passed": False,
            "gen_ms": round(gen_ms, 1),
            "reference_injected": reference_injected,
            "code_length": code_length,
            "failure": failure,
        }
    except Exception as e:
        failure = f"Unexpected generate_code error: {type(e).__name__}: {e}"
        gen_ms = (time.perf_counter() - t0) * 1000
        return {
            "id": case["id"],
            "difficulty_tier": case.get("difficulty_tier", "OTHER"),
            "scores": scores,
            "passed": False,
            "gen_ms": round(gen_ms, 1),
            "reference_injected": reference_injected,
            "code_length": code_length,
            "failure": failure,
        }
    gen_ms = (time.perf_counter() - t0) * 1000

    # 3. Execute against a fixture copy — DO NOT mutate the fixture.
    fixture_src = fixtures_root / Path(case["ifc_fixture"]).name
    if not fixture_src.exists():
        failure = f"Fixture not found: {fixture_src}"
        return {
            "id": case["id"],
            "difficulty_tier": case.get("difficulty_tier", "OTHER"),
            "scores": scores,
            "passed": all(scores.values()),
            "gen_ms": round(gen_ms, 1),
            "reference_injected": reference_injected,
            "code_length": code_length,
            "failure": failure,
        }

    tmp_dir = Path(tempfile.mkdtemp(prefix="castor_t3_eval_"))
    fixture_copy = tmp_dir / fixture_src.name
    try:
        shutil.copy2(fixture_src, fixture_copy)
        executor = Tier3Executor(fixture_copy)
        executor.execute(result["code"])
        scores["execution_ok"] = True

        # `Tier3Executor.execute()` discards the raw modify_ifc() return dict
        # via `_result_to_changes()`, but we added `last_result` on the
        # executor so the D5 harness can score against the real summary and
        # change list instead of reconstructing one from descriptions.
        execution_result = executor.last_result or {}
        truth = case.get("ground_truth_post_state") or {}
        scores["post_state_ok"] = _score_post_state(execution_result, truth)
    except Tier3ExecutionError as e:
        failure = f"Tier3ExecutionError: {e}"
    except Exception as e:
        failure = f"Unexpected execution error: {type(e).__name__}: {e}"
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass

    return {
        "id": case["id"],
        "difficulty_tier": case.get("difficulty_tier", "OTHER"),
        "scores": scores,
        "passed": all(scores.values()),
        "gen_ms": round(gen_ms, 1),
        "reference_injected": reference_injected,
        "code_length": code_length,
        "failure": failure,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="MetaCastor D5 Tier-3 eval harness.")
    parser.add_argument(
        "--inject",
        action="store_true",
        help="Enable Tier 3 reference pattern injection from the skill bank (D5 mode).",
    )
    parser.add_argument(
        "--cases",
        type=str,
        default=None,
        help="Path to an alternate cases JSONL. Defaults to t3_eval_cases.jsonl.",
    )
    args = parser.parse_args()

    cases_file = Path(args.cases) if args.cases else CASES_FILE
    cases = [
        json.loads(line)
        for line in cases_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    from django.conf import settings as django_settings

    django_settings.OLLAMA_MODEL = EVAL_MODEL
    planner = Tier3Planner(temperature=EVAL_TEMPERATURE)

    mode = "D5 (with tier-3 injection)" if args.inject else "baseline (no injection)"
    print(
        f"Model: {EVAL_MODEL}  |  Temperature: {EVAL_TEMPERATURE}  |  "
        f"Cases: {len(cases)}  |  Mode: {mode}"
    )

    fixtures_root = Path(__file__).parent / "fixtures"
    per_case_results: list[dict] = []
    by_tier: dict[str, list[dict]] = {}

    for case in cases:
        res = _score_case(planner, case, fixtures_root, args.inject)
        per_case_results.append(res)
        by_tier.setdefault(res["difficulty_tier"], []).append(res)
        short_fail = (res.get("failure") or "")[:60]
        print(
            f"  {res['id']:<10} {res['difficulty_tier']:<13} "
            f"pass={str(res['passed']):<5} "
            f"{'ref' if res['reference_injected'] else '   '} "
            f"gen={res['gen_ms']:>6.0f}ms "
            f"{short_fail}"
        )

    # ── Aggregate ─────────────────────────────────────────────────────────────
    col_w = 15
    header = f"{'Tier':<14}" + "".join(f"{a:>{col_w}}" for a in AXES) + "    n"
    print(f"\n{header}")
    print("-" * (14 + col_w * len(AXES) + 5))

    totals: dict[str, list[bool]] = {a: [] for a in AXES}
    by_tier_summary: dict[str, dict] = {}
    for difficulty in TIER_ORDER:
        tier_results = by_tier.get(difficulty, [])
        if not tier_results:
            continue
        n = len(tier_results)
        row = {a: sum(r["scores"][a] for r in tier_results) / n for a in AXES}
        for a in AXES:
            totals[a].extend(r["scores"][a] for r in tier_results)
        scores_str = "".join(f"{row[a]:>{col_w}.0%}" for a in AXES)
        print(f"{difficulty:<14}{scores_str}  {n:>3}")
        by_tier_summary[difficulty] = {a: round(row[a], 4) for a in AXES}
        by_tier_summary[difficulty]["n"] = n

    n_all = len(per_case_results) or 1
    print("-" * (14 + col_w * len(AXES) + 5))
    overall = {a: round(sum(totals[a]) / n_all, 4) for a in AXES}
    overall_str = "".join(f"{overall[a]:>{col_w}.0%}" for a in AXES)
    print(f"{'OVERALL':<14}{overall_str}  {n_all:>3}")

    # Aggregate metrics specific to D5.
    activation_count = sum(1 for r in per_case_results if r["reference_injected"])
    gen_times = [r["gen_ms"] for r in per_case_results if r["gen_ms"] is not None]
    median_gen_ms = sorted(gen_times)[len(gen_times) // 2] if gen_times else None

    print(
        f"\nActivation rate: {activation_count}/{n_all} "
        f"({activation_count / n_all:.0%})   "
        f"Median gen time: {median_gen_ms:.0f} ms"
        if median_gen_ms is not None
        else f"\nActivation rate: {activation_count}/{n_all}"
    )

    # ── Persist ───────────────────────────────────────────────────────────────
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    model_slug = EVAL_MODEL.replace(":", "-").replace("/", "-")
    inject_suffix = "_injected" if args.inject else "_baseline"
    filename = (
        f"{now.strftime('%Y%m%d_%H%M%S')}_{model_slug}_T{EVAL_TEMPERATURE}_t3{inject_suffix}.json"
    )
    output = {
        "timestamp": now.isoformat(timespec="seconds"),
        "model": EVAL_MODEL,
        "temperature": EVAL_TEMPERATURE,
        "injection": args.inject,
        "cases_file": cases_file.name,
        "n_cases": n_all,
        "activation_count": activation_count,
        "median_gen_ms": median_gen_ms,
        "by_tier": by_tier_summary,
        "overall": overall,
        "per_case": per_case_results,
    }
    out_path = RESULTS_DIR / filename
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    main()
