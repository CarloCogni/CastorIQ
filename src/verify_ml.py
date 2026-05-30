"""Verification script for completion_ml leakage test. Run once, then delete."""

import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

import numpy as np
from django.db.models import Count, Sum

from islam.scheduling.models import P6ResourceAssignment, Task, TaskDependency
from islam.scheduling.services.completion_ml import (
    _brier,
    _build_feature_names,
    _csi,
    _predict_proba,
    _roc_auc,
    _stratified_split,
    _task_row,
    _train_logreg,
    run_completion_ml,
)

pid = "ce21b8ea-3e31-47de-bce4-754ca68b566f"
_TOP_CSI_N = 8
_STAGES = ("substructure", "structure", "envelope", "mep", "finishes", "external", "")
_ACTIVITY_TYPES = ("Construction", "Submittal", "Approval", "Procurement", "Testing / Inspection")

# ── 1. Full feature importance from production model ─────────────────────────
print("=== 1. FEATURE IMPORTANCE (standardized |w|, all features) ===")
r = run_completion_ml(pid)
for i, f in enumerate(r["feature_importance"]):
    arrow = " <-- total_float" if f["feature"] == "total_float" else ""
    print(f"  {i + 1:>2}. {f['feature']:<35} {f['importance']:.4f}{arrow}")
mq = r["model_quality"]
print(f"\nProduction model: AUC={mq['auc']}  Brier={mq['brier']}\n")

# ── Rebuild raw data ──────────────────────────────────────────────────────────
completed = list(
    Task.objects.filter(project_id=pid, is_non_physical=False, status="complete")
    .exclude(actual_end=None)
    .exclude(end_date=None)
    .exclude(start_date=None)
)
labels_all = np.array([1 if t.actual_end <= t.end_date else 0 for t in completed], dtype=float)
n_on_time = int(labels_all.sum())
global_rate = n_on_time / len(labels_all)

all_pks = [str(t.pk) for t in completed]
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
    for row in P6ResourceAssignment.objects.filter(project_id=pid, is_pending=False)
    .values("task_id")
    .annotate(total=Sum("planned_cost"))
}

trade_agg = {}
for t, lbl in zip(completed, labels_all):
    c = _csi(t.activity_code or "")
    a = trade_agg.setdefault(c, [0, 0])
    a[0] += 1
    a[1] += int(lbl)
top_csi = sorted(trade_agg, key=lambda c: -trade_agg[c][0])[:_TOP_CSI_N]
feat_names = _build_feature_names(top_csi)


def trade_rate_loo(csi, own_label):
    a = trade_agg.get(csi, [0, 0])
    n_ex = a[0] - 1
    k_ex = a[1] - int(own_label)
    return k_ex / n_ex if n_ex > 0 else global_rate


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

# SAME seed=42 split as production model
train_idx, test_idx = _stratified_split(len(completed), labels_all, seed=42)
y_tr = labels_all[train_idx]
y_te = labels_all[test_idx]

# ── 2a. WITH total_float ──────────────────────────────────────────────────────
x_tr_raw = x_all[train_idx]
x_te_raw = x_all[test_idx]
mu_a = x_tr_raw.mean(axis=0)
sig_a = x_tr_raw.std(axis=0)
sig_a[sig_a == 0] = 1.0
x_tr_a = (x_tr_raw - mu_a) / sig_a
x_te_a = (x_te_raw - mu_a) / sig_a
w_a, b_a = _train_logreg(x_tr_a, y_tr, l2=0.05, lr=0.1, n_iter=2000)
p_te_a = _predict_proba(x_te_a, w_a, b_a)
auc_a = _roc_auc(y_te, p_te_a)
brier_a = _brier(y_te, p_te_a)

# ── 2b. WITHOUT total_float ───────────────────────────────────────────────────
tf_idx = feat_names.index("total_float")
keep = [i for i in range(len(feat_names)) if i != tf_idx]
feat_names_nf = [feat_names[i] for i in keep]
x_all_nf = x_all[:, keep]
x_tr_nf = x_all_nf[train_idx]
x_te_nf = x_all_nf[test_idx]
mu_b = x_tr_nf.mean(axis=0)
sig_b = x_tr_nf.std(axis=0)
sig_b[sig_b == 0] = 1.0
x_tr_nf_s = (x_tr_nf - mu_b) / sig_b
x_te_nf_s = (x_te_nf - mu_b) / sig_b
w_b, b_b = _train_logreg(x_tr_nf_s, y_tr, l2=0.05, lr=0.1, n_iter=2000)
p_te_b = _predict_proba(x_te_nf_s, w_b, b_b)
auc_b = _roc_auc(y_te, p_te_b)
brier_b = _brier(y_te, p_te_b)

