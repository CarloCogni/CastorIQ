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

## Schedule Audit Engine

A separate advisory pipeline that runs **alongside** the analytics features above and
flags potential data-quality issues in the schedule for human review. It never modifies
any task, trade classification, or analytics result. The original P6 CSI code is always
the source of record; the audit only surfaces candidates for the planner to inspect and
correct in P6 if warranted.

### Layer 1 — Section-Mismatch Detection (BUILT)

**Service:** `schedule_audit.py · run_section_mismatch_audit(project_id, user=None)`
**Endpoint:** `GET /islam/projects/<pk>/schedule/audit/section-mismatches/`
**UI:** "Schedule Audit" card in the EVM tab, between Anomaly Detection and Report Generator.

Compares each floor-located activity **name** against its planner-coded **CSI division**
and flags likely mis-codes as a review queue. Three stages:

**Stage 1 — keyword pre-filter (no AI, < 100 ms):**
Scans each task name for strong trade keywords. If the keyword implies a different CSI
division than the one in the code, the task becomes a candidate. On the IBS project
(8,766 tasks): 499 candidates, 77 unique names → ~4 LLM batch calls.

**Stage 2 — LLM batch review (unique names only, cached by name):**
Sends batches of 20 unique candidate names to the LLM and asks for a CSI division,
confidence, and a one-line reason. The stable system prompt is marked for Anthropic
ephemeral prompt caching so only the first batch pays full input-token cost. On LLM
unavailability (`LLMConfigurationError`, `LLMMasterKillError`), all candidates receive
`verdict=unavailable` and `ai_ran=False` — no exception is raised.

**Provider note:** LLM provider for the audit is Ollama (local) by design — keeps the audit zero-cost. The name-level Django cache (24h TTL, temperature=0) makes repeat runs instant. Do NOT switch to a paid cloud provider; the ~140s cold first-run is acceptable and the cache handles the rest.

**Stage 3 — verdict assignment:**

| AI output | Verdict | Meaning |
|-----------|---------|---------|
| `division` ≠ coded, `confidence=high` | `confirmed` | Clear coding error; a reviewer would agree |
| `division` ≠ coded, `confidence=medium` | `needs_review` | Possible mismatch; planner's choice may be defensible |
| `confidence=low` or `division=uncertain` | `uncertain` | Genuinely ambiguous; don't assert |
| `division` == coded (any confidence) | `likely_ok` | Keyword false-positive; excluded from output |
| LLM batch failed | `unavailable` | Shown in a collapsed accordion |

Live results on IBS project after calibration: **75 confirmed, 28 needs_review, 8 uncertain,
388 likely_ok** out of 499 stage-1 candidates.

---

### Tunables introduced — where to adjust them

#### Stage 1 keyword rule groups

**File:** `schedule_audit.py:88–266` (constant `_KEYWORD_RULES`)
**Current value:** 10 rule groups covering Finishes (09), Concrete (03), HVAC (23),
Electrical (26), Plumbing (22), Masonry (04), Thermal & Moisture (07), Fire Suppression
(21), Metals (05), Earthwork (31).

Each rule is a `(keyword_list, csi_division, trade_label)` tuple. Keyword lists are
deliberately narrow: prefer false-negatives (missed mismatches) over false-positives
(spurious flags). The LLM clears borderline keyword hits at Stage 2.

**When to adjust:** if a new project uses trade-specific terminology not in the lists
(e.g. "screed" missing the "cement" qualifier, or regional naming conventions), add
keywords to the relevant group. Removing keywords broadens recall; adding tighter
multi-word phrases improves precision.

#### Confidence gating thresholds

**File:** `schedule_audit.py:499–524` (verdict assignment block in `_run_stage2`)
**Current logic:**
- `confidence=high` + disagreement → `confirmed`
- `confidence=medium` + disagreement → `needs_review`
- `confidence=low` or `division=uncertain` → `uncertain`

**When to adjust:** if the `confirmed` tier still contains false positives, raise the
bar to require `high` confidence AND a minimum word overlap between reason and known
division keywords. If `needs_review` is too noisy, demote it to `uncertain`.

#### LLM gray-area disambiguation rules

