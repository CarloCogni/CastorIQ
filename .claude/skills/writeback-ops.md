# Skill: Write-Back Operations (V3)

Read this before creating or modifying ANY code in the writeback/ app.
Contract: `docs/writeback_V3/spec.md`. Plain-language explanation: `docs/writeback_V3/overview.md`.

---

## Core Rule: Maximal Verification

The model always writes a small piece of IfcOpenShell code. Safety is not in limiting what the
model may do; it is in checking, deterministically, what the code **did** to a scratch copy of
the file before a human sees it. There are no tiers, no router, no validators.

```
ground → generate → run → verify → (human approval) → approve
 no LLM    LLM #1   child   diff +     card            fingerprint check,
                   process  LLM #2                     scratch swap, git, index
```

Two model calls per request (code, blind explanation). Guardian is a third, optional, at
proposal time.

---

## The generated block

One fenced Python block defining exactly these two functions:

```python
def select(model) -> list:
    """Return the target entities. Read-only."""

def modify(model, targets) -> None:
    """Change the targets and only the targets."""
```

- `select` may use the eight `castor_select` helpers or raw ifcopenshell. Returning one
  entity instead of a list is accepted (the child wraps it). Two worked examples ship in the
  prompt: storey → type → name, and a bare reference with no storey or space (by Name via
  `by_name`, or by GlobalId via `model.by_guid`) — grounding never lists per-entity names or
  GlobalIds, by design, so a request naming only one of those is not declined for it (C-6,
  *review 8*).
- `modify` receives `select`'s result; it may narrow it, never widen it. Widening is caught
  by the scope check, not by prompt wording.
- The prompt's example and creation recipe are written in the sheet's notation with
  placeholder names; the example's pset variable is `own`, never `pset`, because a local named
  after an api module shadows it (the sandbox error says so).
- The pset recipe in the prompt is `pset.add_pset` (find-or-create **by name**), then
  `pset.unshare_pset` when `element.get_elements_by_pset(pset)` holds more than one entity,
  then `pset.edit_pset`. `add_pset` returns a pset shared by several entities as is, so
  without the unshare step the edit changes every entity on it (a scope violation on all but
  the target; the sample house shares psets across 20 curtain-wall members). Never
  `get_pset(...)["id"]` → `by_id`: when only the type carries the pset, that id is the type's
  pset and the edit changes every sibling.
- Generated code never calls `model.write()` or `ifcopenshell.open()`; the harness owns the file.
- Output is a fenced block, **not JSON**. `writeback/services/generator.py:extract_code_block`
  pulls it out and checks both functions exist with `ast`.

## Helper library — `ifc_processor/services/castor_select.py`

Eight read-only names, each ≤ 5 lines over `ifcopenshell.util.element` / `util.selector`:
`elements_in_storey, elements_in_space, by_type, by_name, by_pset_value, by_material,
decomposition_of, container_of`. No Django import (the sandbox child runs without Django).
The storey/space helpers return elements only (no spaces, no openings). **The first docstring
line of each helper is rendered into the prompt verbatim**: keep it exact.
Growth rule (module docstring): add a helper only when a benchmark case fails on the raw
traversal twice.

## Grounding — `writeback/services/grounding.py`

A lookup, not a retrieval. Injects as exact strings: every storey (name, elevation), every
space (name, storey; capped at 200), type counts, and for the types the request names each
pset with the property names the index holds plus, on standard psets, the standard
properties **not yet set** (`schema_data.lookup.properties_of`, capped at 16 per pset). A
requested property the file lacks must be an exact string in the prompt, or the model
writes the value into the nearest listed property. **Exactly one match happens in grounding**: lowercase substring of the request's
words against type names with `Ifc` stripped, used only to pick which types get a pset list.
"First floor" is resolved by the model reading the storey strings, never by a matcher.
Entity counts state a supertype/subtype split when a type's direct parent is also indexed
(`IfcWall: 1 direct, plus 56 IfcWallStandardCase (subtype of IfcWall); model.by_type('IfcWall')
returns all 57`), so a plain count is never mistaken for "no entities of the supertype exist"
and the model is told never to chain `by_type` to narrow a supertype to a subtype it already
returns (*review 8*). Materials are not grounded: the index carries no material data
(`parser.py`, out of scope), so a material request is answered from the request text alone —
deferred, not decided (*review 8*).

