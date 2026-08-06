# Evaluation — Natural-Language Write-Back Benchmark

**Date:** 2026-08-05
**Subject:** First automated evaluation of the Modify pipeline against real natural language
**Result:** A silent-substitution failure found and fixed; comprehension 53/91 → 64/91, faithful execution 53/53

This is a dated record of one evaluation. It is not updated as the system changes — later runs get their own file. Part A is written for a non-specialist reader; Part B is the evidence behind it.

---

# Part A — What this demonstrates

*Conceptual register. No implementation detail — see Part B for that.*

## The gap conventional testing leaves

A language-driven system is normally tested by substituting a scripted model response for the real one. This makes tests fast and repeatable, and it verifies that the surrounding machinery behaves correctly for the inputs the test author thought to write down.

That is also its limit. A scripted response is one the author already imagined. The failures that matter in a natural-language system live in the gap between what a person actually said and what the system understood them to mean — and a test that supplies the understanding cannot observe that gap. Every test can pass while the system misreads its users.

Castor's write-back path had been validated this way, together with manual trials through the interface. Both were necessary. Neither could answer the question that matters to a user: *given a sentence I would really type, does the system do what I asked?*

## The method

An automated evaluation was built around a corpus of 92 written requests, each annotated in advance with the outcome the architecture says it should produce. The corpus covers every operation the system offers and every category of request it is expected to refuse. Each request was put through the complete pipeline against a live model and a real building model, and the resulting change was applied to a disposable copy of that model.

The decisive design choice was to measure **two different things separately**:

- **Comprehension** — did the system decide to do what the request asked for?
- **Faithful execution** — did the building model afterwards actually say what the system claimed it would?

Keeping these apart is what made the central finding visible. Had they been combined into a single score, the result would have read as a modest, unremarkable pass rate, and the failure below would have been averaged into invisibility.

## The finding

Faithful execution was effectively perfect throughout: when the system decided on a change, that change reliably reached the file. Comprehension was materially weaker. The two measurements pointed in different directions, and the disagreement was the signal.

The most serious case: for requests asking to **remove** a piece of information, the system instead **wrote a new value** into the very field the user asked to clear — and the value it wrote was a fragment of the user's own sentence. A request to clear a reference field across a set of walls resulted in every one of those walls being labelled with the phrase the user had used to describe them.

Every safeguard reported success. The request was classified, validated, previewed to the user as a legitimate change, approved, written and recorded in the audit history as a completed modification. Nothing in the system was in a position to notice, because at each step the operation was internally consistent — it was simply not the operation that had been requested.

The cause was structural rather than a matter of model quality. The internal representation the system uses to describe a requested change had no way to express *removal*. It could describe which field, on which objects, and what new value to write. Faced with a removal request and a representation that demanded a value, the model supplied the closest thing at hand.

## Why this matters beyond one defect

Three points generalise:

**A system that cannot represent an intent does not refuse it — it substitutes.** The failure was not the model inventing something from nothing; it was the model resolving an impossible request into the nearest possible one. Wherever a constrained representation stands between a user and a system, this pressure exists. The remedy is to make the intent expressible, not to instruct the model more firmly.

**Documented capability is not evidence of working capability.** The user-facing help described this exact operation, distinguished it carefully from a related one, and offered a worked example the user could click. The documentation described the intended design faithfully. The implementation could not deliver it, and the two had never been checked against each other.

**Silent wrong answers are more dangerous than visible failures.** A crash is self-reporting. This defect produced a plausible, well-formed, approved, version-controlled change — the audit trail records it as a success. Any system that writes to a shared source of truth on a user's behalf needs a check that compares the *result* against the *request*, not merely against its own intermediate reasoning.

## Outcome

The representation was corrected so that removal is expressible, and a second class of defect surfaced by the same run — a request that passed validation and then failed during execution — was moved to an early, explanatory refusal.

Re-running the identical corpus confirmed the repair: twelve previously failing requests now behave correctly, none regressed, and faithful execution became complete. Comprehension improved from 53 to 64 of 91 scored requests.

The remaining shortfall is not yet a measured deficiency. Roughly a quarter of the original failures turned out to be errors in the *expectations*, not the system — they had been written by reading the design rather than by running it, and were wrong about the system's own contract. The rest await case-by-case judgement. This is stated plainly because the honest reading of a first evaluation is that it calibrates the instrument as much as the system.

---

# Part B — Evidence

*Engineering register. Every figure is taken from the stored run artifacts, not from notes.*

## Method

| | |
|---|---|
| Harness | `manage.py benchmark_writeback` (see [testing.md](../testing.md) §Natural-Language Benchmark) |
| Corpus | `fixtures/benchmark/pipeline-test-prompts.txt` — 92 prompts, 19 sections |
| Building model | `fixtures/benchmark/Ifc4_SampleHouse.ifc` (public buildingSMART sample, IFC4) |
| Model under test | `llama3.1:8b`, local Ollama, temperature 0.1 |
| Execution | Each case runs against a fresh copy of the IFC in a temporary directory; the project file is never modified |

Expected-outcome distribution across the corpus: 32 rejections (tier 0), 34 tier 1, 15 tier 2, 11 tier 3. One case is marked advisory (its outcome is fixture-dependent) and is excluded from scoring, leaving 91.

**Two scores, deliberately separate:**

- **Understanding** — routed tier and operation versus the corpus's declared expectation. Model-dependent.
- **Fidelity** — the executed journal re-read from the written file, per mutation. Model-independent; a drop indicates a writer or executor defect, not a comprehension failure.

## Results

