# MetaCastor D5 — Certified Tier 3 Reuse (Skill Bank Extension)

> Status: **Implemented and measured (2026-04-16).** Depends on D2 (skill bank) for retrieval and harvest. See *Measured Results* below for the A/B numbers and the prompt-bug finding the harness surfaced as a side effect.

## What D5 Is

D5 turns the skill bank into a memory for Tier 3 code generation. When an LLM-authored `modify_ifc()` function is approved, executed, and committed, the harvester already stores its code in `SkillExample.generated_code` (D2 infrastructure). D5 adds one retrieval-time branch: if the top-k examples include a Tier 3 success with code, that code is rendered into the Tier 3 planner's system prompt as a **reference pattern** — not executable code to copy, but a few-shot demonstration of what a working solution looks like for this class of operation.

No new model, no new retrieval pipeline, no parameterization logic. One new field of the `SkillExample` payload (`generated_code`) flows into one new formatter (`_format_tier3_references`). The full Tier 3 safety stack (sandbox, forbidden patterns, timeout, confidence gate, human review) still gates execution — nothing is bypassed.

---

## Why Not A Separate System

The initial design proposed a dedicated `CertifiedTier3` model with its own retrieval pipeline, similarity thresholds, and parameterization logic. At MSc scale a realistic deployment produces 10–20 Tier 3 invocations of which maybe 5–8 succeed. Building retrieval infrastructure for a pool of 5–8 entries is a dictionary, not a system. The Skill Bank (D2) already handles retrieval, scoring, and injection. D5 is a field extension.

Parameterization was also dropped. Tier 3 scripts differ not just in entity filters but in property sets, value types, validation logic, and traversal paths — mechanical swapping would be nearly as hard as generation from scratch. Letting the LLM use the prior script as a few-shot reference sidesteps this entirely.

---

## Retrieval and Injection

Retrieval is unchanged from D2: entity-type pre-filter, then cosine similarity, top-3. The only change in `_truncate()` is the addition of `generated_code` to the returned dict — `None` for Tier 1/2 examples, the raw code string for Tier 3.

Injection lives in `Tier3Planner._format_tier3_references`. It filters for `tier == 3 AND generated_code`, caps the rendered block at `_MAX_REFS = 2` (full-code references are token-expensive), and appends the block to `TIER3_SYSTEM_PROMPT`. The prompt explicitly instructs the LLM to **adapt, not copy verbatim**:

> Use them as reference patterns for approach and structure — do NOT copy verbatim. Entity references, property targets, and filter criteria differ.

The Tier 3 classifier → planner → executor path threads `skill_examples` through `_propose_chain`, `_propose_tier2` (for Tier 2→3 escalations), and `_propose_tier3` so that whichever escalation route reaches Tier 3, the same skill examples retrieved at the top of `propose()` are available.

---

## Token Budget

The classifier's few-shot budget calc (D2) runs at the top of `propose()` before tier is known and does not account for the Tier 3 reference block — the reference block only appears in the separate Tier 3 LLM call, whose system prompt (TIER3_SYSTEM_PROMPT) is large but independent. At `_MAX_REFS = 2` and realistic code lengths (~400–1200 tokens per reference) the additional footprint is ~1000–2400 tokens on top of the Tier 3 system prompt. On llama3.1:8b (8K context) this leaves room, but larger reference pools would need active trimming.

---

## How the Bank Fills Up — Two Paths

**Path A — seeding (cold start).** `t3_reference_bank.jsonl` holds hand-curated Tier 3 examples loaded via `seed_tier3_bank`. Seed records carry `is_organic=False`, `project=None` (global), `outcome_tier=3`, `was_approved=True`, `commit_success=True`, and a `generated_code` payload that is real working code — not a placeholder. This path is used for evaluation and for cold-start mitigation in new deployments.

**Path B — organic harvest (production).** When a user issues a Tier 3 modification through the Modify UI, approves it, and the IFC write + Git commit succeed, `harvest_skill_example()` fires automatically from `modification_service.execute()`. It reads `proposal.intent_json["code"]` and creates a `SkillExample` with `is_organic=True`. No user action beyond normal use is required — the bank grows as Tier 3 operations accumulate.

The retriever is path-agnostic: it filters on `was_approved AND commit_success`, not on `is_organic`. Seeded entries and organically harvested entries are indistinguishable at retrieval time. For the evaluation protocol below, Path A is sufficient — we do not need to drive real Tier 3 proposals through the UI to populate the bank.

