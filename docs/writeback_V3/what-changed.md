# Writeback V3 — What changed from V2

One table per phase, three columns: what V2 did, what V3 does, why. The V2 side links into
[`../writeback/`](../writeback/), which is unchanged and remains the record of V2. Read
[`overview.md`](overview.md) first if V3 is new to you.

## The one-sentence version

V2 tried to **understand** the request with a chain of small model calls and then executed
pre-coded handlers. V3 lets the model **write the change as code** and then **verifies what the
code did to the file**. Understanding is checked by outcome, not by intermediate structure.

## Phase by phase

### Understanding the request

| V2 | V3 | Why |
|---|---|---|
| Five stages: triage splits the sentence into segments, a slot extractor fills pset / property / value per segment, an entity resolver pins targets with up to three model turns, a deterministic tier router, an intent assembler builds the dict the validators read ([overview](../writeback/overview.md#pipeline-at-a-glance)) | Two stages: *ground* (no model) reads the file's storeys, spaces, type counts and property sets from the index and hands them over as exact strings; *generate* (one model call) writes a read-only `select` function and a `modify` function | Every V2 hand-off was a place for meaning to drift with nothing checking. V3 has no intermediate representation to drift; the selection is executed and its result inspected |

### Choosing the target entities

| V2 | V3 | Why |
|---|---|---|
| The model had to fit the request into a seven-key filter spec (type, storey, name pattern, GlobalIds, tag, description, property match) resolved by substring matching ([Tier 1 filters](../writeback/tier1-reference.md#filters)). "The internal doors" had no key | The model writes ifcopenshell code, composing eight thin helpers with raw api. Grounding injects the real storey / space / pset names so the code uses strings that exist. The targets are listed with evidence (type, container, one property) on the card | Targeting was the largest churn cluster in V2's history. Compression into a fixed schema and vocabulary guessing were the two causes; both are removed rather than patched |

### Deciding what to do

| V2 | V3 | Why |
|---|---|---|
| Router picks a tier; Tier 1 validator checks against pre-coded handlers, Tier 2 validator checks a plan, Tier 3 tries a closed set of typed operations first and only falls back to generated code ([tier 2](../writeback/tier2-reference.md), [tier 3](../writeback/tier3-reference.md)) | One path: the model writes `modify(model, targets)` in the same block as `select`. No tiers, no router, no validators | Three tiers meant three code paths, three test suites, and a router whose mistakes were invisible. The safety they bought is bought instead by the diff |

### Previewing the change

| V2 | V3 | Why |
|---|---|---|
| Preview rendered from the journal's own records; for generated code there was no preview at all ("effects are unknowable until the code runs") ([tier 3 UI](../writeback/tier3-reference.md#ui-presentation)) | Select and modify run on a scratch copy before the user sees anything; the preview is a real before/after diff of the file, and the scratch copy is kept | A preview built from what the model *said* it would do is a description, not evidence. The diff is evidence |

### Reviewing the change

| V2 | V3 | Why |
|---|---|---|
| A reviewer model read the generated code against the request and gave a verdict ([Tier3Reviewer](../writeback/tier3-reference.md#tier3reviewer--llm-code-review)) | A model reads the code **and the diff, without the request**, and writes one sentence on what the change does. The user compares that with what they asked. There is no verdict | A model reading code against the request shares the coder's blind spots and can parrot the request. Blind back-translation cannot parrot; it is the one check that targets "right entities, wrong operation" |

### The approval gate

| V2 | V3 | Why |
|---|---|---|
| For generated code, the user had to acknowledge the code block in a separate request before the approve button worked | Acknowledgement moves to flagged diff rows: a value that is not in the request, an entity added or removed. The ticks travel in the approve request itself; normal rows need nothing; the code is collapsed | Facilities users do not read Python. They do read "FireRating: EI30 → EI60 on 5 walls" and a red row beneath it |

### Executing the change

| V2 | V3 | Why |
|---|---|---|
| The journal was replayed per operation type by in-memory handlers, or for one code mutation by the sandbox; the sandbox trusted the code's own list of changes ([journal](../writeback/overview.md#the-mutationjournal)) | The scratch copy the user approved is swapped over the original after the fingerprint check. The code does not run again; the code reports nothing; the diff was computed by the harness | The code's self-reported changes were the model grading its own work. Swapping the reviewed file makes the approved diff and the applied diff the same bytes, with no second execution to drift |

### Updating the index and checking the documents

| V2 | V3 | Why |
|---|---|---|
| The database index was re-synced from the GlobalIds the code reported; Guardian built its document query from the intent JSON | Both read the stored diff. Guardian still runs when the proposal is made, so its verdict is on the card; it can be switched off per request, and the skip is recorded on the proposal | One source of truth for "what changed". The toggle exists because many changes concern things the documents never mention |

## Model calls per request

| Scenario | V2 | V3 |
|---|---|---|
| Single property, target given by GlobalId | 3 | 2 |
| Single property, named target | 4, up to 6 with resolver retries | 2 |
| Two properties in one sentence | 6 | 2 |
| Create or delete via typed operations | 5 to 6 | 2 |
| Create or delete via generated code | 7 to 8 | 2 |
| With Guardian | included above | 3 |

V2 counts include one Guardian call. V3 counts are generation and blind explanation; Guardian
is an optional third. Repairs add at most two calls to the generation step.

## Dropped, kept, adapted

**Dropped, roughly seven and a half thousand source lines and five and a half thousand lines of
their tests:** triage
classifier, slot extractor, entity resolver, tier router, intent assembler, Tier 1 and Tier 2
validators, Tier 3 operation planner, filter builder, FilterEngine, the model-backed half of the
hint generator, the mutation journal, its executor, its builder and its diff renderer, the Tier 3
reviewer. Their references stay readable in [`../writeback/`](../writeback/).

**Kept, untouched:** per-project git storage, the file fingerprint, the round-trip diff, the
sandbox process model and its forbidden-pattern and import guards.

**Kept, promoted:** the round-trip diff moves from a benchmark-only integrity score to the
centre of the pipeline. The proposal row becomes the record of what will run, replacing the
journal.

**Adapted:** the sandbox child runs select then modify and returns the diff instead of a
self-reported change list; the git commit takes a subject and a body instead of a tier label;
Guardian and the database sync read the diff; the proposal model gains code, targets, diff,
fingerprint and scratch path, and its V2 columns become nullable; the benchmark's routing column
becomes "targets match" and "diff matches"; the Modify page, its help modal and the progress
phases get the new vocabulary.

**New:** the eight-function helper library for selection, the hand-written api sheet, the one
diff flag rule, the kept scratch copy, the Guardian toggle.

## The safety story, layer by layer

V2 documented seven sandbox layers for generated code
([safety architecture](../writeback/tier3-reference.md#safety-architecture)). What happens to
each:

| V2 layer | V3 |
|---|---|
| 1 Forbidden pattern scan | kept |
| 2 Restricted globals and imports | kept |
| 3 File copy isolation | kept; the copy is now the artifact the user approves |
| 4 Timeout | kept |
| 5 Return value validation (the code's own change list) | **replaced** by the harness-computed diff; the code reports nothing |
| 6 Git snapshot before execution | kept |
| 7 Human reviews the code | **replaced** by human reviews the diff and the blind explanation; code available, collapsed |

Added on top: the selection becomes the allowed set and any change outside it is a violation;
one deterministic flag rule with per-row acknowledgement; fingerprint at approval with abort on
mismatch. The honest framing for the report: minimal authority was the V2 safety argument, and
V3 replaces it with maximal verification. That has to be argued, not swapped silently.

## Models

| | V2 | V3 |
|---|---|---|
| Modify | the same general model as Ask | a code-tuned model, sized to the graphics memory tier: 7B on 8 GB, 14B on 12 GB, 30B on 24 GB |
| Ask | prose model | unchanged |
| Explanation of a change | the Modify model | the Modify model; the Ask model with a one-line change if the bake-off shows the coder's sentence is poor |
| How the default is chosen | by hand | by the benchmark bake-off, scoring targets match, diff match and integrity per model |
