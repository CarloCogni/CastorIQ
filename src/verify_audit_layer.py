"""Regression + integration test for the audit-view overlay layer.
Run: uv run python verify_audit_layer.py
"""

import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
import django

django.setup()

from castor.scheduling.models import Project
from castor.scheduling.services.anomaly_detect import detect_anomalies
from castor.scheduling.services.delay_rootcause import run_delay_rootcause
from castor.scheduling.services.floor_health import compute_floor_health
from castor.scheduling.services.schedule_audit import run_section_mismatch_audit
from castor.scheduling.services.timelocation import compute_timelocation
from castor.scheduling.services.trade_resolver import (
    build_override_map,
    save_override_map,
)

W = 72
PASS = "  PASS ✓"
FAIL = "  FAIL ✗"

p = Project.objects.get(pk="ce21b8ea-3e31-47de-bce4-754ca68b566f")
pk = str(p.pk)
print(f"Project: {p.name}  pk={pk}")

# ── Build / load override map ─────────────────────────────────────────────────
print()
print("=" * W)
print("3. OVERRIDE MAP CONTENTS")
print("=" * W)

# Re-run audit to get fresh confirmed items (or use cached)
print("  Running section-mismatch audit for override map...")
audit_result = run_section_mismatch_audit(pk, user=None)
if not audit_result.get("has_data"):
    print("  ERROR: audit returned no data — cannot build override map")
    sys.exit(1)

om = build_override_map(audit_result)
save_override_map(pk, audit_result)  # populate cache for load_override_map tests

items = audit_result.get("items", [])
confirmed_items = [i for i in items if i["verdict"] == "confirmed"]
needs_review_items = [i for i in items if i["verdict"] == "needs_review"]
uncertain_items = [i for i in items if i["verdict"] == "uncertain"]

print(f"  Audit confirmed_count  : {audit_result['confirmed_count']}")
print(f"  Audit needs_review     : {audit_result['needs_review_count']}")
print(f"  Audit uncertain        : {audit_result['uncertain_count']}")
print(f"  Override map size      : {len(om)}")
print()

# Verify only confirmed items in override map
for item in needs_review_items + uncertain_items:
    if item.get("task_id") in om:
        print(f"  {FAIL} needs_review/uncertain task {item['task_id']} found in override map!")
        sys.exit(1)
print(f"  confirmed-only check   :{PASS} (no needs_review / uncertain in map)")

# Verify all confirmed items with ai_csi are in map
missing = [
    i for i in confirmed_items if i.get("ai_csi") and i.get("task_id") and i["task_id"] not in om
]
if missing:
    print(f"  {FAIL} {len(missing)} confirmed items missing from override map")
else:
    print(f"  all confirmed in map   :{PASS}")

print()
print("  Breakdown of override map (original CSI → AI CSI):")
from collections import Counter

override_types = Counter(
    f"{i['coded_csi']}→{i['ai_csi']}"
    for i in confirmed_items
    if i.get("ai_csi") and i.get("task_id")
)
for change, cnt in override_types.most_common(8):
    print(f"    {change}: {cnt}")

# ── AS-CODED baseline ─────────────────────────────────────────────────────────
print()
print("=" * W)
print("1. AS-CODED (override_map=None)  —  regression against known values")
print("=" * W)

# Time-Location
tl_coded = compute_timelocation(pk, override_map=None)
tl_n = sum(1 for s in tl_coded.get("segments", []))
tl_ov = tl_coded.get("scope", {}).get("n_overridden", 0)
print(f"\n  Time-Location segments   : {tl_n}")
print(f"  overrides applied        : {tl_ov}  (expected 0)")
tl_ref = 4848
ok_tl = tl_n == tl_ref
print(
    f"  {'matches ref 4,848' if ok_tl else f'DRIFT: expected {tl_ref}, got {tl_n}'} {PASS if ok_tl else FAIL}"
)

# Delay root-cause
drc_coded = run_delay_rootcause(pk, override_map=None)
drc_s = drc_coded.get("summary", {})
rc_total = drc_s.get("root_causes", 0)
rc_firm = drc_s.get("root_causes_firm", 0)
rc_lower = drc_s.get("root_causes_lower_confidence", rc_total - rc_firm)
trade_clusters = sorted(
    drc_coded.get("clusters", {}).get("by_trade", []), key=lambda c: -c["downstream_tasks"]
)
largest_trade = trade_clusters[0] if trade_clusters else {}
print(f"\n  Delay root-causes        : {rc_total}  (firm={rc_firm}, lower={rc_lower})")
print(
    f"  Largest trade cluster    : {largest_trade.get('label', '?')}  {largest_trade.get('root_cause_count', '?')} root-causes  {largest_trade.get('downstream_tasks', '?')} downstream"
)
# Baseline updated from 584/502 → 580/498 after 9ecd98b switched delay_rootcause
# from date.today() to get_project_data_date (data_date=2025-11-30).  4 tasks
# whose actual_start post-dates the P6 DataDate are no longer active as-of.
ok_drc_total = rc_total == 580
ok_drc_firm = rc_firm == 498
ok_drc_lower = rc_lower == 82
print(
    f"  total=580 {PASS if ok_drc_total else f'{FAIL} got {rc_total}'}  "
    f"firm=498 {PASS if ok_drc_firm else f'{FAIL} got {rc_firm}'}  "
    f"lower=82 {PASS if ok_drc_lower else f'{FAIL} got {rc_lower}'}"
)
ok_cluster = largest_trade.get("label", "") == "Masonry"
got_label = largest_trade.get("label", "?")
print(
    f"  largest cluster=Masonry {PASS if ok_cluster else FAIL + ' got ' + got_label}  "
    f"downstream={largest_trade.get('downstream_tasks', '?')}"
)