**File:** `schedule_audit.py:274–355` (constant `_SYSTEM_PROMPT`)
**Current explicit rules encoded in the prompt:**

1. **Scope rule (most important):** A conduit, cable, tray, or fitting that *serves a
   named building system* belongs to that system's division — not the generic trade
   division. Fire-alarm conduits → 28, not 26. Security/access-control wiring → 28, not
   26. IT/comms conduit → 27, not 26. Fire-suppression pipework → 21, not 22. This rule
   was added after the initial run produced 96 false-positive "confirmed" flags on
   fire-alarm conduits.
2. **EIFS = div 07.** Exterior Insulation and Finish System is CSI 07 24 (Thermal &
   Moisture Protection). Despite "finish" in the name it is never div 09. Added after the
   initial run incorrectly classified EIFS items as div 09 Finishes.
3. **Roofing screed/tiles → 07.** Cement screed or tiles laid as part of a roofing
   assembly are protection layers over the waterproofing membrane, not floor finishes.
4. **Traffic coatings / painted pavement markings → 07** (CSI 07 18).
5. **Wire mesh + plaster → 09.** This combination is a plaster finish system.
6. **Return `uncertain` when in doubt.** Admin/procurement tasks (Submit, Approve,
   Procure) always return `uncertain`.

**When to adjust:** add new rules when a pattern of false positives or false negatives
is identified across a project's confirmed output. Each rule should cite the specific CSI
section number to anchor it.

---

### AI-in-the-loop caveats

**The LLM is advisory; all output requires human review before acting.**
The audit surfaces a *review queue*, not a correction list. A planner receiving the
output should open P6, inspect each flagged task, and apply their own judgment before
changing anything. The audit is correct to say "this looks like a mismatch"; it is not
authoritative that it is one.

**Initial calibration failure — over-confidence.**
The first prompt produced **0 uncertain** items across 499 candidates — the model
classified everything at high or medium confidence. This was a calibration failure:
genuinely ambiguous cases (fire-alarm conduits, EIFS, scope-boundary items) were being
asserted rather than flagged as uncertain. The fix required explicit prompt instructions:

- *"Returning `uncertain` is the CORRECT and PREFERRED answer when the planner's coding
  is reasonable."*
- Explicit scope rule preventing system-specific conduits from being reclassified.
- Confidence semantics defined: `high` = CSI MasterFormat is unambiguous; `medium` =
  likely but planner's choice has a reasonable basis; `low` = unclear.

After calibration: 8 uncertain, 75 confirmed (down from 138), 28 needs_review.

**Future audit layers must embed the same discipline from the start.** Write the system
prompt to treat `uncertain` as a first-class, correct output, not a fallback. A model
that never refuses is miscalibrated regardless of how good its confirmations look.

**The confirmed tier is intentionally high-precision.**
A few real mismatches may sit in `needs_review` rather than `confirmed`. That is
acceptable: a review queue where 95% of confirmed items are genuine errors is more
useful than one where planners must second-guess every flag. Precision over recall.

---

### Audit-suggested view (toggle)

The toggle lets a user see what the analytics would look like if every **confirmed**
mismatch were corrected — without touching the underlying schedule data.

#### Two-layer design

| Layer | What it is | Source of record |
|-------|-----------|-----------------|
| **As-coded** (default) | Original P6 CSI codes, exactly as the planner entered them | Always |
| **Audit-suggested** | Confirmed overrides layered on top at query time | Advisory only |

The toggle is opt-in; the default is always As-coded. The original P6 CSI code on each
task is **never modified** — `task.activity_code` is read-only from the audit's
perspective. The override is a lookup table applied at analytics-query time: every
trade-based service (`timelocation.py`, `delay_rootcause.py`, `anomaly_detect.py`,
`floor_health.py`) accepts an optional `override_map` dict; `None` = As-coded, a populated
dict = Audit-suggested. Switching modes is a fetch, not a write.

#### Override map — confirmed-only, DB-persisted

`trade_resolver.py · build_override_map(audit_result)` extracts `{task_pk: ai_csi}` from
**confirmed-verdict items only** — `needs_review` and `uncertain` items are excluded. The
map is persisted in `Project.audit_override_map` (a `JSONField`), not in the Django cache,
so it survives server restarts. On the next page load the toggle button is immediately
enabled without re-running the audit.

