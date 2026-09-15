# Modify pipeline V3 — brainstorming

**Date:** 2026-09-14 (v3.2, open questions resolved) · **Status:** brainstorm, frozen · **Owner:** Carlo

> **Superseded notice (2026-09-14, after two design reviews).** This document is frozen as the
> argument that produced V3. The current design is [`../writeback_V3/spec.md`](../writeback_V3/spec.md);
> the [decision log](../writeback_V3/decision-log.md) records each reversal. The following parts
> of this document state decisions the spec has since reversed and must not be read as current:
> §0 checkpoint-block-on-triggers, §0 Ask-model back-translation, §0 generated corpus
> expectations, §0 keyword → module cheat-sheet map; §1 two sheets and per-request retrieval,
> §1 `format=` JSON schema on the code path; §2 P0 pgvector grounding and three LLM calls,
> §2.3 the checkpoint, §2.5 near-miss grounding and the deterministic sanity checks; §3 journal
> and executor kept, `llm_boundary` extended with `format=`, FilterEngine demoted; §5.1
> property-count and operation-shape rules; §5.2 snapshot cache and intermediate target event;
> §5.4 re-diff at approval and rebase; §6 Ask-model back-translation; §7 all three questions.

v1 challenged the "code-only + agent chain" premise and offered three shapes. Review outcome:
code-only is confirmed, the ring around it stays, and the shape becomes **select → mutate →
verify**. v2 worked that out. v3 (second review) promotes the selection step from a one-line
cell to the load-bearing section (§2.5), corrects the keep table, and adds the open questions
that review surfaced. v3.1 turns §5 from a list of accepted risks into risk → mitigation →
residual, and adds the cheat-sheet rules and the Guardian toggle. v3.2 moves the decided
answers from the open questions into §0 and §6 and leaves §7 with what is still open. Still short on purpose.

## 0. Decided so far

- **One path: generated IfcOpenShell code.** Tiers gone. Minimal authority becomes
  maximal verification; the FMP report must re-argue that, not swap it silently (27 Sep).
- **Keep the ring:** git, journal + sandbox, fingerprint, round-trip diff, Guardian, DB sync,
  benchmark. How far each survives is §3.
- **The Modify model becomes a coder model.** Ask keeps its own; settings already split
  provider/model by purpose, so this is a default change, not a base-LLM change.
- **Design rule:** every LLM call has one small job, one small output, and a deterministic
  check behind it. V2's failures all lived between calls with no check in between.
- **The DB is a lookup, not a selector.** V2's targeting failed because the LLM had to
  compress the request into FilterEngine's seven keys and guess vocabulary the file did not
  use. In V3 the LLM writes the selection; the DB only supplies exact strings (§2.5).
- **VRAM: 8 GB floor, 12 GB default.** `qwen2.5-coder:7b` on 8 GB, `:14b` on 12 GB, no CPU
  offload: a 14B spilled to RAM turns a thirty-second codegen into minutes. The bake-off
  runs both rows; the FMP claim is "works on 8 GB with measured degradation".
- **Checkpoint: show always, block on triggers.** Zero targets, more than N (N a site
  setting), creation or deletion, no explicit noun in the request.
- **The card is diff + back-translation.** Code collapsed, one click to expand, always in
  the git commit. The ack gate sits on flagged diff rows (§5.1), not on the code block.
- **Multi-intent = one commit.** One select + one modify covering all intents, one diff,
  one commit. §5.1's property-count rule allows N distinct properties for N intents.
- **Back-translation by the Ask model.** Prose is its job. On 8/12 GB the coder and the Ask
  model cannot co-reside, so the request pays one model swap before P3b; the card names
  the explainer model; a setting can point the explainer at the coder for speed.
- **Helper library ships with eight:** elements_in_storey, elements_in_space, by_type,
  by_name, by_pset_value, by_material, decomposition_of, container_of. Grows only by
  §5.3's evidence rule.
- **Corpus expectations are generated, then reviewed.** Run the corpus once with the Claude
  ceiling row, capture `targets:` and `diff:` per prompt, hand-review every case.
  Hand-writing ninety GlobalId sets is slower, and one typo fails a good case forever.
- **Guardian toggle: per request, stateless.** Off means no Guardian on this request; the
  skip is recorded on the proposal.
- **Cheat-sheet by keyword → module map**, not pgvector. The api is closed vocabulary
  (~15 modules); entity names are open vocabulary, which is why grounding uses pgvector.
  Escape hatch: an unknown api attribute in a repair turn pulls that module in.