| Run | Started | Understanding | Fidelity | Errors | Median | p90 | Tokens in/out |
|---|---|---|---|---|---|---|---|
| Baseline | 03:35:37 | 53/91 | 53/54 | 0 | 15.8s | 22.6s | 296,443 / 10,624 |
| After fixes | 04:22:34 | **64/91** | **53/53** | 0 | 15.4s | 21.0s | 303,606 / 10,841 |

**Baseline diff: 12 FIXED, 0 REGRESSED.** Fixed cases: 6.1, 6.2, 6.3, 6.4, 7.1, 7.3, 7.4, 8.1, 8.2, 8.3, 8.4, 9.3.

Fidelity denominators differ (54 → 53) because case 5.5 no longer reaches execution — it is now refused at validation, which is the intended repair.

## Defect 1 — removal intent unrepresentable (silent data corruption)

The headline case, verbatim from the artifacts:

```
"remove Reference from all walls"
  before -> SET_PROPERTY: Pset_WallCommon.Reference = 'all walls'   (x5 walls, fidelity 5/5)
  after  -> REMOVE_PROPERTY: Pset_WallCommon.Reference removed
```

```
"remove ExtendToStructure from wall :285330"
  before -> SET_PROPERTY: Pset_WallCommon.ExtendToStructure = True
  after  -> REMOVE_PROPERTY: removed
```

**Root cause.** `PropertySlots` carried `{pset, property, value}` with no operation field, and `tier_router._route_tier1` hard-returned `SET_PROPERTY` for every PROPERTY segment. `REMOVE_PROPERTY` existed in `MutationOp`, in `Tier1Validator` and in the writer, but no stage could emit it — the operation was unreachable. With a schema demanding a value, the model populated it from the target phrase.

**Why every safeguard passed.** Routing was valid, the journal was well-formed, execution was faithful, and fidelity scored 5/5 — the journal did exactly what it said. Only comparison against *declared intent* could catch it.

**Fix.** An `operation` slot (`SET` | `REMOVE`) on the PROPERTY kind, honoured by the router. `ADD` is deliberately absent: `SET_PROPERTY` is an upsert on standard psets, so a separate add would only create a path for "add X" to fail when X already exists. A removal is genuinely a different operation. The finalizer discards a value on a REMOVE even if the model supplies one, and an omitted or unrecognised operation defaults to `SET` — never to the destructive member.

**User exposure.** The Modify help modal shipped a "Remove a property" card badged `REMOVE_PROPERTY`, a clickable example ("Remove the FireRating property from all internal walls"), and a callout distinguishing it from `REMOVE_PSET`. The documentation was correct; the pipeline could not honour it.

## Defect 2 — safe-listed attribute absent from the class

```
5.5  "set LongName to ... on window :286105"
  before -> execution failed: AttributeError: 'IFC4.IfcWindow' has no attribute 'LongName'
  after  -> refused: 'LongName' is not an attribute of IfcWindow. Try a property instead,
            or one of: Description, Name, ObjectType, Tag
```

`LongName` is in `SAFE_ATTRIBUTES` but is declared only on spatial elements and type objects. The request validated, built a journal, and raised mid-execution after approval. The pre-existing writer guard used `getattr(element, attribute, None)`, which never raises for a missing attribute — it was dead code.

**Fix.** `Tier1Validator._types_without_attribute` answers from IFC schema introspection rather than a hand-maintained table, so it stays correct for every class and both schema versions; it fails open if introspection errors. A real `hasattr` guard in `Tier1Writer.set_attribute` provides defence in depth.

## Triage of the 39 baseline failures

| Count | Category |
|---|---|
| 10 | **Wrong expectations** — the corpus was authored by reading code and had never been executed |
| 3 | **Real defects** — 2 removal cases, 1 execution crash |
| 26 | **Unadjudicated** — need case-by-case judgement |

The ten corpus errors were mostly a single misconception: a tier 2 proposal's operation is always `PLAN`; the step name lives inside the plan. Two expected `ADD_PROPERTY` where `SET_PROPERTY` is upsert.

## Remaining failures after the fixes

27 cases, by section: 2 (1), 5 (1), 7 (2), 10 (5), 11 (3), 12 (3), 14 (5), 15 (1), 16 (3), 17 (1), 18 (2). Predominantly rejection-wording drift (section 14), expected-write-got-rejected (11, 12, 17), and tier 3 routing judgement (10).

## Caveats

1. **Single-pass runs.** Temperature is 0.1, not 0. Some of the 27 may be sampling noise. `--repeat N` reports a pass rate and is the way to separate genuine failures from flaky ones before investing in them.
2. **64/91 is not a comprehension benchmark score.** It is one model, one corpus, one run, against expectations that are themselves still being corrected. It is meaningful as a *before/after* on identical inputs; it is not a general capability measure.
3. **One model only.** No cross-model comparison was run. The harness supports a sweep (`--model`), so this is available but not yet evidence.
4. **Corpus expectations remain partly unvalidated.** Ten were demonstrably wrong at baseline. Others in the unadjudicated 26 may be too.

## Reproduction

Run artifacts are in `runs/` (gitignored — every figure above is reproduced in this document precisely because the artifacts are not committed). See [testing.md](../testing.md) §Natural-Language Benchmark for the commands, project setup and safety model.

## Consequences recorded elsewhere

- The general lesson is entry 3 in [known-limitations.md](../known-limitations.md) — an intent absent from a slot schema gets substituted, not refused.
- The SET/REMOVE asymmetry is documented as a durable contract in [writeback/tier1-reference.md](../writeback/tier1-reference.md).