The cache (`audit_overrides_` prefix, 2-hour TTL) is a warm-path optimisation on top of
the DB value; `load_override_map()` always falls back to the DB on a cache miss, and an
empty cache hit is treated as a miss so a stale empty entry can never shadow a real map.

#### Determinism — per-name LLM verdicts in the DB

**The problem:** Ollama at `temperature=0` is still non-deterministic across cold LLM
calls due to GPU floating-point parallelism. Without stabilisation, re-running the audit
after a server restart (which clears the Django locmem cache) could shift the confirmed
count by 10–20 items, silently changing the override map and every downstream analytic.

**The fix:** `Project.audit_name_cache` (`JSONField`) stores `{md5_key: llm_result_dict}`
for every name the LLM has classified. `_run_stage2()` checks this DB field as a cold
fallback after the Django locmem cache miss — before calling the LLM. After each batch of
new LLM calls, the new entries are merged back into the DB field in a single `UPDATE`.
The effective lookup order is:

```
1. Django locmem cache   (fast path, 24h TTL)
2. Project.audit_name_cache  (DB, no TTL — survives restarts)
3. LLM call              (only for truly new names; result written to 1 + 2)
```

Once a `(name, coded_csi)` pair has been classified, it stays classified permanently.
Re-running the audit produces the **same confirmed set** regardless of server restarts or
cache expiry. The count can only shift if the DB field is explicitly cleared, or if new
tasks with previously-unseen names are added to the schedule.

**Caveat:** on the very first run after clearing `audit_name_cache`, the LLM IS called for
all candidate names. That run may produce a different count than a prior pre-fix run (which
is expected: the old result was non-deterministic). From the second run onward the count
is stable.

#### Toggle-linked analytics — live-state, generation-guarded

Four analytics respond to the toggle: Time-Location, Delay Root-Cause, Anomaly Detection,
Floor Health. Each follows the same pattern:

**Mode is read live at fetch time, not captured at render time.**
The load function receives the mode string as a parameter from the toggle controller
(`_setMode(m)` → `loadX(m)`). Inside the function, `const mode = auditMode || _evmAudit.mode()`
is evaluated fresh on every call — no closure captures. This was the class of bug that
caused the Time-Location hover to show stale data (fixed: hover now reads `_highlightKeys`
directly from IIFE scope, not from a render-time `const anyHighlight` snapshot).

**Generation counters discard superseded async responses.**
Each chart holds an IIFE-scoped counter (`_drcGen`, `_anomalyGen`, `_healthGen`).
Every load call increments its counter and saves the value as `const gen`. After
`await resp.json()`, the guard `if (gen !== _XxxGen) return` discards responses from
toggling-while-inflight — preventing a slow "corrected" response from overwriting a newer
"coded" render. The opacity fade is also reverted only by the winning response.

**Handler replace semantics (no accumulation).**
Floor Health sets `grid.onmouseover = ...` and `grid.onmouseout = ...` (property
assignment) so each reload replaces the previous handler. The prior `addEventListener`
pattern accumulated a new listener pair on every toggle, eventually firing multiple stale
closures on each hover. All other toggle charts use HTML rendering with no persistent
handlers, so accumulation was not an issue there.

