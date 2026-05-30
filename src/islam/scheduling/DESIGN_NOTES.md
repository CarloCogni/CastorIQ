# Scheduling Analytics — Design Notes

Audience: future developers extending or productizing this module.
Written after building and verifying all 7 analytics features against the
IBS/Castor project dataset (SPI 0.57, ~8,800 tasks, P6 XML import).

---

## Project-specific vs reusable

### Engines that are fully project-agnostic

These algorithms contain no project-specific knowledge.
Drop them on any P6-derived dataset and they work:

| Engine | Entry point | Why it's universal |
|--------|-------------|-------------------|
| Root-cause propagation forest | `delay_rootcause.py · run_delay_rootcause()` | Builds the driving graph from CPM dates already in the DB; no project knowledge needed |
| Completion-ML pipeline | `completion_ml.py · run_completion_ml()` | Retrains from scratch on each project's completed tasks; includes LOO encoding and leak-free test split |
| DCMA 14-point assessment | `dcma_check.py · run_dcma_check()` | All pass/warn/fail thresholds are verbatim from DCMA's published standard (industry constants, not tuned) |
| Monte Carlo forward-pass | `monte_carlo.py · compute_monte_carlo()` | Draws duration distributions from the project's own completed tasks |
| Robust (MAD) outlier engine | `anomaly_detect.py · _detect_cross_sectional()` | Computes peer median + MAD from each project's own peer groups; no external baseline |
| Cash flow distribution | `cashflow.py · compute_cashflow()` | Aggregates from the project's own resource assignments |
| Earned Value / Earned Schedule | `evm.py · compute_evm()` | Pure formula from the project's own PV/AC |

### Values that auto-adapt per project (no action required)

These are computed dynamically from each project's own data and automatically
correct themselves when the project data changes:

- **Robust z-scores** — computed relative to each project's own peer-group median and MAD
- **`trade_ontime_rate`** — leave-one-out encoding from the project's own completed tasks
- **ML model weights** — retrained each call from the project's own completed tasks
- **Monte Carlo duration distributions** — fit to the project's own actual/planned ratio distributions
- **DCMA pass/warn/fail thresholds** — DCMA industry standards (not tuned)
- **Root-cause confidence labelling** — derived from the structure of the project's own network

### Values currently hardcoded and tuned to THIS project

These are judgment calls that were set by inspecting the IBS/Castor dataset.
They are **not wrong in general**, but they may need adjustment on another project.
Each entry: what it controls, current value, file + line, and the risk of getting it wrong.

---

#### 1. Peer-group duration cap
**File:** `anomaly_detect.py:51`
**Current value:** `_MAX_PEER_DURATION_DAYS = 365`

Excludes tasks with planned_duration > 365 days from cross-sectional outlier peer groups.
**Why it's here:** Without this cap, project-level summary/hammock tasks (e.g. "Project
Duration" at 1,258 days) contaminate trade peer groups and produce z-scores of 800+.
**The real fix (not done):** filter by task type (LOE, Summary, Hammock) from the P6 import,
not by a day cap. A legitimate 400-day structural package on a large project would be
wrongly excluded by this rule.
**Risk if wrong:** set too low → legitimate long tasks flagged as outliers; set too high →
summary tasks re-contaminate peer groups.

Also applies to the lower bound:
`_MIN_PEER_DURATION_DAYS = 3` (`anomaly_detect.py:50`) — excludes 1-2d stub tasks from
peer groups for the same reason.

---

#### 2. Stall — "running long" overrun threshold
**File:** `anomaly_detect.py:49`
**Current value:** `_STALL_MIN_RATIO = 2.0`

A started+incomplete task is "running long" only if elapsed ≥ 2× its planned duration.
**Why it's here:** On this project (SPI 0.57), virtually all started tasks exceed their
planned duration. Raising from 1.0 to 2.0 barely changed the count (811 → 804). The value
is more about framing than filtering on this dataset.
**Risk if wrong:** set too low (1.0) → flags most of the in-progress workforce on a delayed
project; set too high (5.0+) → misses genuinely blocked tasks that are only 3-4× overrun.

---

#### 3. Stall — "unrealistic baseline" duration threshold
**File:** `anomaly_detect.py:86`
**Current value:** `_UNREALISTIC_DURATION_MAX_DAYS = 4`

Construction-trade tasks with planned_duration ≤ 4 days are labelled "unrealistic baseline
duration" rather than "running long."
**Why it's here:** On this project, all 105 affected tasks (1-4d construction CSI) showed
192-335× overrun ratios driven by the short planned duration, not by being blocked. Verified
by checking that all had real cost data and construction CSI codes. The 3-4d "gray zone"
was empirically identical to the 1-2d group.
**Risk if wrong:** On a project with genuine 1-3d activities (small-scope MEP commissioning,
QA inspections), this would mislabel execution problems as data quality. Should be verified
per project, and ideally paired with a task-type check (milestone vs work activity).

