# Known Limitations

A running log of observed reliability limits when running Castor on local
infrastructure. Entries are written so they can be lifted into the FMP
defence write-up: each one leads with a conceptual symptom and *why it
matters*, then drops to concrete evidence (verbatim prompts and observed
model output) and finally to the mitigations in place plus the residual
risk no code can absorb.

This document is **not** a bug tracker. Entries here describe behaviours
that are inherent to the chosen architectural constraints (local-first
inference, small open-weight models, an unconstrained natural-language
front door) — not defects that can be patched away. Each entry should
make clear what *is* recoverable in code, what is *not*, and which
choice the user can make to mitigate (e.g. switch model, rephrase, or
accept reduced reliability).

---

## 1. Small-model JSON schema drift on structured-output tasks

### Symptom

When the write-back classifier asks a small open-weight model to map a
free-text user request to a structured intent (a JSON object with a
fixed schema), models in the 7–8B parameter range occasionally produce
JSON that is *syntactically* valid but *semantically* misshapen: keys
are renamed, the payload is wrapped in extra layers, required fields
are silently dropped, or the model invents synonyms that look reasonable
but are not part of the agreed schema. The same model, given the same
prompt, may produce a different shape on every call.

Larger open-weight models in the 14B+ range exhibit the same drift far
less often. Closed frontier models effectively never drift.

### Why it matters

Castor is committed to local-first inference (see `docs/specs/llm-connection.md`
and CLAUDE.md §"Key Design Decisions"). That commitment trades operating
cost, privacy and offline use for a tax on inference reliability — the
locally-runnable models that are tractable on consumer hardware are the
ones that drift. Documenting the exact failure surface is therefore a
load-bearing part of the FMP discussion: a system that can only meet its
quality bar by calling out to a frontier API is not a local-first system.

### Concrete example

Failing prompt (verbatim, observed in production):

```
change firerating to EI240 of wall: IfcWall Basic Wall:Wall-Ext_102Bwk-75Ins-100LBlk-12P:285330
```

Three consecutive runs against `llama3.1:8B`, same prompt, same
classifier system prompt, temperature 0.1, JSON mode enabled:

**Run A** — model omits the top-level `tier` key entirely:

```json
{
  "operation": "ADD_PROPERTY",
  "filter": {"ifc_type": "IfcWall", "name_pattern": "*Basic Wall:...*"},
  "pset": "Pset_WallCommon", "property": "FireRating",
  "new_value": "2 hours", "confidence": 0.9
}
```
Note also that the model substituted the user's literal value `EI240`
with `"2 hours"` — a unit conversion the system prompt explicitly
forbids ("Value Preservation — STRICT").

**Run B** — model invents its own schema, nesting everything under
ad-hoc wrapper keys:

```json
{
  "intent":       {"type": "modify", "entity_type": "IfcWall",
                   "instance_id": "Basic Wall:..."},
  "modification": {"property_set": "Pset_WallCommon",
                   "property_name": "FireResistanceDuration",
                   "operation": "update", "value": "120"}
}
```

The model has used `property_set`/`property_name`/`value` instead of
the schema's `pset`/`property`/`new_value`, used the verb `update`
instead of one of the four canonical operations, and split the targeting
across two parallel objects. It has *also* renamed the property from
`FireRating` (a real, standard pset property) to the made-up
`FireResistanceDuration`, and re-cast `EI240` as `"120"` (presumed
minutes).

**Run C** — model drops the user's target value entirely:

```json
{
  "attribute_name": "FireResistanceRating",
  "new_value": null,
  "pset": "Pset_WallCommon",
  "filter": {"ifc_type": "IfcWall", "name_pattern": "*Basic Wall:...*"}
}
```

The same prompt against `qwen3:14b` produced a clean Tier-1 SET_PROPERTY
intent with `new_value: "EI240"` on every observed run.

### Why this prompt is hard

The wall identifier `Basic Wall:Wall-Ext_102Bwk-75Ins-100LBlk-12P:285330`
contains colons, hyphens, embedded numerics with letters, and a numeric
suffix that resembles an internal expression ID. Small models lose
focus partway through a hostile target string and start producing
inconsistent structured output around it. This is reproducible with
any IFC name following Revit's typical naming convention.

### Mitigations in place

- **Narrow per-stage prompts** (`writeback/services/{triage_classifier,slot_extractor,entity_resolver}.py`).
  Each LLM stage sees only the slots it needs (kind / target+value
  phrase / per-kind slot dict / entity reference). Drift in one stage
  is contained and produces a localised rejection rather than
  corrupting the whole pipeline. The `tier_router` then picks the
  execution tier deterministically with no LLM involvement.
- **Groundedness guard.** When the model substitutes a value the user
  never typed (e.g. `2 hours` for `EI240`), the proposal is annotated
  with a visible warning and its displayed confidence is capped at 50%.
- **Value-drop short-circuit.** When the model emits `new_value: null`
  or an empty string, the intent is downgraded to a Tier-0 rejection
  with an actionable message asking the user to rephrase. A nonsense
  proposal ("set FireRating to None") is never rendered.
- **Operation auto-fallback for missing standard properties.** Small
  models routinely pick `SET_PROPERTY` for properties that don't yet
  exist on the matched entities (the correct operation in that case is
  `ADD_PROPERTY`). When validation rejects a `SET_PROPERTY` with
  "property not found on any matched entity" *and* the requested
  `(pset, property)` is a standard IFC pset property, the operation is
  silently rewritten to `ADD_PROPERTY` and re-validated. This fallback
  is applied at both Tier 1 and Tier 2 — the small-model failure mode
  recovers identically whether the LLM produced a single-operation
  intent or a multi-step plan.