## The api sheet — `writeback/services/api_sheet.py`

A hand-written module constant (~40 lines): signatures + one-line docstrings for the pset,
root, spatial, type, attribute, classification, aggregate, material and group api modules. No
example values (examples get copied). A test resolves every named function against the installed
ifcopenshell. **Its notation is the calling convention:** the sandbox child binds every module
the sheet names with the model implied (`code_sandbox.API_MODULES`, `bound_api_modules(model)`),
so `spatial.assign_container(products=[e], relating_structure=s)` runs as written; the long
`ifcopenshell.api.run(...)` form also works. `type` is bound and doubles as the builtin. A new
sheet module must be added to `API_MODULES` (a test enforces it). Creation is `root.create_entity`
(an IfcZone included); `model.create_entity` is a forbidden pattern.

## Run — the sandbox child

`ifc_processor/services/code_sandbox.py:run_code_subprocess(scratch_path, code)` spawns
`_sandbox_child.py`: open scratch → snapshot (`IfcSnapshot.from_model`) → exec block →
`targets = select(model)` → `modify(model, targets)` → snapshot → `diff_snapshots` →
`model.write(scratch)` → `{targets: [GlobalIds], diff: IfcDiff.as_dict()}`.

The scratch copy is `.<stem>.proposal-<uuid8>.ifc` beside the original and is **kept** for the
proposal's lifetime. Apply, reject and supersede delete it. Any exception after the copy exists
(cancellation, no-change, a crash) deletes it in `_attempt`; a cancellation during Guardian
rejects the row, because a row with no message can never be superseded. `sweep_scratch_files`
cleans up after crashes. With zero targets the child skips modify, the second snapshot and
the write.

Sandbox guards kept from V2: forbidden-pattern scan, restricted builtins (`getattr` and
`hasattr` are allowed: attribute access is unrestricted anyway), import whitelist (now including
`castor_select`), wall-clock timeout (`budget_for`: a base plus a per-MiB share, because open +
two snapshots + write scale with the file). There is no self-reported change list.

## Verify — `writeback/services/verifier.py`

All deterministic, on `diff.as_dict()`:

- **Scope (V-1):** `IfcDiff.unexpected(allowed=targets, allow_population_change=True)` must be
  empty. Geometry and schema changes are always violations. A violation is a repair. The
  snapshot reads each entity's **own** psets and materials (`should_inherit=False`), and the
  relationship values (container, parent, materials, classifications, groups, type object) on
  occurrences **and** type objects: a type edit, a material or a classification on a type is
  one row on the type, never a violation on its occurrences. A **new** object attached to a
  pre-existing entity that `select()` did not return (`IfcDiff.added_attachments`) is a
  violation: the parent of a creation must be the selection.
- **Population is objects only.** `added_objects` / `removed_objects` (IfcObjects with their
  class) are what the card, the commit counters, the index refresh and `is_empty` use;
  `added_global_ids` holds every IfcRoot (psets, relationships) and must not be counted or
  refreshed. A pset added or removed with no properties is one row named after the pset.
- **Zero targets** is one error string through the same repair loop. **Non-empty targets with
  an empty diff** is an "already so" outcome: no proposal, no repair.
- **The flag rule (V-3):** rows are aggregated by `(pset, property, before → after)` with
  counts. A row is flagged when something about it is not in the request: its new value
  (not a case-insensitive substring of the request; booleans and None are exempt); the
  **name of the property it changed** (no squashed name, no camel word of ≥ 4 letters and
  no `PROPERTY_SYNONYMS` entry appears in the request); or its **prior value** (*review 11*,
  2026-09-18) — every row of a property the proposal overwrites from more than one distinct
  before-value flags, the whole property, not just the minority rows, since the rule counts
  before-values and never ranks them (no fire-rating ordering baked in). A scalar removal is
  exempt from that third condition. Card label: "overwrites different existing values"
  (`heterogeneous` is the internal `flag_reason` key). Every added or removed entity is one
  flagged row. `DiffRow.flag_reason` names which; the card's badge shows it. A flag is never
  a repair. Flagged rows must be ticked in the approve POST.
- **Blind explanation (V-4):** `explainer.py` shows the model the code and the aggregated
  rows, **never the request**, and asks for one sentence. Never raises; on failure the stored
  explanation is **empty** (the card shows a placeholder, the commit subject falls back to the
  request). Passes the same `num_ctx` as the code call.