---

#### 4. FS-violation severity floor
**File:** `anomaly_detect.py:52`
**Current value:** `_FS_MIN_VIOLATION_DAYS = 90`

Only flags FS-link violations where the planned start date precedes the predecessor's
planned finish by 90+ calendar days.
**Why it's here:** Without a floor, 2,465 tasks (28%) were flagged on this project — every
re-scheduled task whose predecessor's planned finish was pushed forward without updating the
successor's planned start. At 90 days the count is 744, which is still high but only flags
the egregious cases.
**Risk if wrong:** set too low → re-detects the known delay as anomalies (noise); set too
high → misses real schedule sequencing errors.

---

#### 5. Robust z-score threshold for statistical outliers
**File:** `anomaly_detect.py:48`
**Current value:** `_OUTLIER_Z = 5.0`

**Original value:** 3.0 (standard 3σ rule for Gaussian data).
**Why it was raised:** At z ≥ 3.0, 1,381 tasks (15.8%) were flagged on this project. The
peer groups within construction trade codes have heavy-tailed distributions, so the
3σ rule over-flags. Raising to 5σ brought the count to 534 (6.1%) and the remaining
outliers confirmed as genuinely unusual. The raised value is a pragmatic adjustment for
a specific heavily-delayed schedule, not a universal improvement.
**Risk if wrong:** set too high (7+) → misses real but moderate outliers; set too low (3.0)
→ floods the output on any complex or delayed project.

---

#### 6. Delay root-cause default threshold
**File:** `delay_rootcause.py:145`
**Current value:** `delay_threshold_days: int = 0`

Tasks with finish_variance > 0 (any delay) are included in root-cause analysis.
**Why it's here:** The default was left open to include everything; callers pass a
positive value to narrow to "delayed by at least N days."
**Risk if wrong:** set to 0 on a project where all tasks have a 2-day float buffer → flags
everything. A threshold of 5-10 working days is more meaningful operationally.

---

#### 7. Driving-predecessor binding window
**File:** `delay_rootcause.py:44`
**Current value:** `BINDING_TOLERANCE = 7` (calendar days)