**Audit-suggested banner.**
When a chart renders in corrected mode it checks `data.using_audit_overrides &&
data.n_overridden > 0` and shows an amber inline banner describing how many confirmed
reclassifications were applied. The banner is hidden when the chart returns to As-coded
mode. The Decision Summary section shows a separate static note ("Based on original P6
coding") whenever the screen is in corrected mode, since its blocks are trade-agnostic
(SPI/CPI, DCMA score, CPM root-cause tasks) and do not change with the toggle.

#### Report generator and Decision Summary — always As-coded

`report_generator.py · gather_report_data()` calls all services without an `override_map`.
Both the PDF and DOCX headers carry a gray note: *"All values reflect original P6 coding.
Audit-suggested reclassifications are not applied to reports."* This is intentional: a
PDF report is a formal record and should reflect the signed-off schedule, not an advisory
reclassification that the planner has not yet acted on.

`decision_layer.py · compute_decision_summary()` follows the same rule: all blocks use
As-coded data. Block 1 (SPI/CPI/forecast) and Block 3 (top CPM delay tasks) are not
affected by trade codes at all; Block 2 (DCMA/anomaly) does use trade peer groups for
statistical outlier detection, but those outlier counts are advisory and the executive
summary is not the right place to surface an unconfirmed reclassification.

#### Operational caveat

The override map is advisory. It reflects whatever the audit produced on the run whose
results are stored in `Project.audit_override_map`. The correct long-term action is for
the planner to inspect the confirmed mismatches, decide which ones are genuine errors, and
correct the CSI codes in P6. After a P6 re-import and re-audit, the new override map will
reflect the corrected baseline. **The tool never writes back to P6 and never auto-applies
corrections.** The toggle is a what-if lens, not a patch.

---

### Future audit layers (ROADMAP — not built)

All future layers follow the same principle as Layer 1: **detect and flag for human
review, never auto-correct.** The planner's data is authoritative; the audit is advisory.

#### Layer 2 — Sequencing audit

Detect unusual or suspicious activity orderings within a floor/zone/trade cell.

**Key design constraint:** do **not** assume a fixed construction sequence. The correct
sequence varies by structural system (flat-slab vs post-tensioned vs precast), procurement
strategy, and site conditions. A rule like "concrete before finishes" holds in general but
breaks for precast systems where finishes are factory-applied. The right approach:

- Learn the project's own patterns from completed activities (similar to how
  `completion_ml.py` learns trade on-time rates from the project's own history).
- Flag activities whose ordering deviates significantly from the project's own majority
  pattern, not from an externally assumed sequence.
- Output: "On this project, 94% of Level 5 Finishes tasks follow Concrete tasks on the
  same floor. This task precedes all Concrete tasks on Level 5 — flag for review."

This ties into the existing CPM driving-relationship graph in `delay_rootcause.py`.

#### Layer 3 — Baseline realism audit

Flag tasks where the planned duration is statistically improbable given the project's own
completed tasks of the same trade and floor.

Ties into the existing unrealistic-baseline anomaly detection in `anomaly_detect.py`
(which already flags construction-trade tasks with planned_duration ≤ 4 days as
"unrealistic baseline"). Layer 3 would extend this to the full distribution using the
Monte Carlo duration data already computed by `monte_carlo.py`.

#### Layer 4 — Logic and constraint audit

Flag missing predecessors (tasks with no predecessors that are not legitimate project
starts), redundant constraints (constrained date that is already enforced by logic), and
open ends (tasks with no successors that are not legitimate project finishes).

Ties into the existing DCMA 14-point checks in `dcma_check.py` (which already measures
missing-predecessor and open-end rates). Layer 4 would surface individual task-level
examples rather than aggregate percentages.

---

## Status

All 7 analytics features + Schedule Audit Layer 1 built and verified for the IBS/Castor project:

| Feature | Service | Verified |
|---------|---------|---------|
| EVM / Earned Schedule | `evm.py` | ✓ |
| Critical path + WBS risk | `intelligence.py` | ✓ |
| Delay root-cause clustering | `delay_rootcause.py` | ✓ |
| Monte Carlo forecast | `monte_carlo.py` | ✓ |
| Completion probability (ML) | `completion_ml.py` | ✓ leak-free (ΔAUC 0.005 float ablation) |
| DCMA 14-point health | `dcma_check.py` | ✓ |
| Anomaly detection | `anomaly_detect.py` | ✓ counts verified on real data |
| Schedule Audit — Layer 1 (section mismatch) | `schedule_audit.py` | ✓ 75 confirmed / 28 needs_review / 8 uncertain on IBS; zero false positives in confirmed tier after calibration |

The gap between "works for this project" and "configurable product for any project" is
the 9 hardcoded thresholds in Bucket 1 and the two coding-scheme parsers in Bucket 3.
Bucket 1 is a UI/settings feature. Bucket 3 is a data-onboarding feature.
Neither is started.