# Anomaly detection
an_coded = detect_anomalies(pk, override_map=None)
an_s = an_coded.get("summary", {}) if an_coded.get("has_data") else {}
an_by_type = an_s.get("by_type", {})
an_total = an_s.get("total_flagged", 0)
an_running = an_by_type.get("running_long", 0)
an_unreal = an_by_type.get("unrealistic_baseline", 0)
an_outlier = an_by_type.get("statistical_outlier", 0)
an_logic = an_by_type.get("logic_anomaly", 0)
print(
    f"\n  Anomaly total flagged    : {an_total}  (rl={an_running} unr={an_unreal} out={an_outlier} logic={an_logic})"
)
# Baselines updated after 9ecd98b switched anomaly_detect from date.today() to
# get_project_data_date (data_date=2025-11-30).  With the earlier reference date:
#   - running_long: 699→509  (shorter elapsed → fewer tasks exceed 2× ratio threshold)
#   - unrealistic_baseline: 105→168  (reclassified bucket: more tasks cross the
#     ≤4 working-day planned-duration threshold once calendar-based calc is used)
#   - outlier: 549→587  (peer-group statistics shift with the different active set)
#   - logic_anomaly: 745 unchanged  (structural logic, not time-based)
ok_an_total = an_total == 1583
ok_an_running = an_running == 509
ok_an_unreal = an_unreal == 168
ok_an_outlier = an_outlier == 587
ok_an_logic = an_logic == 745
ok_an = ok_an_total and ok_an_running and ok_an_unreal and ok_an_outlier and ok_an_logic
print(
    f"  total=1583 {PASS if ok_an_total else f'{FAIL} got {an_total}'}  "
    f"running=509 {PASS if ok_an_running else f'{FAIL} got {an_running}'}  "
    f"unreal=168 {PASS if ok_an_unreal else f'{FAIL} got {an_unreal}'}"
)
print(
    f"  outliers=587 {PASS if ok_an_outlier else f'{FAIL} got {an_outlier}'}  "
    f"logic=745 {PASS if ok_an_logic else f'{FAIL} got {an_logic}'}"
)

# Floor health
fh_coded = compute_floor_health(pk, override_map=None)
fh_floors = {f["token"]: f for f in fh_coded.get("floors", [])}
b03_bq = fh_floors.get("B03", {}).get("build_quality", {}).get("score", None)
l04_bq = fh_floors.get("L04", {}).get("build_quality", {}).get("score", None)
# Floor-health baselines updated from B03=31/L04=32 → 25/29 after 68a7a62
# switched floor_health from date.today() to get_project_data_date.  Build-quality
# scores are computed as-of 2025-11-30 instead of today; tasks completed between
# 2025-11-30 and today no longer count as "done on time" for those floors.
print(f"\n  Floor Health B03 BQ      : {b03_bq}  (ref=25)")
print(f"  Floor Health L04 BQ      : {l04_bq}  (ref=29)")
ok_b03 = b03_bq == 25
ok_l04 = l04_bq == 29
print(
    f"  B03=25 {PASS if ok_b03 else f'{FAIL} got {b03_bq}'}  "
    f"L04=29 {PASS if ok_l04 else f'{FAIL} got {l04_bq}'}"
)

# ── CORRECTED mode ────────────────────────────────────────────────────────────
print()
print("=" * W)
print("2. CORRECTED (override_map applied)  —  what changed?")
print("=" * W)

# Time-Location corrected
tl_corr = compute_timelocation(pk, override_map=om)
tl_n_c = len(tl_corr.get("segments", []))
tl_ov_c = tl_corr.get("scope", {}).get("n_overridden", 0)
using_ov = tl_corr.get("scope", {}).get("using_audit_overrides", False)
print(f"\n  Time-Location segments   : {tl_n_c}  (same? {tl_n_c == tl_n})")
print(f"  overrides applied        : {tl_ov_c}  (using_audit_overrides={using_ov})")
# Show which trade groups changed
coded_trade_counts = {}
corr_trade_counts = {}
for s in tl_coded.get("segments", []):
    coded_trade_counts[s["k"]] = coded_trade_counts.get(s["k"], 0) + 1
