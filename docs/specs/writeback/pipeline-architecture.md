# Writeback Pipeline — Architecture Summary

This is a concept doc, not an inventory of files. For implementation details read the source under `src/writeback/services/`.

## Design goal

Each LLM call has a narrow, well-defined job. Execution-tier selection is deterministic. Each failure mode degrades gracefully into a localised rejection. The project runs Ollama-local per CLAUDE.md §1, and small open-weight models drift on polymorphic schemas — so the pipeline keeps every per-stage prompt small and shape-stable.

## Stages

```
user message
   │
   ▼
TriageClassifier (LLM #1)          — splits into action segments
   │                                 [{kind, target_phrase, value_phrase}]
   │                                 kinds: PROPERTY | ATTRIBUTE | PSET |
   │                                 CREATE | DELETE | RELATIONSHIP |
   │                                 OUT_OF_SCOPE | UNCLEAR
   ▼
SlotExtractor (LLM #2 per segment) — per-kind narrow prompt fills a small
   │                                 fixed slot set; groundedness guard
   │                                 fires per-segment. PROPERTY carries an
   │                                 `operation` (SET | REMOVE); RELATIONSHIP
   │                                 carries a `destination_phrase`
   ▼
EntityNameResolver (LLM #3)        — mode-aware:
   │                                 EXISTING_TARGET / PARENT_TARGET /
   │                                 NEW_TARGET / NO_TARGET
   ▼
tier_router.route()                — DETERMINISTIC, NO LLM
   │                                 single-segment + standard pset → T1
   │                                 single-segment + custom pset → T2
   │                                 multi-segment (any combination)  → T2
   │                                 CREATE / DELETE / RELATIONSHIP   → T3
   │                                 OUT_OF_SCOPE / UNCLEAR           → T0
   ▼
intent_assembler                   — builds the intent dict the validators
   │                                 consume
   ▼
T1 / T2 / T3 dispatchers           — validate, then build ONE artifact
   ▼
MutationJournal                    — the single write IR for every tier
   │                                 (see the next section)
   ▼
render_diff()  →  user approves  →  JournalExecutor  →  git commit  →  DB sync
```

Validators downstream retain veto power: a Tier 1 routing can still escalate to Tier 2 inside `Tier1Validator` when the property does not exist on the matched entities (the `SET_PROPERTY → ADD_PROPERTY` auto-fallback). The router picks the *initial* tier; the validator has the final say.

## One artifact: the MutationJournal

Every tier converges on a `MutationJournal` — an immutable, serializable list of typed mutations pinned to a SHA-256 fingerprint of the file it targets. This is what makes the preview and the write the same thing: `render_diff()` renders the journal, the user approves that journal, and `JournalExecutor` replays exactly it.

Execution is a deferred bake: mutations are applied to a temp copy beside the original, and only a successful run ends in an atomic `os.replace`. Any failure leaves the original byte-identical. A fingerprint mismatch at apply time means the file moved under the proposal, and the journal is refused rather than applied to a model it was not built against.

The op set is closed. Tier 3 chooses *which* pre-coded operation to run rather than authoring the calls, and generated code survives only as a `RUN_CODE` escape hatch for what the typed ops cannot express — which is the one path that still requires a human code review.

## Key properties

- **Number of LLM calls per request**: 2 + 1 per segment (Triage + Resolver + N×SlotExtractor), plus one op-planner call for Tier 3 and one more if it falls back to code generation.
- **Tier choice**: Deterministic policy table.
- **Schema**: Narrow per-stage schemas; each prompt fills only what it needs.
- **Small-model drift repair**: ~20-30 lines per stage extractor; drift is contained per-stage, not global.
- **CREATE / DELETE / RELATIONSHIP**: First-class triage kinds; mode-aware resolver (`NEW_TARGET` skips DB lookup; `PARENT_TARGET` resolves the spatial parent only).
- **OUT_OF_SCOPE rejection**: Triage emits an `OUT_OF_SCOPE` segment with a reason; router rejects T0 with that reason verbatim.
- **Vague-request rejection**: Triage emits an `UNCLEAR` segment with a `missing` list; router rejects T0.
- **Multi-step requests**: Multi-segment → Tier 2 plan assembled deterministically; one proposal, atomic execution. The router never produces a Tier 1 chain because `ModificationProposal.message` is unique per row.
- **Pset inference when user omits a pset**: Deterministic registry lookup — `Pset_<Type>Common` first, else applicable psets, else broad scan.

## Rejection hint generator

T0 rejections from the router run through a `HintGenerator` (`writeback/services/hint_generator.py`) before the user-visible message is composed. Three strategies are tried in order; first non-empty wins:

1. **Templated** — static per-category template. Zero cost. Covers OUT_OF_SCOPE (geometry vs query-via-Modify-tab routing), UNCLEAR (per missing-slot-set guidance).
2. **Registry-grounded** — fuzzy-match the user's wording against `STANDARD_PSETS` properties or `IFCEntity.name` rows. Zero cost, can't hallucinate. Surfaces "did you mean `Pset_WallCommon.FireRating`?" for typos like "FireResistanceDuration", and "did you mean `Windows_Sgl_Plain:1810x1210mm:286105`?" for entity-name near-misses. Gated by a 3-char alphabetic prefix match to keep false friends ("Inspector" vs "Spectrum") out.
3. **LLM-fallback** — narrow LLM call, gated by five guardrails: master flag (`WRITEBACK_HINT_LLM_FALLBACK`), category whitelist (`WRITEBACK_HINT_LLM_CATEGORIES`, starts empty), 2 s hard timeout, registry validation of the suggestion, cache. The whitelist is empty by default — Strategy 3 is plumbing-in-place, not active behaviour. Add categories to `WRITEBACK_HINT_LLM_CATEGORIES` only after observing a real rejection that 1+2 can't address.

The hint strategies are orthogonal to the writeback Tier 1 / Tier 2 / Tier 3 RSAA execution pipeline — they only run after the router has chosen Tier 0 (REJECT) and never produce a proposal or modify the IFC.

## Open follow-ups

Non-blocking refinements; tracked separately:

- **Custom-property requests with no pset named** (e.g. *"set Inspector to TBD on all walls"*) currently route to a T0 rejection asking the user to name the pset. The router declines to guess. **Open question:** should it instead route these to T2 with a default pset name like `Pset_Custom`, or keep the explicit-rejection UX? The current behaviour is by design; if it is wrong, the fix is one branch in `tier_router._maybe_infer_pset` and one branch in `route()`.
- **`ModificationProposal.message` unique-per-row constraint** is what forces multi-segment routing to T2. If we ever want true Tier 1 chains (multiple proposals per chat message), the constraint has to come off via a migration first.
- **The T3 code-generation prompt is not narrowed.** Tier 2 plans are built deterministically by `intent_assembler` — the LLM planner that once wrote them has been deleted. Tier 3 now prefers typed ops, but the `RUN_CODE` fallback prompt still contains polymorphic instructions; narrowing it would further reduce small-model drift but is not load-bearing, because every request the typed ops can express never reaches it.
- **The prompts have been validated against a single small Ollama model.** Drift on a different model would surface at the per-stage level (one stage, not the whole pipeline) and is fixable by editing that stage's prompt.
- ~~**Mixed-type "all X and all Y" target phrases**~~ — **observed working 2026-08-05.** "add Pset_FireCompliance to all walls and all windows" resolved the union and produced 4 verified mutations across 3 walls and 1 window in the benchmark run. The note above described a failure that no longer reproduces; no split-on-"and" pre-pass was needed. See [the evaluation record](../../evaluation/2026-08-05-writeback-nl-benchmark.md).
