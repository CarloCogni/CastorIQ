# Resolver fix plan — entity_resolver scope=all_of_type + unknown-scope extraction

Scope: Cases 1 and 2 only (code fix, this PR), implemented as four
commits — Fix 1, Fix 3, Fix 2, Fix 4 (Fix 3 was discovered mid-implementation
as a precondition Fix 2 needs; Fix 4 was discovered during live-UI
Verification 2 on the 1.7B account; see below for both). Case 3 — the
UI-vs-shell model divergence — is unrelated to "Fix 3" below (naming
collision, not a relationship) and remains a separate infra item, not
fixed here — see bottom.

## Confirmed root causes

### Case 1 — `all_of_type` ignores `entity_name`

`writeback/services/entity_resolver.py::_db_resolve` (~line 724):

```python
if scope == "all_of_type" and isinstance(ifc_type, str) and ifc_type.strip():
    return self._resolve_all_of_type(ifc_type.strip())
```

The extractor correctly returns `entity_name` alongside `scope=all_of_type`
when the user names a specific wall *type* ("all walls of type X"), but
`_resolve_all_of_type` takes only `ifc_type` and queries
`{"ifc_type": ifc_type}` — the type qualifier is silently dropped, so the
request escalates to every wall in the project (200/351 in the observed
case) instead of the named subtype.

### Case 2 — countable category phrasing returns `scope=unknown`