## 1. Which coder model

What matters here is not leaderboard coding skill. No open model knows `ifcopenshell.api`
0.8 from memory, so the api reference gets injected (introspected from the installed version).
The model must (a) follow instructions over ~6–10k tokens of grounded context, (b) write
plain Python, (c) fit the 8–12 GB tier, (d) carry a permissive licence, (e) honour Ollama's
JSON-schema `format=`. All of the candidates below do (e).

| Model (Ollama tag) | ~VRAM Q4 | Tier | Note |
|---|---|---|---|
| `qwen2.5-coder:7b` | 4.7 GB | standard ≤8 GB | floor for the 8 GB persona |
| `qwen2.5-coder:14b` | 9 GB | performance ≤12 GB | **proposed default** |
| `qwen3-coder:30b` (MoE, 3B active) | 19 GB | high_end ≤24 GB | fast per token, best local quality |
| `devstral` (24B) | 14 GB | high_end | agentic-coding tuned, dense, slower |
| `gpt-oss:20b` (MoE) | 13 GB | high_end | strong reasoning, not code-tuned |
| `qwen3:14b` | 9 GB | performance | **control**: today's best non-coder |
| Claude via BYOK | — | cloud | **ceiling**: how far local is from frontier |
| `codestral` | 13 GB | — | excluded: non-production licence |

Recommendation: default `qwen2.5-coder:14b`, `:7b` on 8 GB, `qwen3-coder:30b` on 24 GB —
**decided by the bake-off, not by this table.** Bake-off = the existing benchmark with
`--model` per row, scoring fidelity, integrity and a new "targets + diff match expectation"
column (§3). Two gotchas: Ollama's default context is 4k, so `num_ctx` must be set explicitly
or the api reference is silently truncated; and a coder model is worse at prose, so it must not
leak into Ask, Guardian or the conflict scan.

**Cheat-sheet rules.** The api reference is the model's only source of `ifcopenshell.api`
names, so it stays; but a two-hundred-entry sheet with worked examples is a menu, and a
model pads its answer from a menu.

- **Retrieved per request, not dumped** (keyword → module map, §0). A property edit gets
  the pset/property functions,
  a creation gets root/spatial/type functions. Eight to twelve entries, not two hundred.
- **Signatures and one-line docstrings only.** No worked examples with concrete values;
  examples are what get copied.
- **Two sheets.** The select sheet holds read-only helpers and traversal, zero mutation
  functions, so select code cannot even see a way to write. The mutate sheet holds the api.
- **Backstop is §5.1.** A leaked extra call shows up as a red row in the diff, not as a
  silent change.

## 2. The shape: select → mutate → verify

```
P0 ground   (no LLM)   retrieval, not a dump: request words matched against the DB
                       (storeys, spaces, types + counts, pset names, values; pgvector for
                       "first floor" → "Level 1 (+0.00)") → only the hits, as exact
                       strings, plus the api cheat-sheet and the helper signatures
P1 select   (LLM #1)   def select(model) -> list[entity]     read-only, composes
                       vetted helpers (castor_select) + raw api; sandbox on a file copy
                       → GlobalIds → deterministic sanity checks (§2.5)
                       → checkpoint shows evidence, not a count       [§2.3]
P2 mutate   (LLM #2)   def modify(model, targets) -> None    given select()'s output
                       harness composes select+modify on a scratch copy → IfcDiff
P3 verify              (a) deterministic: diff.unexpected(allowed=target GUIDs) must be empty;
                           empty diff = "already so", not a retry
                       (b) LLM #3, blind: describe the diff without seeing the request
                       (c) compare "you asked / this will do" — user reads both;
                           optional advisory verdict (today's Tier3Reviewer, re-pointed)
P4 approve             existing path: git snapshot → journal RUN_CODE → re-diff on the
                       real file (§5.4) → commit → DB sync from the diff → Guardian from
                       the diff, skippable per request (§5.4)
```

Three LLM calls, four with the verdict, each small. V2 spends five to eight. Traceback or
an out-of-scope diff goes back to the same call with the error, max two repairs.

### 2.1 Why the select phase earns its place, and what it does not fix

- Targeting is the number-one churn cluster in the git history. A read-only query the user
  can see, with a count, before anything is generated for mutation, attacks it directly.
- **The selection becomes the `allowed` set for the diff.** Scope is enforced after the fact
  by `IfcDiff.unexpected`, not by prompt wording. Creation requests select the container;
  the population change is expected and flagged, not forbidden.