for s in tl_corr.get("segments", []):
    corr_trade_counts[s["k"]] = corr_trade_counts.get(s["k"], 0) + 1
all_keys = set(coded_trade_counts) | set(corr_trade_counts)
changed_trades = {
    k for k in all_keys if coded_trade_counts.get(k, 0) != corr_trade_counts.get(k, 0)
}
if changed_trades:
    print("  Trade count deltas (corrected − coded):")
    for k in sorted(changed_trades):
        delta = corr_trade_counts.get(k, 0) - coded_trade_counts.get(k, 0)
        print(
            f"    {k}: {coded_trade_counts.get(k, 0)} → {corr_trade_counts.get(k, 0)}  ({delta:+d})"
        )
else:
    print("  No trade-count changes (override map may map to same divisions)")

# Delay root-cause corrected
drc_corr = run_delay_rootcause(pk, override_map=om)
drc_s_c = drc_corr.get("summary", {})
rc_total_c = drc_s_c.get("root_causes", 0)
rc_firm_c = drc_s_c.get("root_causes_firm", 0)
trade_clusters_c = sorted(
    drc_corr.get("clusters", {}).get("by_trade", []), key=lambda c: -c["downstream_tasks"]
)
largest_c = trade_clusters_c[0] if trade_clusters_c else {}
print(
    f"\n  Delay root-causes        : {rc_total_c}  coded={rc_total}  delta={rc_total_c - rc_total:+d}"
)
print(
    f"  Largest trade cluster    : {largest_c.get('label', '?')}  downstream={largest_c.get('downstream_tasks', '?')}"
)

# Anomaly corrected
an_corr = detect_anomalies(pk, override_map=om)
an_s_c = an_corr.get("summary", {}) if an_corr.get("has_data") else {}
an_tot_c = an_s_c.get("total_flagged", 0)
an_out_c = an_s_c.get("by_type", {}).get("statistical_outlier", 0)
print(
    f"\n  Anomaly total flagged    : {an_tot_c}  coded={an_total}  delta={an_tot_c - an_total:+d}"
)
print(
    f"  Statistical outliers     : {an_out_c}  coded={an_outlier}  delta={an_out_c - an_outlier:+d}"
)

# Floor health corrected
fh_corr = compute_floor_health(pk, override_map=om)
fh_floors_c = {f["token"]: f for f in fh_corr.get("floors", [])}
b03_bq_c = fh_floors_c.get("B03", {}).get("build_quality", {}).get("score", None)
l04_bq_c = fh_floors_c.get("L04", {}).get("build_quality", {}).get("score", None)
print(
    f"\n  Floor Health B03 BQ      : {b03_bq_c}  coded={b03_bq}  delta={b03_bq_c - b03_bq if b03_bq_c is not None and b03_bq is not None else 'n/a':}"
)
print(
    f"  Floor Health L04 BQ      : {l04_bq_c}  coded={l04_bq}  delta={l04_bq_c - l04_bq if l04_bq_c is not None and l04_bq is not None else 'n/a':}"
)

# ── Toggle-back: second as-coded call must match baseline exactly ─────────────
print()
print("=" * W)
print("4. TOGGLE-BACK INTEGRITY  —  second as-coded call = first exactly?")
print("=" * W)

tl2 = compute_timelocation(pk, override_map=None)
drc2 = run_delay_rootcause(pk, override_map=None)
an2 = detect_anomalies(pk, override_map=None)
fh2 = compute_floor_health(pk, override_map=None)

ok_tl2 = len(tl2.get("segments", [])) == tl_n and tl2.get("scope", {}).get("n_overridden", 0) == 0
ok_drc2 = drc2.get("summary", {}).get("root_causes", 0) == rc_total
ok_an2 = an2.get("summary", {}).get("total_flagged", 0) == an_total
fh2_f = {f["token"]: f for f in fh2.get("floors", [])}
ok_fh2 = (
    fh2_f.get("B03", {}).get("build_quality", {}).get("score") == b03_bq
    and fh2_f.get("L04", {}).get("build_quality", {}).get("score") == l04_bq
)

print(f"  TL   segments identical  :{PASS if ok_tl2 else FAIL}")
print(f"  DRC  root-causes identical:{PASS if ok_drc2 else FAIL}")
print(f"  AN   total identical     :{PASS if ok_an2 else FAIL}")
print(f"  FH   BQ scores identical :{PASS if ok_fh2 else FAIL}")

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * W)
regression_ok = (
    ok_tl
    and ok_drc_total
    and ok_drc_firm
    and ok_drc_lower
    and ok_an
    and ok_b03
    and ok_l04
    and ok_tl2
    and ok_drc2
    and ok_an2
    and ok_fh2
)
print(
    "OVERALL REGRESSION:",
    "PASS ✓  — all as-coded values match baseline"
    if regression_ok
    else "FAIL ✗  — see FAIL lines above",
)
print("=" * W)