## One repair rule (C-4)

Every check after generation yields either nothing or **one error string**: extraction
failure, traceback, zero targets, scope violation. The repair prompt is the previous code
plus that string. At most two repairs; a third failure raises `ModificationError` quoting
the last error. No failure kind gets a special prompt. A model timeout (`safe_invoke`, 240 s)
or an unreachable provider is **not** a repair: `CodeGenerator._invoke` raises
`ModificationError` (`LLM_TIMEOUT` / `LLM_UNREACHABLE`, retryable) and the request ends with a
failure card; the typed budget / BYOK / kill-switch errors pass through for the views.

## Proposal row = the journal

`ModificationProposal` V3 columns: `code`, `target_global_ids`, `diff`, `base_fingerprint`,
`scratch_path`, `guardian_skipped`, `flags_acknowledged_at`, `explainer_model`. V2 columns
(`changes`, `diff_preview`, `tier`, `operation`, `intent_json`, `filter_spec`, `confidence`,
`code_review_acknowledged_*`) are nullable and never reused with a new meaning.
`has_flagged_rows` is computed from the stored diff and the request.

## Approve — `writeback/services/execution_service.py`

```
view: claim the row in ONE guarded UPDATE (pending → approved, stamps flags_acknowledged_at);
      rowcount 0 → 409 (a double-click's second POST stops here)
execute, under select_for_update on the IFCFile row and the proposal row:
  status guard on the LOCKED row → complete row → scratch exists
  → fingerprint compare (mismatch: "file changed, please re-propose", abort)
  → os.replace(scratch, original) → git commit (subject + body with the code)
  → GitCommit row from the diff counts (objects only) + APPLIED + file_hash, one savepoint
after the lock: failure-path writes (FAILED, scratch deleted, git rollback), then
  refresh_entities(modified ∪ added objects ∪ removed objects), failures logged not raised
```

**The code is not run again.** The approved diff is the applied diff by construction. Never
read the proposal status from the instance the view holds; the locked row decides.
Index refresh is `ifc_processor/services/index_refresh.py:refresh_entities` — one file open,
only classes the parser indexes, direct spatial container linked, a touched type object refreshes
its `IFCElementType` row and every occurrence, no embedding regeneration (stated, not hidden).

## Guardian (RAV)

