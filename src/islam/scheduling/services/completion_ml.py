# islam/scheduling/services/completion_ml.py
"""Completion Probability ML — logistic regression in pure numpy.

No external ML library required.  sklearn/xgboost are not installed; we
implement logistic regression, stratified split, AUC, Brier score, and
calibration from first principles using only numpy.

LEAKAGE POLICY (strict):
  Features USED: everything knowable at task START — planned dates,
    baseline float, network topology (pred/succ counts), planned cost,
    floor location, and the historical on-time rate of the same CSI trade
    computed from OTHER completed tasks (leave-one-out target encoding).
  Features FORBIDDEN: actual_finish, actual_duration, actual_cost,
    percent_complete, or anything derived from actual progress.
  CAVEAT: total_float is the CPM float from the LAST schedule calculation.
    For completed tasks this reflects the current CPM state, which may
    incorporate post-completion progress.  Treat float importance
    with appropriate caution.
"""

from __future__ import annotations

import logging
import math
import re

import numpy as np

from .utils import get_project_data_date

logger = logging.getLogger(__name__)

# ── Feature extractors (re-used from sibling services) ────────────────────────

_FLOOR_RE = re.compile(r"^(B0?[1-3]|L\d{1,2}|R0?[1-2])", re.IGNORECASE)
_CSI_RE = re.compile(r"-[A-Z]*(\d{2})\d{4}")
_FLOOR_ORDINALS = {
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

_CSI_NAMES = {
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
    "01": "General Requirements",
}

# Categorical constants (top N + "other" bucket)
_TOP_CSI_N = 8  # top 8 CSI trades by training frequency
_STAGES = ("substructure", "structure", "envelope", "mep", "finishes", "external", "")
_ACTIVITY_TYPES = ("Construction", "Submittal", "Approval", "Procurement", "Testing / Inspection")


def _floor_ordinal(activity_code: str) -> float:
    prefix = (activity_code or "").split("-")[0]
    m = _FLOOR_RE.match(prefix)
    if not m:
        return -1.0
    raw = m.group(1).upper()
    if len(raw) == 2:
        raw = raw[0] + "0" + raw[1]
    return float(_FLOOR_ORDINALS.get(raw, -1))


def _csi(activity_code: str) -> str:
    m = _CSI_RE.search(activity_code or "")
    return m.group(1) if m else "XX"


def _activity_type(name: str) -> str:
    n = name.lower()
    if "submit" in n:
        return "Submittal"
    if "approv" in n:
        return "Approval"
    if "procur" in n or "purchase" in n:
        return "Procurement"
    if "inspect" in n or "testing" in n or "commission" in n:
        return "Testing / Inspection"
    return "Construction"


# ── Logistic regression (gradient descent, L2 regularisation) ─────────────────


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))


def _train_logreg(
    x_mat: np.ndarray,
    y: np.ndarray,
    l2: float = 0.05,
    lr: float = 0.1,
    n_iter: int = 2000,
) -> tuple[np.ndarray, float]:
    """Gradient-descent logistic regression.  Returns (weights, bias)."""
    n, p = x_mat.shape
    w = np.zeros(p)
    b = 0.0
    for _ in range(n_iter):
        prob = _sigmoid(x_mat @ w + b)
        err = prob - y
        w -= lr * (x_mat.T @ err / n + l2 * w)
        b -= lr * err.mean()
    return w, b


def _predict_proba(x_mat: np.ndarray, w: np.ndarray, b: float) -> np.ndarray:
    return _sigmoid(x_mat @ w + b)


# ── Metrics ────────────────────────────────────────────────────────────────────


def _roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    order = np.argsort(-y_score)
    ys = y_true[order]
    n_pos = ys.sum()
    n_neg = len(ys) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    tpr = np.concatenate([[0.0], np.cumsum(ys) / n_pos])
    fpr = np.concatenate([[0.0], np.cumsum(1 - ys) / n_neg])
    # np.trapezoid preferred in NumPy ≥ 2.0; fall back to np.trapz
    integrate = getattr(np, "trapezoid", None) or np.trapz
    return float(integrate(tpr, fpr))