print("=== 2. ABLATION: WITH vs WITHOUT total_float (same seed=42 split) ===")
print(f"  (a) WITH  total_float:   AUC={auc_a:.4f}  Brier={brier_a:.4f}")
print(f"  (b) WITHOUT total_float: AUC={auc_b:.4f}  Brier={brier_b:.4f}")
print(f"  Delta AUC:   {auc_a - auc_b:+.4f}")
print(f"  Delta Brier: {brier_a - brier_b:+.4f}")
delta = auc_a - auc_b
if abs(delta) < 0.03:
    print(f"  VERDICT: |delta| = {abs(delta):.4f} < 0.03 threshold.")
    print("  Float contribution is MINOR. AUC=0.744 is not materially inflated.")
    print("  No change needed to production model.")
else:
    print(f"  VERDICT: |delta| = {abs(delta):.4f} >= 0.03 threshold.")
    print(f"  Float leakage is material. Honest no-float AUC = {auc_b:.3f}")

# ── 3. LOO verification: worked example ──────────────────────────────────────
print("\n=== 3. LOO TRADE RATE VERIFICATION ===")
# Pick first task with a trade that has >=5 examples
for t, lbl in zip(completed, labels_all):
    csi = _csi(t.activity_code or "")
    a = trade_agg.get(csi, [0, 0])
    if a[0] >= 5:
        n_total, k_total = a
        rate_global = k_total / n_total
        n_loo = n_total - 1
        k_loo = k_total - int(lbl)
        rate_loo = k_loo / n_loo
        print(f'  Task:          "{t.name[:55]}"')
        print(f"  Trade (CSI):   {csi}  (n={n_total} completed tasks in trade)")
        print(f"  Own outcome:   {'ON-TIME (y=1)' if lbl == 1 else 'LATE (y=0)'}")
        print(f"  Global rate:   {k_total}/{n_total} = {rate_global:.4f}")
        print(
            f"  LOO rate:      ({k_total}-{int(lbl)})/({n_total}-1) = {k_loo}/{n_loo} = {rate_loo:.4f}"
        )
        print(f"  Delta:         {rate_loo - rate_global:+.4f}  (own outcome excluded ✓)")
        break

# ── 4. Feature contributions for watchlist[0] ────────────────────────────────
print("\n=== 4. FEATURE CONTRIBUTION — watchlist[0] ('Install AV accessories') ===")
watchlist_task_pk = r["watchlist"][0]["task_pk"]
inc_tasks = list(
    Task.objects.filter(project_id=pid, is_non_physical=False)
    .exclude(status="complete")
    .exclude(start_date=None)
    .exclude(end_date=None)
)
target = next((t for t in inc_tasks if str(t.pk) == watchlist_task_pk), None)
if target:
    from islam.scheduling.services.completion_ml import _CSI_NAMES

    # Get its feature vector using global rate (inference mode)
    def trade_rate_global(csi):
        a = trade_agg.get(csi, [0, 0])
        return a[1] / a[0] if a[0] > 0 else global_rate

    row = _task_row(
        target,
        pred_count_map.get(str(target.pk), 0),
        succ_count_map.get(str(target.pk), 0),
        ra_cost.get(str(target.pk), 0.0),
        trade_rate_global(_csi(target.activity_code or "")),
        top_csi,
        feat_names,
    )
    row_arr = np.array(row)

    # Standardise with the full-model mu/sigma
    x_all_full_s = (x_all - mu_a) / sig_a  # full model params
    # Refit full model
    w_full, b_full = _train_logreg(x_all_full_s, labels_all, l2=0.05, lr=0.1, n_iter=2000)

    # Standardise target row
    row_std = (row_arr - mu_a) / sig_a
    prob = float(1 / (1 + np.exp(-np.clip(row_std @ w_full + b_full, -500, 500))))
    print(f"  Task:  {target.name[:60]}")
    print(f"  P(on-time) = {prob:.3f}")
    print(f"  total_float = {target.total_float}")
    print(f"  activity_code = {target.activity_code}")
    print(
        f"  trade = {_CSI_NAMES.get(_csi(target.activity_code or ''), 'Div ' + _csi(target.activity_code or ''))}"
    )
    print()

    # Per-feature log-odds contributions = w_i * x_std_i
    contributions = w_full * row_std
    feat_contrib = sorted(zip(feat_names, contributions), key=lambda x: abs(x[1]), reverse=True)
    print("  Top 5 log-odds contributions (w_i * x_std_i):")
    for fname, contrib in feat_contrib[:5]:
        raw_val = row_arr[feat_names.index(fname)]
        tf_flag = " <-- total_float" if fname == "total_float" else ""
        print(f"    {fname:<35} raw={raw_val:>8.2f}  contribution={contrib:+.4f}{tf_flag}")
    print()
    # Compare WITH vs WITHOUT float for this task
    row_nf = np.array([row_arr[i] for i in keep])
    row_nf_std = (row_nf - mu_b) / sig_b
    prob_nf = float(1 / (1 + np.exp(-np.clip(row_nf_std @ w_b + b_b, -500, 500))))
    print(f"  P(on-time) WITH    total_float: {prob:.3f}")
    print(f"  P(on-time) WITHOUT total_float: {prob_nf:.3f}")
    print(
        f"  Delta:  {prob - prob_nf:+.3f}  {'(float is driving low prob)' if abs(prob - prob_nf) > 0.05 else '(float is NOT the main driver)'}"
    )