Without any entries at all, the retriever returns nothing Tier-3-shaped and `_format_tier3_references()` emits an empty string. That is the correct baseline state.

---

## Evaluation Protocol

Run the baseline first (no injection), then seed the reference bank and run again with `--inject`. Same model, same temperature, same cases — the only delta is whether retrieval returns seeded Tier 3 references.

```bash
cd src
uv run python -m metacastor.eval.run_t3_eval                          # baseline
uv run manage.py seed_tier3_bank --file metacastor/eval/t3_reference_bank.jsonl
uv run python -m metacastor.eval.run_t3_eval --inject                 # D5 injection
```

**Five scoring axes** (each boolean per case):

| Axis | Pass condition |
|---|---|
| `parse_ok` | LLM response parses as JSON |
| `validation_ok` | `_validate_result()` + forbidden-pattern check pass |
| `confidence_ok` | `confidence >= 70` (production gate) |
| `execution_ok` | Sandbox `Tier3Executor` runs the code against the fixture copy without raising |
| `post_state_ok` | Returned `changes` list matches ground truth on `(min_changes, entity_types, summary_contains)` |

**Aggregate-only metrics:**

- Activation rate — fraction of cases where a Tier 3 reference was actually retrieved and injected. Expected ≈ 0 in the baseline run, > 0 in the injection run, never 100% (entity-type filter can still reject).
- Median generation time — A/B delta is a secondary signal; human review is the real Tier 3 bottleneck.

Fixtures are **never mutated** — the runner copies to a temp file before invoking `Tier3Executor`.

---

## Measured Results (2026-04-16, llama3.1:8b, T=0, 12 cases, seeded bank of 5 refs)

Two A/B rounds were run. The first round surfaced a pre-existing bug in the Tier 3 system prompt that the eval had no way to know about upfront. Reporting both is important — the first round is where D5's contribution appeared largest; the second round is where it stabilises to its real value after the confound is removed.

### Round 1 — pre-existing system-prompt bug in place

`TIER3_SYSTEM_PROMPT` at `src/writeback/services/tier3_planner.py` shipped with Example 1 using `ifcopenshell.api.run('spatial.assign_container', …)` to attach a new `IfcSpace` to a storey. `IfcSpace` is a spatial structure element — it attaches via `IfcRelAggregates` (`aggregate.assign_object`), not containment. The LLM faithfully copied the prompt's example and every `IfcSpace` CREATE case crashed at execution with `AttributeError: IfcSpace has no attribute 'ContainedInStructure'`.

| Metric (OVERALL) | Baseline | D5 injected | Delta |
|---|---|---|---|
| parse_ok | 100% | 100% | — |
| validation_ok | 100% | 100% | — |
| confidence_ok | 100% | 100% | — |
| **execution_ok** | **42%** | **50%** | **+8pp** |
| post_state_ok | 42% | 42% | — |
| Activation rate | 0 / 12 | 4 / 12 (33%) | +33pp |
| Median generation time | 23.3 s | 23.7 s | +0.4 s |

Per-difficulty execution_ok: CREATE 0→25%, DELETE 100%→100%, RELATIONSHIP 67%→67%, SPATIAL 0%→0%. The single case that flipped under injection was `t3_012` (IfcZone creation) — the system prompt has no IfcZone example, so ref_005 was the *only* prior the LLM had to draw from, and it was enough. For the three IfcSpace cases (t3_001–003), activation fired but the LLM followed the **system prompt's** wrong example rather than the **retrieved reference's** correct one.

### Round 2 — system-prompt bug fixed (1 line: `spatial.assign_container` → `aggregate.assign_object`)

| Metric (OVERALL) | Baseline | D5 injected | Delta |
|---|---|---|---|
| parse_ok | 100% | 100% | — |
| validation_ok | 100% | 100% | — |
| confidence_ok | 100% | 100% | — |
| **execution_ok** | **83%** | **83%** | **0pp** |
| post_state_ok | 75% | 75% | 0pp |
| Activation rate | 0 / 12 | 4 / 12 (33%) | +33pp |
| Median generation time | 18.3 s | 22.4 s | +4.1 s |