- Today's Tier 3 has `affected_count=0` and no preview. This gives both before mutation.
- It does **not** fix right-entities-wrong-operation ("remove X" executed as "set X").
  That is what P3(b) is for.

### 2.2 Where the selection code runs

- **S1 — ifcopenshell on a file copy, sandbox in read-only mode (recommended).** Same idiom
  as the mutation code, fully expressive (materials, types, containment, any pset), cannot
  touch the DB by construction. Cost: the file is opened twice per request; large models pay
  seconds each time. Sandbox needs a mode where the child never writes.
- **S2 — LLM emits a FilterEngine spec.** Fast, no sandbox. It is V2's filter schema again:
  drift and substitution come back. Dropped; the helper library (§2.5) is the fast path.
- **S3 — LLM-written Django ORM code in-process.** No. Arbitrary code with DB write access.

### 2.3 Checkpoint after select — decided

**Show always, block on triggers.** The target list is always displayed; the pipeline
continues straight to P2 unless one of these fires:

- zero targets;
- more than N targets (N a site setting, value open in §7);
- the request creates or deletes entities;
- the request named nothing explicit (no type, name, storey, space or GlobalId).

Rejected: *always block* (doubles round-trips; a checkpoint rubber-stamped on every
single-entity request stops being a check) and *never block, one approval on the diff*
(loses the cheap early catch).

### 2.4 Review: code, diff, or back-translation?

- **LLM reads the code against the request.** Same model, same blind spots: it cannot flag an
  api name it also believes in. Cheap, but keep it out of the critical path.