A predecessor is considered "binding" (driving the task's early_start) if its projected
finish date is within 7 days of the task's early_start.
**Why it's here:** 7 days was chosen to account for calendar/working-day rounding in P6.
**Risk if wrong:** set too tight (1-2 days) → misses real driving relationships with small
lag; set too wide (30+) → incorrectly classifies non-driving predecessors as driving.

---

#### 8. Constraint-gap threshold for hard-date classification
**File:** `delay_rootcause.py:48`
**Current value:** `CONSTRAINT_GAP = 14` (calendar days)

If all predecessor binding dates are more than 14 days before the task's early_start, the
task is classified as constraint-driven (a hard date is pulling the start forward) rather
than logic-driven.
**Why it's here:** 14 days (2 working weeks) was tuned to distinguish genuine date
constraints from predecessor-driven float. Smaller → more tasks classified as
constraint-driven; larger → fewer.
**Risk if wrong:** set too tight → under-detects constraint-driven delays; set too wide →
classifies float as a constraint.

---

#### 9. Completion-ML near-critical watchlist window
**File:** `completion_ml.py:449`
**Current value:** `float_near_critical = 44` (working days, ~2 months)

Incomplete tasks with total_float ≤ 44 working days appear in the at-risk watchlist.
**Why it's here:** Chosen as "within 2 months of criticality" for a project with a long
runway. On a short project (3-6 month remaining duration), 44 wd might encompass the
entire incomplete workforce.
**Risk if wrong:** set too wide → watchlist loses meaning (most tasks qualify); set too
narrow → critical near-miss tasks are excluded.

---

## Parsers that are coding-scheme specific

These regular expressions and conventions are **specific to this project's P6 activity-code
format** (`LLGG-TTradeNNNN.VV`). They will silently produce wrong output on a project with
a different code format — no error, just missing data.

### Floor / zone extraction
```python
# completion_ml.py:34 and timelocation.py:30
_FLOOR_RE = re.compile(r"^(B0?[1-3]|L\d{1,2}|R0?[1-2])", re.IGNORECASE)
```
Matches codes starting with `B01–B03` (basements), `L00–L19` (levels), `R01–R02` (roofs).
Used to assign tasks to floors for the Time-Location chart and as an ML feature.
**Breaks on:** any project that encodes location differently in activity codes, or
that uses a zone/area prefix instead of a level prefix, or that stores location in a
separate field rather than the code.

### CSI trade extraction
```python
# completion_ml.py:35, delay_rootcause.py:92, timelocation.py:117, anomaly_detect.py:26
_CSI_RE = re.compile(r"-[A-Z]*(\d{2})\d{4}")
```
Extracts a two-digit CSI division from the format `...-[letters][DD][NNNN]...`.
Used for peer grouping in cross-sectional outlier detection, trade-level on-time rates,
root-cause clustering, and flowline trade grouping.
**Breaks on:** projects where trade is encoded in a different position, uses a different
number of digits, or is not encoded in the activity code at all (stored in a WBS code or
resource field instead).

### Consequence
On a project with a different coding scheme, the floor-ordinal ML feature returns −1
("unlocated") for all tasks, the Time-Location chart shows zero tasks, and cross-sectional
outlier peer groups collapse to single-member groups (no stats possible). None of these
failures raise an exception — they degrade silently.

---

## Productization roadmap

**Status: not started.**

Agreed principle with stakeholder: anything that needs to change per project to produce
correct or useful output must be a **user-editable input**, never a code edit. A developer
shouldn't be required for each new project onboarding.

### Bucket 1 — Sensitivity / judgment thresholds → Settings panel

The 9 hardcoded values above belong in a per-project Settings panel.
Each input needs:
- **Label** (plain English)
- **Default** (the current hardcoded value)
- **Bounded range** (min/max) to prevent absurd values that silently produce garbage
- **One-line explanation** of what it does and what changes if you raise/lower it
- **Reset-to-default** button

Example rows:

| Setting | Default | Range | One-liner |
|---------|---------|-------|-----------|
| Outlier z-threshold | 5.0 | [3.0, 10.0] | Lower = more outliers flagged; raise if too many false positives |
| Stall overrun ratio | 2.0 | [1.2, 10.0] | Flag tasks where elapsed ≥ N× planned duration |
| Unrealistic baseline ≤ | 4d | [1, 14] | Construction tasks shorter than this are treated as mis-planned, not blocked |
| FS violation floor | 90d | [7, 365] | Ignore planned-date FS conflicts smaller than this |
| Driving-predecessor bind window | 7d | [1, 30] | How close a predecessor's finish must be to a task's early_start to count as driving |
| Constraint-gap threshold | 14d | [3, 60] | If all predecessors finish 14+ days early, treat task as date-constrained |
| Root-cause delay threshold | 0d | [0, 30] | Minimum finish variance to include a task in root-cause analysis |
| Near-critical float window | 44 wd | [5, 130] | Tasks within this many working days of criticality appear in the at-risk watchlist |

Live recompute is feasible: all data is already client-side after the initial page load
(the anomaly, root-cause, and ML endpoints are called once on page load). A settings change
can trigger a fresh fetch.

**Guardrail requirement:** an absurd value must not silently produce a misleading output.
For example, z = 10 hides nearly everything and looks clean rather than wrong; overrun = 1.0
flags the entire in-progress workforce on a delayed project. Each field needs a bounded
input and a live count readout ("currently flagging N tasks") so the user sees the effect
immediately.

### Bucket 2 — Auto-computed values → keep internal, do not expose

These compute correctly from the project's own data and would only confuse users if exposed:
robust peer medians/MADs, ML model weights, Monte Carlo duration distributions, DCMA
industry-standard targets. No configuration needed.

### Bucket 3 — Code parsers (floor/trade) → guided mapping UI, not a regex box

Exposing the floor regex or CSI pattern as a text input would just move the programming
requirement from a developer to the user. That fails the principle.

Instead: a **guided mapping UI** on project setup or first import:
1. Detect the distinct code prefixes in the uploaded schedule (scan the first two
   segments of each activity code, show frequency).
2. Present a table: each detected prefix → dropdown mapping to Floor/Zone, Trade, or
   "Ignore."
3. Store the mapping per project in a new `ProjectCodeMapping` model.
4. Replace the hardcoded regexes with a lookup against the stored mapping.

This keeps the user in a structured UI (not programming), survives any coding scheme, and
makes the mapping auditable and correctable.

---

## Status

All 7 analytics features built and verified for the IBS/Castor project:

| Feature | Service | Verified |
|---------|---------|---------|
| EVM / Earned Schedule | `evm.py` | ✓ |
| Critical path + WBS risk | `intelligence.py` | ✓ |
| Delay root-cause clustering | `delay_rootcause.py` | ✓ |
| Monte Carlo forecast | `monte_carlo.py` | ✓ |
| Completion probability (ML) | `completion_ml.py` | ✓ leak-free (ΔAUC 0.005 float ablation) |
| DCMA 14-point health | `dcma_check.py` | ✓ |
| Anomaly detection | `anomaly_detect.py` | ✓ counts verified on real data |

The gap between "works for this project" and "configurable product for any project" is
the 9 hardcoded thresholds in Bucket 1 and the two coding-scheme parsers in Bucket 3.
Bucket 1 is a UI/settings feature. Bucket 3 is a data-onboarding feature.
Neither is started.