Runs at proposal time inside `ProposalService.create_proposal`, so the verdict is on the card
before the human decides. Advisory, never blocks: wrapped in try/except, failure → status
`failed`. Skippable per request (`skip_guardian`), stateless, the skip is recorded on the row.
Two document search queries, unioned: the dominant aggregated diff row ("wall fire rating
EI60") and the request text (capped at 512 chars, `REQUEST_QUERY_CAP`) — the diff row is
precise but carries nothing the user actually wrote, so a citation, a clause or a term in
another language in the request can retrieve a chunk the diff row alone cannot (*review 10*,
proposal 9173ae8f: the diff-row query missed a Norwegian clause at 0.4551 against the 0.45
threshold; the request text, already carrying the term, scored 0.267). Results are deduped by
chunk id keeping the best distance before the threshold applies; identical queries embed once.
Uses the Modify model with the Modify `num_ctx` so a request stays on one loaded runner, built
**for the requesting user** (BYOK, budget, call log) and called through `safe_invoke` (90 s);
a timeout is a FAILED verdict.

## Progress phases

`writeback/services/emitters.py:Phase` (StrEnum): `ground · generate · run · verify · guardian`.
Detail keys the page renders: `targets`, `flags`, `verdict`. Nothing else.

## Card (one serialiser)

`writeback/services/proposal_serializer.py:serialize_proposal` feeds the HTTP view, the
WebSocket consumer and the persisted-card template. Shows: request, blind explanation + model
name, targets with evidence from the index (name, container, one distinguishing property),
aggregated diff rows (flagged on top), the code collapsed, Guardian verdict / "skipped".

## Approval is one POST

`action=approve` carries `acknowledged_keys` (the flagged row keys the user ticked). The
server recomputes the flagged keys from the stored diff and refuses with 422 unless the sets
are equal, then claims the row (`ProposalService.claim_for_approval`: one guarded UPDATE that
stamps `flags_acknowledged_at`) and executes; a second POST for the same row gets 409. There
is no separate acknowledge endpoint. The page disables Approve and Reject while a POST is in
flight and escapes every server or model string before inserting it. Every Modify POST needs EDITOR or OWNER (`can_modify`), like the
WebSocket consumer; viewers read the tab and get 403 on a POST.

## Models and settings

Modify uses a code-tuned model (`SiteLLMConfig.modify_model`, Ollama tag honoured); Ask keeps
its prose model. `NUM_CTX = 8192` is one constant in `generator.py`, passed to **every**
Modify-model call (code, explainer, Guardian): a different `num_ctx` makes Ollama reload the
model. The explainer uses the Modify model; no setting for it.
GPU table (help modal): 7B ≤ 8 GB, 14B ≤ 12 GB, 30B ≤ 24 GB. No CPU offload by design.

## Benchmark

Corpus `fixtures/benchmark/pipeline-test-prompts.txt` against `Ifc4_SampleHouse.ifc`.
Expectations are human-readable and GlobalId-free: `# targets: IfcWall x5 in "Ground Floor"`,
`# diff: Pset_WallCommon.FireRating = EI60 x5`, `# reject: "geometry"`. Columns:
targets-match, diff-match, integrity, latency median / p90, tokens. A refused change case
scores **false** on targets and diff (it stays in the denominators); a `ModelUnavailableError`
is a harness error outside every score. Integrity re-reads the written scratch copy with
`diff_files` + `IfcDiff.unexpected`, independently of the pipeline's gate. Diff values compare
equal (`EI60` ≠ `REI60`). `--model provider:tag` per bake-off row; `--repeat` keeps the first
failing run and never repeats a failed or advisory case. `--user EMAIL_OR_USERNAME` runs as a
real account so `UserLLMConfig` applies as it would for a real request; omitted, every call
runs anonymous (site defaults only, no BYOK) — the default before *review 8*, kept for
backward compatibility but no longer the recommended way to run a row. A `guid: <IfcType>
named "<substring>"` case line resolves one real GlobalId through the index at run time and
substitutes it for `{GUID}` in the prompt, so a GlobalId-only request is testable without ever
writing a literal GlobalId into the corpus file (B-1).

---

## Common Mistakes to Avoid

1. Mutating in `select` — the snapshot is taken before it, so the mutation lands in the diff
   and the scope check treats it like any other change. Keep select read-only anyway.
2. Iterating `model.by_type(...)` inside `modify` instead of `targets` — scope violation.
3. Any geometry or placement change — always a violation, never a feature.
4. `model.write()` / `ifcopenshell.open()` in generated code — forbidden pattern.
5. Forgetting to delete the scratch file on reject / supersede / apply / cancel.
6. Running the code again at approval — never. Swap the reviewed scratch copy.
7. Adding a special repair prompt for one failure kind — there is one repair rule.
8. Storing V3 data in a V2 column (`changes`, `diff_preview`, `intent_json`, `confidence`).
9. Serialising the card in a second place — `serialize_proposal` is the only one. Same rule on
   the client: the live payload's `proposal.html` (`views.py`, `consumers.py`) is the rendered
   card — insert it, never rebuild one from the JSON fields in page-script JS (`review 8`/`9`
   found exactly this: a second, dead client-side renderer expecting V2 keys had survived
   in `_modify.html` since the build).
10. Adding a setting for the explainer model or for the GPU tier — there is none.
11. Business logic in views or consumers — services only.
12. Letting Guardian block or raise — advisory, try/except, `failed` status.
13. Counting or refreshing `added_global_ids` — that is every IfcRoot; use the object rows.
14. Reaching a pset through `get_pset(...)["id"]` in the prompt — it may be the type's pset.
18. Adding a module to the api sheet without adding it to `code_sandbox.API_MODULES`, or
    naming a variable after one (`pset = pset.add_pset(...)`) in prompt text.
19. Creating an entity under a parent `select()` did not return — a scope violation; the
    recipe is "select the storey, building or project it belongs to".
15. A Modify-model call without `num_ctx=NUM_CTX` — Ollama reloads the model.
16. Deciding approval from the proposal instance the view holds — the locked row decides;
    claim first, execute under the file lock.
17. Inserting a server or model string into the page with `innerHTML` unescaped.