- **LLM reads the diff.** Grounded in what actually happened. Short input. Better.
- **Reverse check (Carlo's, sharpened).** LLM reads code + diff **without the request** and
  writes what it does in plain language. Blind, so it cannot parrot. The user (or an advisory
  verdict) compares that with the request. This is the one check that targets substitution.
- Order of trust: deterministic first (2.1), LLM second, human last. The human reviews the
  diff and the back-translation; code stays available but collapsed.

### 2.5 Making select right

The whole safety story rests on the `allowed` set being the right entities. In V2 the
FilterEngine was the selector and the LLM had to squeeze the request into seven keys with
substring matching: "doors in the corridor" has no key, so the nearest one (name_pattern)
returns doors *named* corridor, confidently. In V3 the FilterEngine never sees the LLM's
output; the LLM writes the selection and these four measures keep it honest.

- **Grounding as retrieval.** Match the request's tokens against the index and inject only
  the hits, verbatim: the storey that matches "first floor", the space that matches
  "corridor", the psets present on the named type, the population count. Near-miss names
  are held back for the zero-target repair turn ("no storey matches 'first floor';
  closest: Level 1 (+0.00)"). The shared 1024d space does the fuzzy part.
- **Helper library (`castor_select`).** Ten to fifteen tested read-only functions:
  elements_in_storey, elements_in_space, by_pset_value, by_type_name, by_material,
  decomposition_of, contained_by. The prompt says "prefer these, raw api allowed". The 90%
  path is three lines of composition, not thirty lines of containment traversal that a 14B
  model gets subtly wrong. Named risk: past ~20 functions it is V2's schema again; cure
  in §5.3.
- **Deterministic sanity checks, no LLM.** Request named a type ⇒ every target is that type.
  Request named a storey or space ⇒ every target is contained there. Zero targets ⇒ one
  repair turn with near-misses. Count far above the type's population share ⇒ checkpoint
  trigger. All reuse the grounded vocabulary, so they cost nothing.
- **Checkpoint shows evidence, not a number.** "5 walls" is rubber-stamped. "5 IfcWall on
  Level 1 (+0.00): Wall-201, Wall-202, … all IsExternal=True" is read. Name, container, one
  distinguishing property per target.

Worked example, "set the fire rating of the corridor doors to EI30": grounding finds space
`Corridor 1.02` on Level 1 and 14 IfcDoor in the file. select() is
`by_type_name(elements_in_space(model, "Corridor 1.02"), "IfcDoor")`. Sandbox returns 3
GlobalIds; sanity check passes (all IfcDoor, all in that space); checkpoint lists the three
doors by name and space. modify() sets the property; the diff confirms exactly those three
changed. V2 on the same request: resolver picks doors with "corridor" in the name, finds
zero, refines twice, then grabs all doors or gives up.

The bake-off criterion that matters most is the "targets match" column (§3), more than
code quality: a model that writes elegant mutation code on the wrong walls is worse than
none.

## 3. What we keep, and how far

| Component | Fate | Note |
|---|---|---|
| GitService, MutationJournal RUN_CODE, JournalExecutor, fingerprint | keep | untouched |
| code_sandbox | keep + extend | read-only select mode; drop the `changes` self-report contract, the diff replaces it |
| IfcDiff | keep | becomes the centre of the pipeline |
| FilterEngine | keep, demoted | P0 lookup only (storeys, spaces, types, psets, name matches); its spec is nobody's output |
| `castor_select` helper library | **new** | eight vetted read-only helpers (§0); tested like a writer |
| llm_boundary.call_structured | keep + extend | pass a real JSON schema to Ollama `format=` |
| Benchmark corpus | rewrite expectations | `router:` lines die with the tiers; each prompt gets `targets:` (GlobalId set) and `diff:` (expected changes). Prerequisite for the bake-off, not follow-up |
| Benchmark fidelity | adapt | today RUN_CODE is advisory ("effects not independently verifiable"); in V3 everything is RUN_CODE, so fidelity becomes "diff matches expectation" or the column is empty |
| Benchmark integrity | keep | `IfcDiff.unexpected(allowed=targets)`, unchanged; runtime now runs the same check (P3a) |
| Guardian | adapt | query built from the diff, not `intent_json`; skippable per request, skip recorded on the proposal |
| DB sync | adapt | driven by the diff, not by self-reported GlobalIds |
| ModificationProposal | adapt | `tier` vestigial; add targets + preview diff; `requires_code_ack` → diff acknowledgement |
| Tier3Reviewer | adapt | becomes the blind back-translation + advisory verdict |
| `_modify.html`, help modal, emitter phases | adapt | new phase vocabulary: ground / select / mutate / verify / guardian |
| `.claude/skills/writeback-ops.md` | rewrite | stale since August anyway |
| triage, slot extractor, entity resolver, tier router, intent assembler, tier 1/2 validators, t3 op planner, filter_builder, hint generator (LLM part) | drop | roughly 6k lines of services plus their tests |

## 4. Roads not taken, kept for the record

- **Spec → code → verify.** Its assertions survive as the `allowed` diff check; its spec
  checkpoint is replaced by the select checkpoint.
- **Constrained `ifcopenshell.api` call plan.** The fallback if the bake-off shows local
  coders cannot write correct code. The api introspection is needed either way (it is the
  cheat-sheet), so nothing is wasted.
- **Skill library from successful runs.** Later.

## 5. Risks: mitigation and what stays residual

Each block is risk → mitigation → residual. The mitigations are design commitments for V3,
not follow-ups.

### 5.1 A plausible-but-wrong diff the user skims

**Risk.** "Set FireRating to EI60 on Level 1 walls": the code sets FireRating on the five
allowed walls and also ThermalTransmittance, copied from the cheat-sheet. All ten rows are
in scope, the user sees "5 walls, EI60", approves. Or "remove FireRating" executed as "set
FireRating to ''". The scope check asks *which entities*, never *which properties* or
*which operation*.

**Mitigation.**
- **Aggregate, then surface anomalies.** Render "FireRating: EI30 → EI60 on 5 walls", not
  five rows. Anything that breaks the dominant pattern (second property, second type, a
  removal among sets) goes on top in a warning colour.
- **Property-count rule.** A single-intent request changes one distinct property or
  attribute; two or more is flagged. Multi-intent requests know their count from the split.
- **Value groundedness on the diff side.** Every new value in the diff must appear in the
  request, or be a boolean, or be unit-derivable; otherwise flagged. This is V2's
  slot-extractor guard moved from input to output.
- **Operation-shape rule.** "remove / delete / clear" in the request but only value sets
  in the diff ⇒ flagged.
- **Acknowledge the anomaly, not the diff.** Today's `requires_code_ack` gate re-pointed:
  approval requires ticking each flagged row; normal rows need nothing.

**Residual.** The user ignores a red row. Back-translation (§2.4) stays as the catch for
what the rules cannot express.

### 5.2 File opened several times; large IFCs make the checkpoint slow

**Risk.** A 150 MB model takes ~15 s to open. Select opens it, mutate opens it again,
before-snapshot, after-snapshot, approval: five opens, most of them repeated parsing. A
checkpoint that costs 15 s gets switched off, and the early catch goes with it.

**Mitigation.**
- **Baseline `IfcSnapshot` cached per fingerprint**, computed at upload and after each
  commit. The benchmark runner already caches it per run; this makes it per file.
- **One sandbox child on the fast path.** Select and mutate run in the same subprocess with
  the file loaded once; the target list is emitted as an intermediate event. The second open
  happens only when a checkpoint trigger fires and the user has to answer.
- **Checkpoint evidence comes from the DB index**, no file open for display.
- **Approval re-uses the executor's temp copy** for the re-diff (§5.4): one open, not two.
- **Measure geometry hashing** on the largest project file before touching it; it is the
  likely hotspot, but that is a guess until timed.

**Residual.** Two opens per request, the same as today's Tier 3; a blocked checkpoint
costs one more. On 8/12 GB the request also pays one model swap for P3b.

### 5.3 The helper library becomes a second rigid schema

**Risk.** "Walls adjacent to the corridor" has no helper. If the prompt says "helpers
only", the model forces it into `elements_in_space("Corridor")` and returns the corridor's
own walls, confidently. V2's seven keys again, with fifteen.

**Mitigation.**
- **Helpers are vocabulary, not a format.** Plain functions in the ifcopenshell namespace
  returning plain entity lists; the prompt shows one helper + raw mix example.
- **Hard cap ~15.**
- **Evidence rule.** A helper enters only when the benchmark shows the same raw traversal
  failing in more than one case. Never because it seems useful.
- **Ratio tracked.** The benchmark records helper-only / raw-only / mix per select. A rising
  helper-only failure rate means the library is compressing requests; a rising raw-only
  failure rate means a helper is missing.

**Residual.** A request the model forces into a helper anyway; caught by the targets-match
column, not at runtime.

### 5.4 The propose-time diff is not the approve-time diff

**Risk.** Monday: propose, diff shows five walls. Tuesday: a colleague's approval adds a
sixth wall to Level 1. Wednesday: approve. The fingerprint refuses, correctly. Subtler:
nothing changed, fingerprint matches, but sync reads Monday's stored diff while the code,
if non-deterministic, wrote something else. The index drifts from the file silently.

**Mitigation.**
- **Re-diff at approval, always.** The executor already runs the code on a temp sibling
  before the atomic swap. Snapshot that copy, diff against the cached baseline, and feed
  *that* diff to the git commit, DB sync and Guardian.
- **The two diffs must match.** If the approval-time diff differs from the approved one,
  abort before the swap: "the code produced a different result than the one you approved".
  Non-determinism becomes a visible failure, not a drift.
- **`stale_policy="warn"` removed.** The user approved a specific diff against a specific
  file; a warn policy makes that approval meaningless. Abort only.
- **Rebase instead of re-propose.** On fingerprint mismatch, re-run the stored select +
  modify on the new file and show the fresh diff. When it matches the approved one within
  the allowed set, offer one-click re-approval. A queue of pending proposals no longer dies
  when the first is approved.
- **Guardian toggle.** A per-request on/off switch in `_modify.html`, default on, for
  changes the user knows are not in the documents or when there are none. The proposal
  records `guardian_skipped=True` so history and FMP evidence stay honest. Consistent with
  design decision 5: Guardian advises, never blocks, so skipping it changes speed, not
  safety.

**Residual.** Non-deterministic code that happens to produce the same diff twice; the FM
user who never looks at the diff.

### 5.5 Accepted without mitigation

- Maximal-authority safety story three weeks before the FMP deadline.
- Everything that passes today's 599 tests is not evidence for V3. Only the benchmark is.

## 6. Build order

Deadline 27 Sep, today 14 Sep. The bake-off decides the model, and the model decides prompt
size and `num_ctx`, so anything built before the bake-off risks being built twice.

```
week 1   P0 ground · P1 select · P2 mutate · P3a deterministic verify
         benchmark: ~20-prompt corpus subset with targets:/diff: lines, targets-match column
         → bake-off (7b / 14b / qwen3-coder:30b / qwen3:14b control / Claude ceiling)
week 2   P3b back-translation (Ask model) · card (aggregated diff, flagged rows, collapsed code)
         Guardian toggle · help modal · full corpus · FMP report re-argues minimal → maximal
```

The first half is the FMP evidence. The second half is what users need, and it can follow
the report if it must.

## 7. Open questions

- N for the "more than N targets" checkpoint trigger: 10, 20, or a share of the type's
  population?
- Snapshot cache location: sidecar file next to the IFC, or a column on `IFCFile`?
- Rebase (§5.4) in the first cut, or after the FMP?
