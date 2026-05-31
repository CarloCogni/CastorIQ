# islam/scheduling/services/floor_health.py
"""Per-floor Project Health Matrix — two independent diagnostic axes per floor.

Cell 1  Build Quality   — is the schedule CONSTRUCTED correctly on this floor?
        Sources: DCMA-style logic gaps (missing pred/succ, hard constraints),
                 anomaly flags (stall / stat-outlier / FS-violation / etc.),
                 peer-comparison gaps (CSI codes present on >=60% of floors but absent here).

Cell 2  Project Status  — where is this floor HEADED?
        Sources: finish variance on completed tasks (delay breadth + severity),
                 ML P(on-time) averaged across incomplete floor tasks.

The two cells are intentionally independent: a floor can be well-built but behind
schedule, or poorly-built but currently on time.

Reuses: detect_anomalies(), predict_all_incomplete()
        Direct Task/TaskDependency queries for DCMA-style logic checks and delay stats.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date

logger = logging.getLogger(__name__)

# ── Floor extraction (mirrors timelocation.py and completion_ml.py) ───────────

_FLOOR_RE = re.compile(r"^(B0?[1-3]|L\d{1,2}|R0?[1-2])", re.IGNORECASE)
_FLOOR_ORDINALS: dict[str, int] = {
    "B03": 0,
    "B02": 1,
    "B01": 2,
    "L00": 3,
    "L01": 4,
    "L02": 5,
    "L03": 6,
    "L04": 7,
    "L05": 8,
    "L06": 9,
    "L07": 10,
    "L08": 11,
    "L09": 12,
    "L10": 13,
    "L11": 14,
    "L12": 15,
    "R01": 16,
    "R02": 17,
}
_FLOOR_LABELS: dict[str, str] = {
    "B03": "Basement 3",
    "B02": "Basement 2",
    "B01": "Basement 1",
    "L00": "Ground Floor",
    **{f"L{i:02d}": f"Level {i}" for i in range(1, 13)},
    "R01": "Roof 1",
    "R02": "Roof 2",
}

# Admin/doc activity-code prefixes excluded from spatial analysis (same as timelocation.py)
_ADMIN_PREFIXES = frozenset(
    {
        "GEND",
        "MATM",
        "MCLA",
        "MCLS",
        "PRQP",
        "PRQL",
        "TPTS",
        "TPTA",
        "SCCA",
        "SCCS",
        "MOCA",
        "MOCS",
        "GRQS",
        "GRQA",
        "GRQM",
        "GRQG",
        "SUMG",
        "GENG",
        "GENK",
        "GENN",
        "GENP",
        "GENS",
    }
)

_HARD_CONSTRAINT_TYPES = frozenset(
    {"Mandatory Finish", "Finish On", "Finish On or Before", "Mandatory Start", "Start On"}
)

# Peer-comparison: a CSI code present on >= this fraction of data floors is "expected"
_PEER_PRESENCE_THRESHOLD = 0.60

# Minimum tasks a floor must have to appear in the matrix
_MIN_FLOOR_TASKS = 3

# Peer comparison requires this many data floors minimum
_MIN_FLOORS_FOR_PEERS = 3

_CSI_RE = re.compile(r"-[A-Z]*(\d{2})\d{4}")
_CSI_NAMES: dict[str, str] = {
    "01": "General Requirements",
    "03": "Concrete",
    "04": "Masonry",
    "05": "Metals",
    "07": "Thermal/Moisture",
    "08": "Openings",
    "09": "Finishes",
    "10": "Specialties",
    "13": "Special Constr.",
    "14": "Conveying",
    "21": "Fire Suppression",
    "22": "Plumbing",
    "23": "HVAC",
    "26": "Electrical",
    "27": "Communications",
    "28": "Electronic Safety",
    "31": "Earthwork",
}


def _extract_floor_token(activity_code: str) -> str | None:
    """Return canonical floor token ('B03', 'L05', 'R01') or None."""
    prefix = (activity_code or "").split("-")[0]
    m = _FLOOR_RE.match(prefix)
    if not m:
        return None
    raw = m.group(1).upper()
    if len(raw) == 2:
        raw = raw[0] + "0" + raw[1]
    return raw if raw in _FLOOR_ORDINALS else None


def _is_admin(activity_code: str) -> bool:
    return (activity_code or "")[:4].upper() in _ADMIN_PREFIXES


def _csi(activity_code: str) -> str:
    m = _CSI_RE.search(activity_code or "")
    return m.group(1) if m else "XX"


def _floor_band(tok: str) -> str:
    """Structural band for peer CSI comparison: 'B' basement, 'L' typical, 'R' roof."""
    if tok.startswith("B"):
        return "B"
    if tok.startswith("R"):
        return "R"
    return "L"


def _score_build_quality(n_tasks: int, n_logic: int, n_anomaly: int, n_gaps: int) -> int:
    """0-100 Build Quality score.  Higher = better.

    Defect density = (logic_issues + logic_anomalies + band_gaps × 2) / tasks.
    Score = 100 − defect_rate × 300  (each 1 % of tasks defective = 3 pts off).

    No soft caps: the formula maps directly so a clean floor reads 100 and a floor
    with ≥17 % defect density reads 0.  Each 1 % of tasks with a structural defect
    = 6 points off.  This gives genuine red/amber/green contrast across typical
    construction project defect-rate ranges (0–15 %).

    Counts only DCMA-style logic gaps and structural anomalies (FS violations,
    unrealistic baselines, orphans, cycles) — not execution metrics like stall
    or statistical outliers, which belong to Project Status.
    """
    total = n_logic + n_anomaly + n_gaps * 2
    rate = total / max(n_tasks, 1)
    return max(0, round(100 - rate * 600))


def _score_project_status(
    delay_pct: float,
    avg_fv_days: float,
    avg_prob_on_time: float | None,
    n_incomplete: int,
    n_completed: int,
) -> int:
    """0-100 Project Status score.  Higher = better.

    Backward axis (what already happened, from completed tasks):
      delay_pct × 0.8  → up to 80 pts deducted.
      avg_fv_days / 5  → up to 20 pts additional severity deduction.
      No completed tasks → backward score = 100 (no evidence of delay yet).

    Forward axis (ML P(on-time) for incomplete tasks):
      avg_prob_on_time × 100 → 0-100.
      No ML data → forward axis omitted, score = backward only.
    """
    if n_completed > 0:
        backward = max(0.0, 100 - delay_pct * 0.8 - min(avg_fv_days / 5, 20))
    else:
        backward = 100.0

    if avg_prob_on_time is not None and n_incomplete > 0:
        forward = avg_prob_on_time * 100
        return round(0.5 * backward + 0.5 * forward)
    return round(backward)


def compute_floor_health(project_id: str, override_map: dict | None = None) -> dict:
    """Compute per-floor Build Quality and Project Status scores.

    Returns:
        has_data    — bool
        n_floors    — int (floors with >= 3 tasks)
        floors      — list[dict], sorted top floor first (R02 → B03)
        as_of       — ISO date string
    """
    from islam.scheduling.models import Task, TaskDependency

    from .anomaly_detect import detect_anomalies
    from .completion_ml import predict_all_incomplete

    today = date.today()

    # ── 1. Load all physical tasks with date data ─────────────────────────────
    all_tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
        .only(
            "pk",
            "name",
            "activity_code",
            "stage",
            "status",
            "start_date",
            "end_date",
            "actual_start",
            "actual_end",
            "constraint_type",
            "total_float",
        )
    )
    if not all_tasks:
        return {"has_data": False}

    # Group by floor token; skip admin and unlocated tasks
    floor_tasks: dict[str, list] = defaultdict(list)
    pk_to_floor: dict[str, str] = {}
    for t in all_tasks:
        if _is_admin(t.activity_code or ""):
            continue
        tok = _extract_floor_token(t.activity_code or "")
        if tok:
            floor_tasks[tok].append(t)
            pk_to_floor[str(t.pk)] = tok

    if not floor_tasks:
        return {"has_data": False}

    # ── 2. Dependency sets for logic-gap checks ───────────────────────────────
    all_pks = list(pk_to_floor.keys())
    tasks_with_predecessors: set[str] = {
        str(pk)
        for pk in TaskDependency.objects.filter(successor_id__in=all_pks)
        .values_list("successor_id", flat=True)
        .distinct()
    }
    tasks_with_successors: set[str] = {
        str(pk)
        for pk in TaskDependency.objects.filter(predecessor_id__in=all_pks)
        .values_list("predecessor_id", flat=True)
        .distinct()
    }

    # ── 3. Anomaly data — logic anomalies only for Build Quality ─────────────
    # running_long and statistical_outliers measure execution risk → Project Status.
    # Build Quality only counts structural/data-quality errors: FS violations,
    # unrealistic baselines, mid-network orphans, circular dependencies.
    anomaly_result = detect_anomalies(project_id, override_map=override_map)
    anomaly_by_pk: dict[str, list[dict]] = defaultdict(list)
    if anomaly_result.get("has_data"):
        for item in anomaly_result.get("logic_anomalies", []):
            pk = item.get("task_pk", "")
            if pk in pk_to_floor:
                anomaly_by_pk[pk].append(item)

    # ── 4. ML predictions — all incomplete tasks ──────────────────────────────
    ml_by_pk: dict[str, float] = {}
    for pred in predict_all_incomplete(project_id):
        pk = pred["task_pk"]
        if pk in pk_to_floor:
            ml_by_pk[pk] = pred["probability_on_time"]

    # ── 5. Cross-floor CSI peer-comparison — within structural band ───────────
    # Floors are grouped into three structural bands: B (basement), L (typical), R (roof).
    # A trade is "expected" on a floor only when >=60 % of that floor's OWN BAND have it.
    # This prevents naturally-absent trades (e.g. no Masonry on a roof floor) from
    # triggering false Build Quality penalties.
    # Bands with < 3 data floors are excluded from peer comparison entirely.
    data_floors = [tok for tok, tasks in floor_tasks.items() if len(tasks) >= _MIN_FLOOR_TASKS]

    floor_csi_sets: dict[str, set[str]] = {}
    # band → {csi → count of floors in band that have it}
    band_csi_count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    band_floor_count: dict[str, int] = defaultdict(int)

    for tok in data_floors:
        csi_set = {
            override_map.get(str(t.pk)) or _csi(t.activity_code or "")
            if override_map
            else _csi(t.activity_code or "")
            for t in floor_tasks[tok]
        } - {"XX"}
        floor_csi_sets[tok] = csi_set
        band = _floor_band(tok)
        band_floor_count[band] += 1
        for csi in csi_set:
            band_csi_count[band][csi] += 1

    # For each band with >=3 floors, build the set of "expected" CSI codes
    band_expected_csi: dict[str, set[str]] = {}
    for band, n_band in band_floor_count.items():
        if n_band >= _MIN_FLOORS_FOR_PEERS:
            band_expected_csi[band] = {
                csi
                for csi, n in band_csi_count[band].items()
                if n >= _PEER_PRESENCE_THRESHOLD * n_band
            }
        else:
            band_expected_csi[band] = set()  # too few floors for meaningful comparison

    # ── 6. Compute per-floor scores ───────────────────────────────────────────
    results: list[dict] = []

    for tok in data_floors:
        tasks = floor_tasks[tok]
        n_tasks = len(tasks)
        task_pks = {str(t.pk) for t in tasks}

        # ── Cell 1: Build Quality ─────────────────────────────────────────────
        no_pred = [t for t in tasks if str(t.pk) not in tasks_with_predecessors]
        no_succ = [t for t in tasks if str(t.pk) not in tasks_with_successors]
        hard_constrained = [t for t in tasks if (t.constraint_type or "") in _HARD_CONSTRAINT_TYPES]
        logic_issue_pks = (
            {str(t.pk) for t in no_pred}
            | {str(t.pk) for t in no_succ}
            | {str(t.pk) for t in hard_constrained}
        )
        n_logic = len(logic_issue_pks)

        floor_anomaly_pks = {pk for pk in task_pks if pk in anomaly_by_pk}
        n_anomaly = len(floor_anomaly_pks)

        floor_csi = floor_csi_sets.get(tok, set())
        band = _floor_band(tok)
        missing_csi = band_expected_csi.get(band, set()) - floor_csi
        gaps = [f"{_CSI_NAMES.get(csi, 'Div ' + csi)} (CSI {csi})" for csi in sorted(missing_csi)]

        n_defects = n_logic + n_anomaly + len(gaps) * 2
        defect_rate_pct = round(n_defects / max(n_tasks, 1) * 100, 1)
        bq_score = _score_build_quality(n_tasks, n_logic, n_anomaly, len(gaps))

        # Worst BQ tasks: logic issues first, then anomalies (up to 5 unique)
        bq_worst: list[dict] = []
        seen_bq: set[str] = set()
        for t in no_pred[:3]:
            pk = str(t.pk)
            if pk not in seen_bq:
                bq_worst.append({"pk": pk, "name": t.name[:60], "issue": "missing predecessor"})
                seen_bq.add(pk)
        for t in no_succ[:2]:
            pk = str(t.pk)
            if pk not in seen_bq:
                bq_worst.append({"pk": pk, "name": t.name[:60], "issue": "missing successor"})
                seen_bq.add(pk)
        for t in hard_constrained[:2]:
            pk = str(t.pk)
            if pk not in seen_bq:
                bq_worst.append(
                    {
                        "pk": pk,
                        "name": t.name[:60],
                        "issue": f"hard constraint ({t.constraint_type})",
                    }
                )
                seen_bq.add(pk)
        for pk in sorted(floor_anomaly_pks, key=lambda p: -len(anomaly_by_pk[p])):
            if pk not in seen_bq and len(bq_worst) < 5:
                types = ", ".join({a["anomaly_type"] for a in anomaly_by_pk[pk]})
                task = next((t for t in tasks if str(t.pk) == pk), None)
                if task:
                    bq_worst.append({"pk": pk, "name": task.name[:60], "issue": types})
                    seen_bq.add(pk)

        # ── Cell 2: Project Status ────────────────────────────────────────────
        completed_tasks = [t for t in tasks if t.actual_end is not None]
        n_completed = len(completed_tasks)

        delayed_tasks = [
            t for t in completed_tasks if t.actual_end and t.end_date and t.actual_end > t.end_date
        ]
        n_delayed = len(delayed_tasks)
        delay_pct = (n_delayed / n_completed * 100) if n_completed > 0 else 0.0

        fv_days = [
            (t.actual_end - t.end_date).days for t in delayed_tasks if t.actual_end and t.end_date
        ]
        avg_fv_days = sum(fv_days) / len(fv_days) if fv_days else 0.0

        incomplete_tasks = [t for t in tasks if t.actual_end is None]
        n_incomplete = len(incomplete_tasks)

        floor_probs = [ml_by_pk[str(t.pk)] for t in incomplete_tasks if str(t.pk) in ml_by_pk]
        avg_prob = (sum(floor_probs) / len(floor_probs)) if floor_probs else None

        ps_score = _score_project_status(
            delay_pct, avg_fv_days, avg_prob, n_incomplete, n_completed
        )

        # Worst PS tasks: most-delayed completed (up to 3) + lowest ML prob (up to 2)
        ps_worst: list[dict] = []
        for t in sorted(
            delayed_tasks, key=lambda x: (x.actual_end - x.end_date).days, reverse=True
        )[:3]:
            fv = (t.actual_end - t.end_date).days
            ps_worst.append(
                {
                    "pk": str(t.pk),
                    "name": t.name[:60],
                    "issue": f"{fv}d late",
                    "days_late": fv,
                }
            )
        at_risk = sorted(
            [(t, ml_by_pk[str(t.pk)]) for t in incomplete_tasks if str(t.pk) in ml_by_pk],
            key=lambda x: x[1],
        )[:2]
        for t, prob in at_risk:
            if len(ps_worst) < 5:
                ps_worst.append(
                    {
                        "pk": str(t.pk),
                        "name": t.name[:60],
                        "issue": f"P(on-time) = {round(prob * 100)}%",
                        "prob": prob,
                    }
                )

        results.append(
            {
                "token": tok,
                "label": _FLOOR_LABELS.get(tok, tok),
                "ordinal": _FLOOR_ORDINALS.get(tok, 99),
                "task_count": n_tasks,
                "build_quality": {
                    "score": bq_score,
                    "logic_issues": n_logic,
                    "anomaly_flags": n_anomaly,
                    "gap_count": len(gaps),
                    "gaps": gaps,
                    "defect_count": n_defects,
                    "defect_rate_pct": defect_rate_pct,
                    "worst_tasks": bq_worst[:5],
                },
                "project_status": {
                    "score": ps_score,
                    "completed_count": n_completed,
                    "delayed_count": n_delayed,
                    "delay_pct": round(delay_pct, 1),
                    "avg_finish_variance_days": round(avg_fv_days, 1),
                    "incomplete_count": n_incomplete,
                    "avg_prob_on_time": round(avg_prob, 3) if avg_prob is not None else None,
                    "worst_tasks": ps_worst,
                },
            }
        )

    if not results:
        return {"has_data": False}

    # Sort top floor first (R02 → B03) — matches Time-Location chart visual order
    results.sort(key=lambda r: -r["ordinal"])

    logger.info("Floor health computed for project %s: %d floors", project_id, len(results))

    return {
        "has_data": True,
        "n_floors": len(results),
        "floors": results,
        "as_of": today.isoformat(),
    }