`EXTRACTION_SYSTEM_PROMPT` in the same file has no example for a
countable/quantified category reference with no proper name ("the four
elevator shaft walls") plus a trailing code-citation clause ("per IBC
713.4"). The model gives up rather than mapping this to `all_of_type` +
a descriptive `entity_name`. This is a prompt-coverage gap, not a
downstream code bug — the extraction call itself returns
`scope=unknown`.

### Case 3 — NOT part of this fix (see bottom)

Confirmed separately: the 4-vs-200 divergence between the shell diagnostic
and the live Modify UI (test case C18) is caused by a per-user LLM model
override, not a resolver code-path bug. Full writeup below. Excluded from
this PR's scope per direction — owned by Carlo as infra work.

---

## Fix 1 — honor `entity_name` under `all_of_type`

In `_db_resolve`, route to the existing (already-tested) `_resolve_specific`
name-matching path when `entity_name` is present alongside
`scope=all_of_type`, instead of blanket-matching the whole `ifc_type`:

```python
if scope == "all_of_type" and isinstance(ifc_type, str) and ifc_type.strip():
    if isinstance(entity_name, str) and entity_name.strip():
        return self._resolve_specific(ifc_type.strip(), entity_name.strip())
    return self._resolve_all_of_type(ifc_type.strip())
```

This reuses `_resolve_specific`'s progressive-trim cascade and match-count
cap — no new matching logic. Reference: entity_resolver.py:736-833.

**Known limitation, accepted for this PR:** `_resolve_specific` matches via
plain `name__icontains` (`filter_engine.py:_apply_name_pattern`), with no
punctuation normalization. When the extracted phrase doesn't share a
contiguous substring with the real entity name (e.g. `"Interior 6 1/8
Partition 2-hr"` vs the DB's `'Interior - 6 1/8" Partition (2-hr)'`), this
fix will correctly return **no match** rather than the wrong 200-wall
match. That surfaces as a "could not locate entities" rejection
(`proposal_pipeline.py:_dispatch_t1`, ~line 337) asking the user to name
the entity more precisely — a safe fail-closed outcome, not a silent
mutation of the wrong 200 entities. Fuzzy/punctuation-tolerant name
matching is a separate, larger change and is intentionally out of scope
here — flag it if the fix doesn't fully resolve the original report's
literal wording.

## Fix 3 — guard exemption when Fix 1's precondition is met

**Discovered during implementation, not in the original plan.** Writing the
regression test for Fix 2 surfaced a second, independent code path that
also blocks Case 2: `_downgrade_unquantified_all_of_type`
(`entity_resolver.py:280-310`) runs on every extraction and rewrites
`scope="all_of_type"`/`"filtered"` back to `scope="specific"` (with the
*entire raw user message* as `entity_name`) whenever the message has none
of `all `/`every `/`each `/`any ` **and** matches `_SPECIFIC_NAME_MARKERS`
(a hyphen, a colon, or a 3+-digit run).

"Set the FireRating property to 2 HR on the four elevator shaft walls per
IBC 713.4." hits both conditions — no quantifier word, and "IBC **713**.4"
supplies the 3-digit marker. So even after Fix 2 teaches the extractor to
correctly emit `{"scope": "all_of_type", "ifc_type": "IfcWall",
"entity_name": "elevator shaft"}`, this guard overwrites it back to
`scope="specific"` with the whole sentence as `entity_name` before
`_db_resolve` ever sees the correct shape — reproducing the original bug's
symptom (0 matches, fail-through) via a different mechanism than either
Fix 1 or Fix 2 addresses.

**Fix:** skip the rewrite when the extraction already has
`scope="all_of_type"` **and** a non-empty `entity_name`:

```python
def _downgrade_unquantified_all_of_type(extracted: dict, user_message: str) -> dict:
    scope = extracted.get("scope")
    if scope not in ("all_of_type", "filtered"):
        return extracted
    # Fix 1 already routes all_of_type + entity_name through the
    # name-pattern matcher, which handles both a real "category with a
    # descriptor" (e.g. "elevator shaft") and this guard's original
    # concern (a hallucinated specific name still gets tried as a name
    # pattern and correctly matches nothing). The guard is redundant once
    # entity_name is present, and actively harmful — it discards a
    # correct all_of_type+entity_name extraction in favour of matching
    # the whole raw sentence as a name, which never matches. Only run the
    # override when there's no entity_name for Fix 1 to route on.
    if scope == "all_of_type" and isinstance(extracted.get("entity_name"), str) and extracted["entity_name"].strip():
        return extracted
    msg = (user_message or "").lower()
    ...
```

**Rationale:** this aligns the guard with Fix 1's design principle. Fix 1
already trusts the LLM's `entity_name` to narrow the match when present
alongside `all_of_type` — routing it through `_resolve_specific`'s
name-pattern matching. That single path correctly handles both intended
cases:
- a real all-of-type-with-descriptor request ("elevator shaft" → matches
  the elevator shaft walls), and
- the guard's original concern, a small model mislabeling one specific
  instance as a category (e.g. `entity_name="Wall-030"` would still be
  tried as a name pattern via `_resolve_specific` and correctly match
  just that one wall, or nothing if it doesn't exist) — it does not
  silently blanket-match `ifc_type` either way, because Fix 1 never lets
  `entity_name`-bearing `all_of_type` reach `_resolve_all_of_type`.

The guard's original protection is **preserved** for the two cases it was
actually built for:
- `scope="filtered"` — untouched, the exemption only checks
  `scope == "all_of_type"`.
- `scope="all_of_type"` with `entity_name=None` (the plain "all walls"
  case) — untouched, the exemption requires a non-empty `entity_name`.

**Interim behavior — what happens if Fix 3 lands without Fix 2:** nothing.
Fix 3's exemption only fires when the extraction already has
`scope="all_of_type"` **and** a non-empty `entity_name`. Without Fix 2,
the (unpatched) prompt still returns `scope="unknown"` for prompt 2's
phrasing, so the exemption's precondition is never met and the guard's
code path is never reached for this prompt. **Fix 3 alone, without Fix 2,
is a no-op** — safe to land on its own, same as Fix 1.

## Fix 2 — extraction prompt: countable category + citation clause

Add to `EXTRACTION_SYSTEM_PROMPT` (entity_resolver.py, in the `## Examples`
section):

```
User: "Set the FireRating property to 2 HR on the four elevator shaft walls per IBC 713.4."
Output: {"scope": "all_of_type", "ifc_type": "IfcWall", "entity_name": "elevator shaft"}
```

And a rule addition near the `"all_of_type"` bullet:

```
- A counted category with no proper name ("the four X walls", "the six Y
  doors") is still "all_of_type" — set entity_name to the descriptive noun
  phrase (e.g. "elevator shaft") so it narrows within ifc_type, per the
  entity_name-honoring fix above. Ignore trailing citation clauses ("per
  IBC 713.4", "per NFPA 101") — they are not part of entity targeting and
  must not cause scope="unknown".
```

This depends on **both** Fix 1 and Fix 3 being in place — once
`all_of_type` honors `entity_name` (Fix 1) and the unquantified-category
guard no longer discards an `all_of_type`+`entity_name` extraction (Fix
3), extracting `entity_name="elevator shaft"` here correctly narrows to
the walls whose name contains "elevator shaft" instead of falling back to
`unknown`/full rejection. Without Fix 3, this prompt's numeric citation
("IBC 713.4") trips `_downgrade_unquantified_all_of_type` and the
extraction is discarded regardless of what the prompt teaches the model
to emit — confirmed by the regression test failing before Fix 3 was
added.

## Verification

- `cd src && uv run pytest writeback/tests/test_entity_resolver.py -v`
  (add regression cases for both: `all_of_type` + `entity_name` present;
  countable-category + citation-clause extraction).
- Re-run the three original prompts through
  `uv run manage.py dry_run_v2_pipeline --project <uuid> --prompts-file <file>`
  and confirm resolved counts are no longer the full-`ifc_type` blast
  radius.
- Per CLAUDE.md: `ruff check` and `ruff format` after the edit.
- **Caveat carried from the shell step alone:** the dry-run tool runs
  anonymously (`user=None`) and therefore always uses the site-default
  model (`llama3.1:8b`), not any given user's configured model. It does
  not by itself prove parity with a live UI session running under a
  per-user model override. That's what the live-UI step below is for.

### Live-UI verification 1 (required, not optional) — on the actual 1.7B model, pre-Fix-4

The shell run above proves the code is correct on the 8B site default. It
does **not** prove the fix behaves on `qwen3:1.7b`, which is what my
account actually calls in production (see Case 3). Before calling this
fix verified:

1. Log into Castor as `mariamakri.2112@gmail.com` (browser session, not
   shell) so `EntityNameResolver.llm` resolves to `qwen3:1.7b`, matching
   production.
2. In the Modify tab, submit the same three prompts one at a time:
   - "Set the FireRating property to 2 HR on all walls of type Interior 6
     1/8 Partition 2-hr, per the fire safety report section 5.1."
   - "Set the FireRating property to 2 HR on the four elevator shaft
     walls per IBC 713.4."
   - "Set FireRating to 2 HR on all walls of type \"Basic Wall:Interior
     Elevator Shaft Wall\""
3. For each, inspect the resulting proposal's affected-entity count / diff
   preview **without approving it** — `propose()` only creates a
   `ModificationProposal` (`views.py:_handle_propose`); the IFC file is
   only written on approval/execution. Reject or leave the proposal
   pending after inspecting it so nothing is actually committed to the
   project's Git-tracked IFC file during verification.
4. Confirm the affected count matches the intended subtype/category (not
   the full 200/351-wall blast radius) for prompts 1 and 3, and that
   prompt 2 either resolves narrowly or fails closed with a clear
   "couldn't locate entities" message — not a silent full-`ifc_type`
   match.
5. Record the actual `qwen3:1.7b` output in the PR description alongside
   the 8B shell output, so the PR shows both, not just the easier-to-run
   one.

If the live-UI run behaves differently from the shell run even after the
fix, that's a live finding for Case 3 (model-capability gap), not a
reason to revert this fix — but it must be reported before merge, not
discovered after.

**Result of this run: see "Verification 2 finding" above.** Prompt 1
returned 200 walls, unchanged from pre-fix — step 4's expectation above
(prompt 1 resolves narrowly) did not hold on 1.7B. This is what led to
Fix 4.

### Live-UI re-verification (Verification 2 — after Fix 4), pending

Same three prompts, same account (`mariamakri.2112@gmail.com`,
`qwen3:1.7b`), same procedure (inspect the proposal's affected-entity
count without approving; reject or leave pending afterward). Expected
outcome, now that Fix 4 is in place:

- Prompt 1 ("all walls of type Interior 6 1/8 Partition 2-hr, per the
  fire safety report section 5.1") — fails closed with a clear "could not
  identify the specific type named in the request" message. Not 200
  walls, and not yet a resolved 60-wall match either — see the
  punctuation-normalization limitation above for why full resolution is
  still out of reach.
- Prompt 2 ("the four elevator shaft walls per IBC 713.4") — resolves
  narrowly to 4 walls, unaffected by Fix 4 (no "type" phrasing in this
  prompt, entity_name is populated by Fix 2's extraction).
- Prompt 3 ("all walls of type \"Basic Wall:Interior Elevator Shaft
  Wall\"") — fails closed with the same clear message as prompt 1. Same
  root cause: 1.7B does not populate `entity_name` for "of type X"
  phrasing regardless of quoting, so Fix 4's guard fires here too.

All three outcomes are "safe" (no blast-radius match on any of them),
even though only prompt 2 resolves to the intended entities. Record the
actual `qwen3:1.7b` output for all three in the PR description.

## Landing order for Fix 1, Fix 3, Fix 2 — MUST land together, in this order

**All three must land together, in one PR.** Not split across PRs — the
dependencies below make any independently-mergeable subset unsafe or
incomplete.

**Within the PR: three commits, ordered Fix 1 → Fix 3 → Fix 2** (not
squashed into one), so the history shows each safety precondition landing
before the change that depends on it, and so any single commit can be
reverted independently if one needs to be pulled back later.

Interim-behavior analysis (what happens with each partial subset):

- **Fix 1 alone (no Fix 3, no Fix 2):** Safe, independently useful —
  unchanged from the original plan. Fix 1's branch in `_db_resolve` only
  fires when the extractor already returns `scope="all_of_type"` with a
  non-empty `entity_name` — Case 1's literal failure mode ("all walls of
  type X"). Case 2's prompt ("the four elevator shaft walls per IBC
  713.4") still hits `scope="unknown"` before `_db_resolve` is ever
  called, so it's untouched by Fix 1. No regression, no improvement for
  that prompt — stays fail-closed.

- **Fix 1 + Fix 3 (no Fix 2 yet):** Still safe, still a no-op for prompt
  2. Fix 3's exemption only fires when the extractor emits
  `scope="all_of_type"` + non-empty `entity_name`; without Fix 2 the
  unpatched prompt never produces that shape for prompt 2, so Fix 3's new
  code path is simply never reached yet. No behavior change beyond what
  Fix 1 alone already provides.

- **Fix 2 without Fix 3 (regardless of Fix 1) — confirmed broken, not
  just theoretically unsafe:** this is exactly what the failing regression
  test caught. Teaching the extractor to emit `scope="all_of_type"` +
  `entity_name="elevator shaft"` does nothing on its own —
  `_downgrade_unquantified_all_of_type` rewrites it back to
  `scope="specific"` with the raw sentence as `entity_name` before
  `_db_resolve` sees it (see Fix 3 above), which then matches 0 entities.
  Fix 2 is inert without Fix 3 for this exact prompt — not dangerous, but
  useless, which is its own reason not to ship it alone.

- **Fix 2 without Fix 1 (regardless of Fix 3) — unsafe, do not do:** if
  Fix 1 isn't present, an `all_of_type`+`entity_name` extraction that
  survives Fix 3's guard exemption still falls into the unpatched
  `_db_resolve`, which routes straight to `_resolve_all_of_type(ifc_type)`
  and ignores `entity_name` — a fail-open blanket match against all ~351
  walls, strictly worse than today's fail-closed `unknown` rejection.

Net: **Fix 2 only does anything, and only does something safe, once both
Fix 1 and Fix 3 are already present.** Hence the fixed order Fix 1 → Fix
3 → Fix 2, all three in this PR.

---

## Verification 2 finding — prompt 1 still returns 200 walls on 1.7B

Live-UI verification (per the "Live-UI verification" section below) on the
`qwen3:1.7b` account after Fix 1 → Fix 3 → Fix 2 landed: prompt 1 ("all
walls of type Interior 6 1/8 Partition 2-hr, per the fire safety report
section 5.1") still resolved to 200 walls, unchanged from the pre-fix
behavior.

### Root cause

`qwen3:1.7b` does not populate `entity_name` at all for the "walls of type
X" phrasing pattern — the extraction returns
`{"scope": "all_of_type", "ifc_type": "IfcWall", "entity_name": null}`.
Fix 1's routing (`_db_resolve`, `entity_resolver.py:733`) only narrows the
match when `entity_name` is present; with it null, the request falls
straight through to `_resolve_all_of_type(ifc_type)` and blast-matches
every wall in the project. This is a distinct failure mode from Case 1
(which Fix 1 already closes) — same symptom (200-wall match), different
gap: the extractor drops the field entirely rather than a routing bug
discarding a populated one.

## Fix 4 — fail-closed guard when "of type" phrasing drops entity_name

New function `_fail_closed_on_unresolved_type_phrasing`
(`entity_resolver.py`, alongside `_downgrade_unquantified_all_of_type`),
following the same phrasing-detector pattern as Fix 3's guard. Detects
"of type X" (case-insensitive), a bare trailing "type X" noun phrase (no
"of"), or a quoted string following type/named/called, in the raw user
message. When all three hold — `scope="all_of_type"`, `entity_name` is
null/empty, and the message shows this phrasing — the resolver returns a
fail-closed empty `ResolutionResult` instead of letting `_db_resolve` fall
through to `_resolve_all_of_type`. Wired in at all three points
`_db_resolve` is called in `resolve()` (iteration 0, and both refinement
iterations, since iteration 1/2 can also reconsider scope to
`all_of_type` — see `test_iteration_1_can_reconsider_scope_to_all_of_type`).

A value phrase that happens to contain the word "type" (e.g. "Change the
FireRating classification to type A on all interior walls") must not
trigger this guard — the detector requires the "type X" phrase to run to
the true end of the message (after stripping a trailing citation clause),
with no continuation preposition ("on", "for", ...) in its tail. A
continuation preposition present means "type" introduced a property VALUE
and the sentence keeps going to name the real target afterward, not a
type descriptor for the target itself.

**Explicit statement — this changes prompt 1's behavior on 1.7B from 200
walls to fail-closed. That is the intended outcome of this fix, not a
regression to investigate.** The user sees "could not identify the
specific type named in the request" and is asked to name the type more
precisely, instead of silently getting every wall in the project.

Tests: `writeback/tests/test_entity_resolver.py::TestFailClosedOnUnresolvedTypePhrasing`
(6 cases: entity_name populated passes through unchanged; "of type X" +
no entity_name fails closed; quoted type name + no entity_name fails
closed; plain "all walls" with no type qualifier passes through
unchanged; a value phrase containing "type" does not trigger; scope
other than `all_of_type` is untouched) plus one end-to-end test in
`TestResolveAllOfType` reproducing prompt 1's exact wording through
`resolver.resolve()`. Full suite: `writeback/tests/` — 649 passed, 0
failed.

### Known limitation carried forward — punctuation-normalization gap (separate follow-up, not part of this PR)

Fix 4 makes prompt 1 fail closed safely. It does **not** make prompt 1
resolve to the correct 60-wall subset, and no combination of Fix 1-4 can,
on its own. Even in the case where `entity_name` **is** correctly
populated (e.g. on an 8B model, or after any future extraction
improvement), `"Interior 6 1/8 Partition 2-hr"` is not a contiguous,
punctuation-identical substring of the DB's actual type name
(`'Interior - 6 1/8" Partition (2-hr)'` — differing hyphen, quote mark,
and parentheses). `filter_engine._apply_name_pattern`
(`filter_engine.py:97-107`) does a plain `name__icontains` with no
normalization, so this still resolves to 0 matches downstream in
`_resolve_specific` — same fail-closed outcome, different code path than
Fix 4's guard.

**Flag for Carlo, as a separate follow-up item, not part of this PR:**
punctuation-insensitive name matching in `filter_engine._apply_name_pattern`
(e.g. normalizing hyphens/quotes/parentheses/whitespace on both sides of
the comparison before `icontains`) is required before "walls of type
Interior 6 1/8 Partition 2-hr" can actually resolve to the intended 60
walls on any model, not just 1.7B. Until that lands, this class of
request will always fail closed rather than blast-match — which is safe,
but not yet "resolved correctly."

---

## Conflicts pipeline regression check — ADSK Conference Center

The plan asserts this change touches only `_db_resolve` /
`EXTRACTION_SYSTEM_PROMPT` in `entity_resolver.py`. Confirmed by
inspection: `writeback/services/conflict_scan_service.py` has zero
references to `entity_resolver`, `EntityNameResolver`, or `FilterEngine`
— the Conflicts pipeline (semantic conflict scan, Conflicts tab) is a
fully separate code path with no shared surface. Expected result:
unaffected. Cheap to confirm rather than assume:

1. Open the ADSK Conference Center project's Conflicts tab in the Modify
   UI, after the fix is deployed.
2. Run one semantic conflict scan.
3. Confirm it still detects the same 10 known conflicts (no new misses,
   no new false positives introduced).
4. If the count differs, that's a real finding — stop and investigate
   before merge, since it would mean the "no shared surface" assumption
   above is wrong somewhere non-obvious (e.g. a shared LLM prompt cache,
   a shared FilterEngine instance lifecycle). Not expected, but the check
   is what turns that from an assumption into a fact.

### Verification 2 result — Fix 4 confirmed working

Re-ran the three prompts on `qwen3:1.7b` after Fix 4 landed. All three
passed: prompt 1 and prompt 3 fail closed with a clear message, prompt 2
resolves to 4 walls — matching the expected outcome above exactly.

### Verification 3 result — Conflicts-on-1.7B finding (separate, NOT caused by this PR)

Running the Conflicts regression check above (step 2-3) on `qwen3:1.7b`
instead of the `qwen3:14b` used in the original Round 1 scan surfaced a
pre-existing quality issue in the Conflicts pipeline, unrelated to any of
this PR's four commits:

- 46 conflicts on 1.7B vs 10 on 14B — a 4.6x jump, on the same ADSK
  Conference Center project.
- IFC values populated with a hallucinated `'EI60'` on 53 of 68
  conflicts, where the IFC actually has no `FireRating` property
  populated at all (correct value is "absent").
- Invented composite values such as `'EI2HR'` — not a real unit, mixing
  European EI-notation with American HR-notation.
- Entity types flagged that don't carry `Pset_WallCommon.FireRating` at
  all: `IfcStair`, `IfcRoof`, `IfcSlab`, `IfcPipeSegment`,
  `IfcFurniture`.

**Confirmed not caused by this PR:** `git log -p main..HEAD --
src/writeback/services/conflict_scan_service.py` returns no output —
none of the four commits on this branch (Fix 1, Fix 3, Fix 2, Fix 4)
touch `conflict_scan_service.py` or any other Conflicts-pipeline file.
This corroborates the "no shared surface" finding above: the Conflicts
pipeline was already producing this quality gap on 1.7B before this PR;
it was masked in Round 1 only because that scan ran on the 14B model.

**Out of scope for this PR — flag for Carlo as a separate follow-up,
part of his infra work track (alongside the Case 3 items below), not
fixed here:**

1. The 1.7B/14B conflict-count divergence (46 vs 10) needs its own root
   cause investigation — likely the same class of "small model
   instruction-following gap" as Case 3 below, but for the Conflicts
   scanner's `SCANNER_SYSTEM_PROMPT` / gate logic
   (`conflict_scan_service.py`) rather than the resolver's extraction
   prompt.
2. Hallucinated property values (`'EI60'` where the IFC has none,
   invented units like `'EI2HR'`) suggest the scanner needs a
   grounding/validation step against the entity's actual Pset values
   before a conflict is reported — it currently appears to accept the
   LLM's stated IFC value without checking it against the real property.
3. Entity types flagged that don't carry the referenced Pset at all
   (`IfcStair`, `IfcRoof`, `IfcSlab`, `IfcPipeSegment`, `IfcFurniture`
   flagged for `Pset_WallCommon.FireRating`) point at a gap in
   `ELEMENT_TYPE_MAP` gating or the `_finding_applies_to_entity` check
   (`conflict_scan_service.py:636-657`) — unknown/mismatched labels
   should fail closed here too, not fail open.
4. Whether Conflicts scans should be restricted to models above some
   minimum capability threshold is the same open policy question Case 3
   raises for the resolver — worth deciding once, for both pipelines,
   rather than twice.

---

## Case 3 — UI-vs-shell divergence (confirmed cause, NOT fixed in this PR)

### Confirmed: my (test user) `UserLLMConfig` differs from the site default

Queried directly against the DB for `mariamakri.2112@gmail.com` (user
`raven`, id 1):

| Field | Value |
|---|---|
| `active_model` | `qwen3:1.7b` |
| `ask_provider_override` | `''` (none) |
| `modify_provider_override` | `''` (none) |
| BYOK keys | none stored (anthropic/groq both false) |

Site default (what an anonymous/shell call resolves to):

| | Provider | Model |
|---|---|---|
| `SiteLLMConfig.resolve("modify")` | `ollama` | `llama3.1:8b` |
| Anonymous `_resolve_llm_choice(None, "modify")` | `ollama` | `llama3.1:8b` |
| This user's `_resolve_llm_choice(user, "modify")` | `ollama` | **`qwen3:1.7b`** |

No provider override is set — this isn't a BYOK/cloud-routing issue. It's
purely `UserLLMConfig.active_model`: this account is pinned to a **1.7B**
local model, while every shell diagnostic tool (`dry_run_v2_pipeline`,
`benchmark_writeback`/`BenchmarkRunner`) runs `ProposalPipeline(project,
user=None)` and therefore always exercises the **8B** site-default model
instead. This confirms the divergence and explains C18: the resolver's
extraction LLM call in `EntityNameResolver.llm`
(`entity_resolver.py:342-345`) resolves per-user via `get_llm(user=self.user,
...)`, and `ProposalPipeline`/`ModificationService` pass `request.user`
through (`views.py:195` → `modification_service.py:58`), while both shell
tools hardcode `user=None`. A 1.7B model has materially weaker instruction
following than an 8B model on a narrow 4-field JSON extraction task — fully
sufficient to explain misclassifying `specific_multi` (4 walls) as
`all_of_type` (200 walls) on an identical prompt.

### Temperature=0.1 + no seed — does this explain a 4-vs-200 flip on its own?

**No — not as the primary explanation, though it's a real secondary
factor.** `EntityNameResolver.llm` uses `temperature=0.1`
(entity_resolver.py:344), not `0.0` like `triage_classifier.py:193`, and
`core/llm.py` sets no seed anywhere in the Ollama builder path. At
`temperature=0.1` the token distribution is heavily peaked toward the
top candidate, so run-to-run drift on a *well-separated* classification
(a case where the correct answer is clearly the dominant token) is
usually small and wouldn't be expected to reliably flip `specific_multi`
→ `all_of_type`. It could plausibly tip a genuinely close/borderline call,
but it does not on its own account for a same-model, same-prompt flip of
this magnitude — the confirmed 1.7B-vs-8B model swap is sufficient and is
the dominant, evidenced cause. `temperature=0.1`/no-seed is still worth
fixing on its own merits (a resolver whose job is deterministic entity
targeting arguably shouldn't sample at all), but it's a policy question,
not the root cause of C18.

### Action items for Carlo (separate item, not in this PR)

1. Add a `--user <email-or-id>` flag to `dry_run_v2_pipeline` and to
   `benchmark_writeback` (`BenchmarkRunner` already accepts `user=`; the
   command's `add_arguments`/`handle` never exposes it) so diagnostics can
   be run under a specific account's actual LLM config, not just the site
   default.
2. Decide whether resolver benchmarking should run against the site
   default (fast, consistent baseline) and separately against a sample of
   real per-user configs (catches this class of divergence), and make that
   an explicit, documented part of the benchmark harness rather than an
   implicit `user=None`.
3. Decide the temperature/seed policy for `EntityNameResolver.llm` —
   candidates: drop to `temperature=0.0` to match `triage_classifier`, or
   set an explicit seed via Ollama's `options.seed` if determinism is the
   goal, or accept the variance and document it in the resolver's module
   docstring as intentional.
4. Consider whether resolver quality should be a factor in what
   `active_model` values are allowed/recommended for accounts doing
   writeback (a 1.7B model on Tier 1/2 entity resolution has a materially
   different risk profile than on Ask/RAG).