Per-difficulty execution_ok: CREATE 100%, DELETE 100%, RELATIONSHIP 100%, SPATIAL 0%. Injection ran on the same four cases as round 1 (the retriever is deterministic at T=0). The numbers did not move: the three IfcSpace cases now succeed in the **baseline** because the prompt itself is correct; the injected reference is redundant with the prompt. IfcZone (t3_012) executes under both conditions but fails `post_state_ok` in both — the LLM copied ref_005's hardcoded `Zone-01` name instead of using the user-requested "Thermal Zone A", which is the exact `copy-too-literally` failure the spec warned about.

### The real takeaway

D5's contribution at 8B scale on this 12-case set is **+8pp execution when the system prompt has an authoritative wrong prior on a covered operation, 0pp otherwise**. At the same time:

- **D5 delivered independent value as a diagnostic tool.** The eval surfaced a pre-existing Tier 3 prompt bug that would have silently degraded every IfcSpace creation in production. Fixing the prompt alone lifted end-to-end execution from 42% to 83% (+41pp) on this set.
- **D5's value is on the long tail, not the short tail.** Cases the system prompt already covers correctly (DELETE, aggregation via IfcRelAggregates) do not benefit from reference patterns. Cases the system prompt does not cover at all (IfcZone, and — had ref_004 been followed without error — storey reassignment) are where injection is the only signal source.
- **The "do NOT copy verbatim" instruction is weak at 8B.** Ref_005 explicitly tells the model to substitute names, and the model still hardcoded the reference's name. For reliable adaptation of code references, a stronger model or a parameterised template is needed — but that is out of scope for D5 per the original spec.

---

## Honest Limitations (confirmed by the 2026-04-16 runs)

- **Small pool, small N.** 5 seed references, 12 eval cases, 4 activations per injected run. Don't over-interpret single-digit case deltas — one flipped case moves the OVERALL by 8pp.
- **System-prompt priors outweigh injected references at 8B scale.** Observed directly in round 1: the LLM followed `TIER3_SYSTEM_PROMPT`'s Example 1 rather than the retrieved, demonstrably-correct reference for every IfcSpace case. Injection does not correct an authoritative wrong prior; it supplements a correct one.
- **"Do NOT copy verbatim" is a weak instruction on small models.** Round 2 `t3_012` executed but failed post_state because the LLM copied ref_005's hardcoded `Zone-01` name instead of substituting the user's "Thermal Zone A". The safety stack does not catch this class of failure — only post-state assertion does.
- **Injection widens generation time.** Round 2 median gen time rose from 18.3 s to 22.4 s (+22%) despite identical success rates, because the injected prompt is longer. On production hardware where Tier 3 is already the slow path, this adds ~4 s of latency per invocation.
- **D5's upside is restricted to the long tail.** Operations the system prompt already covers correctly (delete, aggregate) gain nothing. The only observed win was IfcZone creation — an operation with no prompt-level example.
- **Determinism caveat.** T=0 reduces but doesn't eliminate LLM output variance, especially at the longer outputs Tier 3 produces. A third run might shift numbers by 1–2 cases.
- **Post-state soft match is permissive.** `(min_changes, entity_types, summary_contains)` accepts wrong-but-shaped-right outputs. Deliberate trade-off — strict GUID-level diffing is brittle across runs.
- **Stale references over time.** A script that worked when the IFC file had 47 walls with `Pset_WallCommon` may reference assumptions that no longer hold after subsequent commits. Not observed in this 12-case run but worth flagging for longer-horizon deployments.

---

## Key Files

- `metacastor/models.py` — `SkillExample.generated_code` field (added in D2)
- `metacastor/services/skill_retriever.py` — `_truncate()` surfaces `generated_code`
- `metacastor/services/skill_harvester.py` — captures `intent.get("code")` on successful Tier 3 commits (D2)
- `metacastor/management/commands/seed_tier3_bank.py` — cold-start seeding from `t3_reference_bank.jsonl`
- `metacastor/eval/t3_reference_bank.jsonl` — certified Tier 3 reference patterns
- `metacastor/eval/t3_eval_cases.jsonl` — A/B cases with soft post-state ground truth
- `metacastor/eval/run_t3_eval.py` — A/B runner
- `writeback/services/tier3_planner.py` — `_format_tier3_references()`, `skill_examples` param on `generate_code()`
- `writeback/services/modification_service.py` — threads `skill_examples` through `_propose_chain` → `_propose_tier2` → `_propose_tier3`

Admin at `/admin/metacastor/skillexample/` filters by `outcome_tier=3` and `is_organic=False` to inspect the seeded reference bank.