- **Tier 1 validation error forwarded as a Tier 2 planner hint.** When
  Tier 1 validation fails on "property not found", the error message —
  including the validator's "Did you mean: …" suggestion — is forwarded
  into the Tier 2 planner's prompt under a `Tier 1 Feedback` section.
  This gives the planner a chance to correct a hallucinated property
  name (e.g. `FireResistanceDuration` → `FireRating`) instead of
  re-producing the same mistake blind.
- **GUID-targeting fields lifted into `filter.global_ids`.** When a
  small model emits a top-level `entityGuid` (or `globalId` / `guid` /
  variants) instead of nesting it under `filter.global_ids`, the repair
  pass lifts the value into the canonical filter shape. Without this,
  the structural validator would crash on a missing `filter` field even
  though the model unambiguously targeted a single entity.
- **Diff-style `properties` dict, with safe-vs-unsafe split.** Some
  small-model runs nest the change as `properties: {Prop: value}` or
  `properties: {Prop: {old_value, new_value}}` instead of using flat
  `property` / `new_value` fields. Single-entry dicts are promoted to
  the canonical shape (the existing groundedness guard then catches any
  value substitution). Multi-entry dicts — observed when a hostile
  entity name causes the model to hallucinate several unrelated
  property changes for a single user request — are downgraded to a
  Tier-0 rejection with a clear message. Silently picking one of the
  hallucinated changes would propose the wrong operation; refusing is
  safer than guessing.
- **Pre-LLM entity-name resolution
  (`writeback/services/entity_resolver.py`).** The largest structural
  mitigation. Before the main classifier runs, a focused extraction LLM
  call identifies the user's entity targeting (specific name / all of
  type / filtered) into a four-field JSON schema. The result is looked
  up against the database, and on success the matched entity's
  `global_id` is pinned into `filter.global_ids` *before* the classifier
  is invoked — so even if the classifier produces a malformed filter,
  the proposal lands on the correct entity. The architectural shift
  shrinks the classifier's per-call job from "do everything in one
  shot" to "decide operation/property/value over a narrowed set," and
  the empirically-stable parts (entity extraction) move into a smaller
  call where they can stay reliable. Strict no-regression: when the
  extraction LLM drifts or finds nothing, the resolver returns empty
  and the pipeline runs exactly as before. When ambiguous matches
  arise, the classifier is allowed to disambiguate among the candidate
  set; if it can't, the user gets a clear "be more specific" message
  instead of a wrong proposal.

### Residual risk

No amount of post-hoc parsing can recover from the cases where the
model silently changed semantic content — the substituted value, the
renamed property. The groundedness guard catches the substituted-value
case at warning level but cannot tell the user *which* value the model
hallucinated. A renamed property cannot be detected at all without a
domain-aware second pass.

### What to do

- For demos and FMP screen recordings, prefer `qwen3:14b` or larger
  for the write-back path. Reserve `llama3.1:8B` for the Ask path,
  where output is free-form and drift is far less consequential.
- The clean long-term fix is JSON-schema-constrained generation
  (Ollama's `format` parameter accepting a JSON Schema, not just
  `format: "json"`). This makes the model physically incapable of
  emitting off-schema output and removes the entire repair-pass family.
  Tracked as future work.

---

## 2. Ollama client does not honour a real wall-clock timeout

### Symptom

The LangChain `ChatOllama` client accepts a `timeout` setting, but it
is applied per-network-read, not as a wall-clock cap on the call. A
local model that gets stuck in long-running generation (very large
`num_predict`, or a pathological prompt that triggers near-loops) will
hang the request thread well past the apparent timeout, blocking the
user-facing request and any downstream pipeline phases.

### Why it matters

The promise of local-first inference is that the user sees responses
without leaving their machine — but if "without leaving the machine"
also means "without a stop button," the UX collapses on the first long
generation. Acceptance of bounded latency is part of the local-first
contract.

### Mitigation in place

`ChatOllama.invoke()` is wrapped with a `ThreadPoolExecutor.result(timeout=N)`
so a real wall-clock deadline is enforced. `num_predict` is also bounded
on the model side. The two together produce a hard upper bound on call
duration regardless of what the underlying client does.

### Residual risk

A killed thread leaves the Ollama server-side generation running until
it finishes naturally. On constrained hardware this can pile up if the
user retries quickly. There is no clean way to cancel a running Ollama
generation from the client side; the workaround is to rate-limit retries
in the UI, which we do.

---

## How to add an entry

When you observe a reproducible failure that is **inherent to the
chosen architecture or model class** (not a fixable defect), add a new
section here. Use this skeleton:

```
## N. Short conceptual title

### Symptom
What the user sees. One paragraph.

### Why it matters
Connect it to a Castor architectural commitment (local-first, RSAA,
file-as-source-of-truth, etc.). One paragraph — defence-panel readable.

### Concrete example
Verbatim prompt or input. One or more verbatim model outputs.
Identify the model + version. Note any factor that makes the input
hostile (here: the wall name contained colons + numerics + hyphens).

### Mitigations in place
What the codebase does about it today. Link to the file that does it
in `app/path/file.py:line` form when useful.

### Residual risk
What still leaks through. Be explicit — do not pretend mitigation is
total when it isn't.

### What to do
The operational guidance: "switch model X for path Y", "rephrase as Z",
or "accept the reduced reliability and move on."
```

Two rules that keep this document useful:

1. **No fixed defects.** If a problem can be patched away, fix the code
   instead and write a unit test. This file is for the failure modes
   you cannot eliminate — only manage.
2. **Concrete evidence required.** Every entry must include at least
   one verbatim prompt and at least one verbatim observed output.
   Anecdote without evidence drifts toward folklore.