def _brier(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    return float(np.mean((y_prob - y_true) ** 2))


def _calibration(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 5) -> list[dict]:
    edges = np.linspace(0, 1, n_bins + 1)
    result = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (y_prob >= lo) & (y_prob < hi)
        if mask.sum() < 10:
            continue
        result.append(
            {
                "range": f"{lo:.1f}–{hi:.1f}",
                "n": int(mask.sum()),
                "mean_pred": round(float(y_prob[mask].mean()), 3),
                "actual_rate": round(float(y_true[mask].mean()), 3),
            }
        )
    return result


def _stratified_split(
    n: int,
    y: np.ndarray,
    test_frac: float = 0.25,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    pos = np.where(y == 1)[0]
    neg = np.where(y == 0)[0]
    rng.shuffle(pos)
    rng.shuffle(neg)
    n_pos_test = max(1, int(len(pos) * test_frac))
    n_neg_test = max(1, int(len(neg) * test_frac))
    test_idx = np.concatenate([pos[:n_pos_test], neg[:n_neg_test]])
    train_idx = np.concatenate([pos[n_pos_test:], neg[n_neg_test:]])
    return train_idx, test_idx


# ── Feature matrix construction ───────────────────────────────────────────────


def _build_feature_names(top_csi: list[str]) -> list[str]:
    names = [
        "planned_duration",
        "log_duration",
        "total_float",
        "pred_count",
        "succ_count",
        "has_planned_cost",
        "log_planned_cost",
        "is_floor_located",
        "floor_ordinal",
        "trade_ontime_rate",
    ]
    for c in top_csi:
        names.append(f"csi_{c}")
    names.append("csi_other")
    for s in _STAGES:
        names.append(f"stage_{s or 'unassigned'}")
    for t in _ACTIVITY_TYPES:
        names.append(f"type_{t.replace(' ', '_').replace('/', '_')}")
    return names


def _task_row(
    task,
    pred_count: int,
    succ_count: int,
    cost: float,
    trade_rate: float,
    top_csi: list[str],
    feat_names: list[str],
) -> list[float]:
    dur = max((task.end_date - task.start_date).days, 0)
    tf = float(task.total_float) if task.total_float is not None else 0.0
    ford = _floor_ordinal(task.activity_code or "")
    csi = _csi(task.activity_code or "")
    stage = task.stage or ""
    atype = _activity_type(task.name)

    row: dict[str, float] = {
        "planned_duration": float(dur),
        "log_duration": math.log1p(dur),
        "total_float": tf,
        "pred_count": float(pred_count),
        "succ_count": float(succ_count),
        "has_planned_cost": 1.0 if cost > 0 else 0.0,
        "log_planned_cost": math.log1p(cost),
        "is_floor_located": 0.0 if ford < 0 else 1.0,
        "floor_ordinal": ford if ford >= 0 else 8.5,  # median floor when absent
        "trade_ontime_rate": trade_rate,
    }
    for c in top_csi:
        row[f"csi_{c}"] = 1.0 if csi == c else 0.0
    row["csi_other"] = 0.0 if csi in top_csi else 1.0
    for s in _STAGES:
        row[f"stage_{s or 'unassigned'}"] = 1.0 if stage == s else 0.0
    for t in _ACTIVITY_TYPES:
        key = f"type_{t.replace(' ', '_').replace('/', '_')}"
        row[key] = 1.0 if atype == t else 0.0
    return [row.get(n, 0.0) for n in feat_names]


# ── Main entry point ──────────────────────────────────────────────────────────


def run_completion_ml(project_id: str) -> dict:
    """Train logistic regression on completed tasks; predict incomplete ones.

    Returns:
        has_data         — bool
        model_quality    — AUC, Brier, class balance, n_train/test, calibration
        feature_list     — features used (with leakage notes)
        feature_importance — list[{name, importance}] descending
        watchlist        — top-20 riskiest near-critical incomplete tasks
        as_of            — ISO date
    """
    from django.db.models import Count, Sum

    from islam.scheduling.models import P6ResourceAssignment, Task, TaskDependency

    today, _ = get_project_data_date(project_id)

    # ── Load tasks ─────────────────────────────────────────────────────────
    completed = list(
        Task.objects.filter(
            project_id=project_id,
            is_non_physical=False,
            status="complete",
        )
        .exclude(actual_end=None)
        .exclude(end_date=None)
        .exclude(start_date=None)
    )
    incomplete = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(status="complete")
        .exclude(start_date=None)
        .exclude(end_date=None)
    )

    if len(completed) < 50:
        return {
            "has_data": False,
            "reason": f"Too few completed tasks ({len(completed)}); need ≥ 50.",
        }

    # ── Labels ────────────────────────────────────────────────────────────
    labels_all = np.array([1 if t.actual_end <= t.end_date else 0 for t in completed], dtype=float)
    n_on_time = int(labels_all.sum())
    n_late = len(labels_all) - n_on_time
    class_balance = {
        "on_time": n_on_time,
        "late": n_late,
        "on_time_pct": round(n_on_time / len(labels_all) * 100, 1),
    }

    # ── Network topology ──────────────────────────────────────────────────
    all_pks = [str(t.pk) for t in completed + incomplete]
    pred_count_map = dict(
        TaskDependency.objects.filter(successor_id__in=all_pks)
        .values("successor_id")
        .annotate(n=Count("id"))
        .values_list("successor_id", "n")
    )
    succ_count_map = dict(
        TaskDependency.objects.filter(predecessor_id__in=all_pks)
        .values("predecessor_id")
        .annotate(n=Count("id"))
        .values_list("predecessor_id", "n")
    )

    # ── Planned cost (from P6 assignments) ────────────────────────────────
    ra_cost = {
        str(row["task_id"]): float(row["total"] or 0)
        for row in P6ResourceAssignment.objects.filter(project_id=project_id, is_pending=False)
        .values("task_id")
        .annotate(total=Sum("planned_cost"))
    }

    # ── Trade on-time rates (LOO for training, global for test/inference) ─
    # Compute global aggregates across ALL completed tasks
    trade_agg: dict[str, list[int]] = {}  # csi -> [n, k_on_time]
    for t, lbl in zip(completed, labels_all):
        c = _csi(t.activity_code or "")
        a = trade_agg.setdefault(c, [0, 0])
        a[0] += 1
        a[1] += int(lbl)

    global_rate = n_on_time / len(labels_all)  # fallback for unknown trades

    def trade_rate_global(csi: str) -> float:
        a = trade_agg.get(csi)
        if not a or a[0] == 0:
            return global_rate
        return a[1] / a[0]

    def trade_rate_loo(csi: str, own_label: int) -> float:
        """Leave-one-out: exclude this task from the trade rate."""
        a = trade_agg.get(csi, [0, 0])
        n_ex = a[0] - 1
        k_ex = a[1] - own_label
        if n_ex <= 0:
            return global_rate
        return k_ex / n_ex

    # ── Top CSI trades by training frequency ─────────────────────────────
    top_csi = sorted(trade_agg, key=lambda c: -trade_agg[c][0])[:_TOP_CSI_N]
    feat_names = _build_feature_names(top_csi)

    # ── Build training matrix ─────────────────────────────────────────────
    x_all = np.array(
        [
            _task_row(
                t,
                pred_count_map.get(str(t.pk), 0),
                succ_count_map.get(str(t.pk), 0),
                ra_cost.get(str(t.pk), 0.0),
                trade_rate_loo(_csi(t.activity_code or ""), int(lbl)),
                top_csi,
                feat_names,
            )
            for t, lbl in zip(completed, labels_all)
        ],
        dtype=float,
    )

    # ── Stratified split ──────────────────────────────────────────────────
    train_idx, test_idx = _stratified_split(len(completed), labels_all)
    x_tr_raw, y_tr = x_all[train_idx], labels_all[train_idx]
    x_te_raw, y_te = x_all[test_idx], labels_all[test_idx]

    # ── Standardise (fit on train) ────────────────────────────────────────
    mu = x_tr_raw.mean(axis=0)
    sigma = x_tr_raw.std(axis=0)
    sigma[sigma == 0] = 1.0  # constant features → no scaling needed

    x_tr_s = (x_tr_raw - mu) / sigma
    x_te_s = (x_te_raw - mu) / sigma

    # ── Train ─────────────────────────────────────────────────────────────
    w, b = _train_logreg(x_tr_s, y_tr, l2=0.05, lr=0.1, n_iter=2000)

    # ── Evaluate on held-out test set ─────────────────────────────────────
    prob_te = _predict_proba(x_te_s, w, b)
    auc = _roc_auc(y_te, prob_te)
    brier = _brier(y_te, prob_te)
    calib = _calibration(y_te, prob_te)

    # ── Retrain on full dataset for inference ─────────────────────────────
    x_full_s = (x_all - mu) / sigma
    w_full, b_full = _train_logreg(x_full_s, labels_all, l2=0.05, lr=0.1, n_iter=2000)

    # ── Feature importance (|w| after standardisation = log-odds per σ) ───
    importance = np.abs(w_full)
    top_feat_idx = np.argsort(-importance)[:15]
    feature_importance = [
        {
            "feature": feat_names[i],
            "importance": round(float(importance[i]), 4),
        }
        for i in top_feat_idx
    ]

    # ── Predict incomplete tasks ──────────────────────────────────────────
    if not incomplete:
        watchlist = []
    else:
        # Build inference matrix (use global trade rates, not LOO)
        x_inc = np.array(
            [
                _task_row(
                    t,
                    pred_count_map.get(str(t.pk), 0),
                    succ_count_map.get(str(t.pk), 0),
                    ra_cost.get(str(t.pk), 0.0),
                    trade_rate_global(_csi(t.activity_code or "")),
                    top_csi,
                    feat_names,
                )
                for t in incomplete
            ],
            dtype=float,
        )
        x_inc_s = (x_inc - mu) / sigma
        prob_inc = _predict_proba(x_inc_s, w_full, b_full)

        # ── Watchlist: riskiest near-critical tasks ───────────────────────
        # Near-critical threshold: ≤ 10 working days of total float (~2 weeks).
        # Tasks with float > 10 have a comfortable schedule buffer and must not
        # be labeled "near-critical" even if the ML model predicts low P(on-time).
        float_near_critical = 10
        risk_items = []
        for t, p in zip(incomplete, prob_inc):
            tf = t.total_float if t.total_float is not None else 9999
            risk_items.append(
                {
                    "task_pk": str(t.pk),
                    "name": t.name,
                    "activity_code": t.activity_code or "",
                    "trade": _CSI_NAMES.get(
                        _csi(t.activity_code or ""), f"Div {_csi(t.activity_code or '')}"
                    ),
                    "csi": _csi(t.activity_code or ""),
                    "stage": t.stage or "unassigned",
                    "total_float": tf,
                    "probability_on_time": round(float(p), 3),
                    "near_critical": tf <= float_near_critical,
                    "baseline_finish": t.end_date.isoformat(),
                }
            )

        # Sort by risk: near-critical first, then ascending probability
        near_crit = sorted(
            [r for r in risk_items if r["near_critical"]],
            key=lambda x: x["probability_on_time"],
        )
        others = sorted(
            [r for r in risk_items if not r["near_critical"]],
            key=lambda x: x["probability_on_time"],
        )
        watchlist = (near_crit + others)[:20]

    # ── AUC quality label ─────────────────────────────────────────────────
    if auc >= 0.75:
        auc_label = "good"
    elif auc >= 0.65:
        auc_label = "fair"
    else:
        auc_label = "limited"

    logger.info(
        "Completion ML — project %s: n_completed=%d AUC=%.3f Brier=%.3f "
        "n_watchlist=%d auc_label=%s",
        project_id,
        len(completed),
        auc,
        brier,
        len(watchlist),
        auc_label,
    )

    return {
        "has_data": True,
        "model_quality": {
            "auc": round(auc, 3),
            "brier": round(brier, 3),
            "auc_label": auc_label,
            "n_train": len(train_idx),
            "n_test": len(test_idx),
            "n_completed_total": len(completed),
            "n_incomplete": len(incomplete),
            "class_balance": class_balance,
            "calibration": calib,
        },
        "feature_list": [
            {
                "name": n,
                "note": (
                    "CPM float — last-calc state, may reflect post-completion progress"
                    if n == "total_float"
                    else ""
                ),
            }
            for n in feat_names
        ],
        "feature_importance": feature_importance,
        "watchlist": watchlist,
        "float_near_critical_wd": float_near_critical,
        "as_of": today.isoformat(),
    }


def predict_all_incomplete(project_id: str) -> list[dict]:
    """Return P(on-time) for every incomplete task, with activity_code for floor mapping.

    Re-runs the same logistic regression pipeline as run_completion_ml() but
    trains on the full completed set (no evaluation split) and returns the
    complete prediction set — not just the top-20 watchlist.

    Returns [] when fewer than 50 completed tasks exist (model too thin).

    Each item: {"task_pk": str, "activity_code": str, "probability_on_time": float}
    """
    from django.db.models import Count, Sum

    from islam.scheduling.models import P6ResourceAssignment, Task, TaskDependency

    completed = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False, status="complete")
        .exclude(actual_end=None)
        .exclude(end_date=None)
        .exclude(start_date=None)
        .only(
            "pk",
            "activity_code",
            "name",
            "start_date",
            "end_date",
            "actual_start",
            "actual_end",
            "stage",
            "status",
            "total_float",
        )
    )
    if len(completed) < 50:
        return []

    incomplete = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(status="complete")
        .exclude(start_date=None)
        .exclude(end_date=None)
        .only(
            "pk",
            "activity_code",
            "name",
            "start_date",
            "end_date",
            "actual_start",
            "actual_end",
            "stage",
            "status",
            "total_float",
        )
    )
    if not incomplete:
        return []

    labels = np.array([1 if t.actual_end <= t.end_date else 0 for t in completed], dtype=float)
    n_on_time = int(labels.sum())
    global_rate = n_on_time / len(labels)

    all_pks = [str(t.pk) for t in completed + incomplete]
    pred_count_map = dict(
        TaskDependency.objects.filter(successor_id__in=all_pks)
        .values("successor_id")
        .annotate(n=Count("id"))
        .values_list("successor_id", "n")
    )
    succ_count_map = dict(
        TaskDependency.objects.filter(predecessor_id__in=all_pks)
        .values("predecessor_id")
        .annotate(n=Count("id"))
        .values_list("predecessor_id", "n")
    )
    ra_cost = {
        str(row["task_id"]): float(row["total"] or 0)
        for row in P6ResourceAssignment.objects.filter(project_id=project_id, is_pending=False)
        .values("task_id")
        .annotate(total=Sum("planned_cost"))
    }

    trade_agg: dict[str, list] = {}
    for t, lbl in zip(completed, labels):
        c = _csi(t.activity_code or "")
        a = trade_agg.setdefault(c, [0, 0])
        a[0] += 1
        a[1] += int(lbl)

    def _rate_loo(csi: str, own_lbl: int) -> float:
        a = trade_agg.get(csi, [0, 0])
        n_ex = a[0] - 1
        k_ex = a[1] - own_lbl
        return k_ex / n_ex if n_ex > 0 else global_rate

    def _rate_global(csi: str) -> float:
        a = trade_agg.get(csi)
        return (a[1] / a[0]) if (a and a[0] > 0) else global_rate

    top_csi = sorted(trade_agg, key=lambda c: -trade_agg[c][0])[:_TOP_CSI_N]
    feat_names = _build_feature_names(top_csi)

    x_train = np.array(
        [
            _task_row(
                t,
                pred_count_map.get(str(t.pk), 0),
                succ_count_map.get(str(t.pk), 0),
                ra_cost.get(str(t.pk), 0.0),
                _rate_loo(_csi(t.activity_code or ""), int(lbl)),
                top_csi,
                feat_names,
            )
            for t, lbl in zip(completed, labels)
        ],
        dtype=float,
    )
    mu = x_train.mean(axis=0)
    sigma = x_train.std(axis=0)
    sigma[sigma == 0] = 1.0
    w, b = _train_logreg((x_train - mu) / sigma, labels, l2=0.05, lr=0.1, n_iter=2000)

    x_inc = np.array(
        [
            _task_row(
                t,
                pred_count_map.get(str(t.pk), 0),
                succ_count_map.get(str(t.pk), 0),
                ra_cost.get(str(t.pk), 0.0),
                _rate_global(_csi(t.activity_code or "")),
                top_csi,
                feat_names,
            )
            for t in incomplete
        ],
        dtype=float,
    )
    probs = _predict_proba((x_inc - mu) / sigma, w, b)

    return [
        {
            "task_pk": str(t.pk),
            "activity_code": t.activity_code or "",
            "probability_on_time": round(float(p), 3),
        }
        for t, p in zip(incomplete, probs)
    ]
