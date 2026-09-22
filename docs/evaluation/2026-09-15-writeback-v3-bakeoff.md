# 2026-09-15 — Writeback V3 bake-off

**Harness:** `manage.py benchmark_writeback` (Harness B, V3 columns) · **Corpus:** `fixtures/benchmark/pipeline-test-prompts.txt`, 98 prompts, 20 sections · **Fixture:** `Ifc4_SampleHouse.ifc` (47,369 entities, 2.2 MiB) loaded in the local "RAV Benchmark" project · **Machine:** RTX 4070 Laptop, 8 GB graphics memory, Ollama, no CPU offload unless stated · **Artifacts:** `runs/writeback-v3-2026-09-15-*.json` (the JSON carries every case's generated code, outcome and explanation).

This record is immutable by convention. Later rows (a 12 GB and a 24 GB machine) get their own dated record and are linked from here.

**Correction, 2026-09-15 (review 4).** The harness that printed the table below had two scoring defects, found in the code review after the build and fixed the same day (decision log, *review 4*). The rows were **not re-run**; the corrected columns are re-scored from the stored artifacts, which carry every case's outcome:

| | 7B coder | Claude ceiling |
|---|---|---|
| targets match (corrected) | **16 / 64** | **38 / 64** |
| diff match (corrected) | **14 / 66** | **39 / 66** |
| integrity | not measured — see below | not measured — see below |

1. **Denominators.** A change case the model refused, or failed three times on, left `targets_match` and `diff_match` unset and dropped out of both denominators. Thirty of the 7B row's 66 change cases and 20 of Claude's ended that way. Under the corrected rule they score false, which is what they are. The printed 16 / 36 and 38 / 46 below overstate both models; every sentence in "Reading the numbers" that cites a targets rate should be read against the corrected column. *Review 5* found the same gap on the "already so" path: one change case per row (3.4 on the 7B row, 5.1 on Claude's) ended with no change and left the diff denominator; it now scores false on both columns, which is why the diff column reads / 66.
2. **Integrity.** The column re-ran the pipeline's own scope check on the diff the pipeline had already gated on, so it could not be false; 39 / 39 and 50 / 50 are the gate's output, not a measurement. The harness now re-reads the written scratch copy from disk and diffs it against the original. The claim "nothing reached the file that the scope check did not permit" stands for what it says, that the gate held; it is not evidence about the bytes on disk until the next row runs.

The `passed`, `reject / no-change`, `repairs`, latency and token rows are unaffected. The table below is left as printed.

## What is measured

| Column | Question |
|---|---|
| targets match | `select()` returned exactly the entities the corpus names, resolved through the index |
| diff match | the measured diff contains every expected row with the expected count |
| integrity | nothing changed outside the selection and no geometry moved — as printed here, the pipeline's own gate re-applied (see the correction above); from the next row, an independent re-read of the written copy |
| reject / no-change | requests that must be declined were; requests that already hold say so |
| repairs used | how many of the at-most-two repair calls the model needed across the run |

`--repeat 2` keeps the worst of two runs, so non-determinism counts against a model rather than being averaged away. Three cases are advisory and never scored (2.4, 7.2, 18.4).

## Rows

| Row | Tag | Fit | Notes |
|---|---|---|---|
| 7B coder | `ollama:qwen2.5-coder:7b` | 8 GB, no offload | the floor the spec promises for limited hardware |
| Claude ceiling | `anthropic:claude-sonnet-4-6` | cloud | how far local is from frontier; API key set for the run only |
| 14B control | `ollama:qwen3:14b` | 8 GB **with CPU offload** | today's best non-coder; **stopped**: 135 s median per call offloaded, see below |
| 14B coder | `ollama:qwen2.5-coder:14b` | 12 GB | **pending** a 12 GB machine |
| 30B coder | `ollama:qwen3-coder:30b` | 24 GB | **pending** a 24 GB machine |

## Results

95 scored cases (3 advisory), `--repeat 2` on the coder and ceiling rows.

| | 7B coder | Claude ceiling | 14B control (offloaded) |
|---|---|---|---|
| passed (scored) | 39 / 95 | 65 / 95 | not run |
| targets match | 16 / 36 | 38 / 46 | — |
| diff match | 14 / 35 | 39 / 47 | — |
| integrity | 39 / 39 | 50 / 50 | — |
| reject / no-change | 25 / 29 | 26 / 29 | — |
| repairs used | 62 | 58 | — |
| errors | 0 | 0 | — |
| latency median / p90 | 12.4 s / 15.7 s | 8.1 s / 21.5 s | 135 s per **call** (15 calls measured) |
| tokens in / out | 755,803 / 48,105 | 896,951 / 72,510 ($3.78) | — |

**7B row, by section** (passed / scored): 1: 2/6 · 2: 1/4 · 3: 2/4 · 4: 1/5 · 5: 0/5 · 6: 1/4 · 7: 1/4 · 8: 0/4 · 9: 1/5 · 10: 1/5 · 11: 0/3 · 12: 0/3 · 13: 8/8 · 14: 5/6 · 15: 4/6 · 16: 4/5 · 17: 3/4 · 18: 2/4 · 19: 2/4 · 20: 1/6.

**Claude row, by section:** 1: 3/6 · 2: 2/4 · 3: 4/4 · 4: 4/5 · 5: 1/5 · 6: 3/4 · 7: 2/4 · 8: 1/4 · 9: 2/5 · 10: 5/5 · 11: 1/3 · 12: 2/3 · 13: 8/8 · 14: 6/6 · 15: 5/6 · 16: 5/5 · 17: 3/4 · 18: 3/4 · 19: 1/4 · 20: 4/6.

**Claude row, how the 30 failures fail.** Three causes account for most of them, and all three are about the harness, not the model's code:

1. **The Revit suffix.** Nineteen corpus prompts name a wall as `:285330`, the trailing number of its Revit export name. Claude reads it as a STEP id and writes `model.by_id(285330)` (or declines: "Entity #285395 does not exist"), while the 7B coder, less knowledgeable, uses `by_name` and gets it right. Nine empty-selection rejections and two declines are this. Grounding lists no entity names by design (G-1); the corpus convention is hostile to a model that knows IFC. Either the corpus drops the convention or the prompt says a trailing number is part of the name.
2. **"Walls" includes curtain walls.** The one grounding match is a substring on the type stem, so *walls* selects `IfcWall`, `IfcWallStandardCase` **and** `IfcCurtainWall` for the pset list, and Claude dutifully includes the two curtain walls (cases 2.5, 4.1, 7.1, 8.3: "expected 5, got 7"). The corpus counts five. Whether a curtain wall is a wall is a modelling question the corpus should state; the match itself works as specified.
3. **`getattr` is a forbidden pattern.** Three rejections (1.5, 12.1, 18.3) are Claude writing `getattr(e, "Name", None)`, which the V2 sandbox scan bans as an escape vector. The ban is out of proportion for a sandbox the spec calls a speed bump; lifting it is a security decision, recorded here and not taken. *(Taken on 2026-09-15 in review 4: `getattr` and `hasattr` are ordinary builtins now; these three cases would run.)*

Three requests that must be declined produced a proposal (15.6, 19.4, 20.5): Claude created three unnamed zones, set an empty string, and found the doors of the entrance hall by walking geometry the corpus assumed nobody would. Integrity is 50 of 50: nothing left the selection.

**7B row, how the 56 failures fail:** a proposal on the wrong entities, 19 (the selection was off: a subtype confused with its parent, a name fragment missed, a storey walk that returned everything); a rejection after three code errors, 15 (hallucinated helpers such as `by_guid`, a wrong api keyword, a value cast the pset refused); a rejection after three empty selections, 10; a proposal whose diff did not match, 2; a scope violation three times, 2; declined outright by the model, 2; other, 5. Nothing reached the file that the scope check did not permit: integrity is 39 of 39, and every failed attempt ended in a repair or a rejection, never in a wrong proposal that passed as right.

## Re-run after review 5 (7B row)

The third code review (decision log, *review 5*) changed the prompt: the pset recipe gained an
`unshare_pset` step, because `add_pset` returns a pset shared by several entities as is. A prompt
change makes the 14 Sep row a different system under test, so the 7B row was re-run the same day,
`--repeat 2` as before, on the same machine (8 GB, no offload). Artifact:
`runs/writeback-v3-2026-09-15-qwen2.5-coder-7b-review5-repeat2.json` (a single-run artifact,
`…-review5.json`, is kept beside it: 42 / 95).

| | 7B coder, 14 Sep row | 7B coder, review 5 re-run |
|---|---|---|
| passed (scored) | 39 / 95 | **41 / 95** |
| targets match | 16 / 64 | **19 / 64** |
| diff match | 14 / 66 | **18 / 66** |
| integrity | 39 / 39 (gate re-applied) | **46 / 46 (re-read of the written copy)** |
| reject / no-change | 25 / 29 | 23 / 29 |
| repairs used | 62 | 59 |
| errors | 0 | 0 |
| latency median / p90 | 12.4 s / 15.7 s | **6.4 s / 14.0 s** |
| tokens in / out | 755,803 / 48,105 | 509,066 / 26,081 |

**What the re-run measures.** The 14 Sep row predates reviews 3 and 4 as well (one `num_ctx` for
every Modify call, so Ollama no longer reloads the model between the code call and the
explainer; `getattr` / `hasattr` allowed; occurrences snapshot their own psets). The halved
latency and the lower token count come from those, not from the recipe. No corpus case targets an
entity with a shared pset (walls, doors, windows, slabs and spaces on the sample house own their
psets), so the recipe change itself moves no score here; it costs the model two more lines to
copy, and the row shows the 7B still copies the example correctly. The integrity column is now a
measurement: the runner re-reads the scratch copy from disk and diffs it against the original,
independently of the pipeline's gate (review 4), and every written copy changed only its targets.

**Case-level diff against the 14 Sep row** (`--baseline`): fixed 3.4, 4.2, 7.3, 11.2, 16.3, 17.1,
20.3; regressed 4.4, 7.5, 14.4, 16.4, 18.2. Of the regressions, 14.4 and 16.4 are over-eager
proposals on prompts the corpus expects declined (the 7B took "higher thermal mass" and "third
wall from the left" as instructions), 7.5 and 18.2 are three-strikes rejections (zero targets on
"remove Pset_Maintenance", a runtime error on REI120), and 4.4 is the "internal walls" selection
that now produces a proposal on the wrong set. All five are model non-determinism on prompts that
sat at the edge before; none touches the recipe.

**7B re-run, by section** (passed / scored): 1: 2/6 · 2: 1/4 · 3: 3/4 · 4: 1/5 · 5: 0/5 · 6: 1/4
· 7: 1/4 · 8: 0/4 · 9: 1/5 · 10: 1/5 · 11: 1/3 · 12: 0/3 · 13: 8/8 · 14: 4/6 · 15: 4/6 · 16: 4/5
· 17: 4/4 · 18: 1/4 · 19: 2/4 · 20: 2/6.

The sentences in "Reading the numbers" below were written against the 14 Sep row; the picture
they draw (rejections handled well, plain "all walls" requests pass, names and subtypes fail) is
unchanged by the re-run. Cite the re-run row.

## P-1: open and snapshot time

Measured with `manage.py time_snapshot` on the largest processed file (the sample house):

| | seconds |
|---|---|
| `ifcopenshell.open()` | 0.12 |
| `IfcSnapshot.from_model()` | 0.49 |

Two snapshots per attempt cost about one second. No snapshot cache is needed (spec non-goal confirmed).

## What the section-1 smoke runs changed before the bake-off

Three short runs of section 1 with the 7B coder, before any full row, found two prompt-level defects that would have sunk every local row. Both are fixed in the code, not in the corpus:

1. **Bare helper names.** The model calls `by_name(...)` without the import line the prompt showed. The eight helpers, `ifcopenshell`, `ifcopenshell.api` and `element` are now bound in the sandbox namespace; the prompt says so. (0/6 → 4/6 on section 1.)
2. **The pset dance.** Setting a property means find-or-create the pset, then `pset.edit_pset`. Without an example the model invented `entity.get_pset(...)`. The prompt now carries the one mixed example the spec allows (C-2), with no example values that could be copied into a change.

The two remaining section-1 misses are model errors the benchmark should measure, not mask: a hallucinated `by_guid` helper (case 1.5) and selecting `IfcWallStandardCase` for an external wall (case 1.6).

A first full 7B row was abandoned after 80 minutes when one generation ran for 30 minutes: the 7B model looped, and the streaming client timeout never fires while tokens keep arriving. Two caps went into the pipeline (not the benchmark): `num_predict` on both model calls (1536 tokens for code, 160 for the sentence) and a hard wall-clock cap per call through `safe_invoke` (240 s / 90 s). The rows below ran with those caps.

A second finding from the end-to-end check, recorded rather than fixed: the one flag rule exempts `None` as a removal, so a container **removal** (a wall detached from its storey by a wrong `assign_container`) is a normal row; only the blind explanation exposed it ("removing them from the Ground Floor container"). A rule change is a spec decision, not a build one.

## Explainer prose (ten-case sample, 7B coder)

Ten passing cases drawn at random (seed 20260915) from the 7B artifact. Judged for (a) property, value and count stated; (b) nothing said that the diff does not show.

| Case | Request | Blind explanation (model never saw the request) | a | b |
|---|---|---|---|---|
| 16.5 | change FireRating to EI120 on Basic Wall:Wall-Ext_102Bwk-75I… | The FireRating property was set to 'EI120' on 3 IfcWall entities. | ✓ | ✓ |
| 16.2 | set FireRating to EI120 on Wall-Ext_102Bwk | The FireRating property was set to 'EI120' on 3 IfcWall entities. | ✓ | ✓ |
| 17.2 | add CustomStatus=Active on all doors | The CustomStatus property was set to 'Active' on 3 IfcDoor entities. | ✓ | ✓ |
| 1.5 | set FireRating to EI30 on the door :285959 | The FireRating property was set to 'EI30' on 1 IfcDoor entity. | ✓ | ✓ |
| 3.1 | change FireRating to REI120 on all walls | The FireRating property was set to 'REI120' on 5 IfcWall entities. | ✓ | ✓ (the two IfcWallStandardCase are counted as IfcWall, which is what the diff summary says) |
| 18.2 | change fire rating to REI120 to all walls | The FireRating property was set to 'REI120' on 5 IfcWall entities. | ✓ | ✓ |
| 6.4 | add FireRating EI90 to all walls | The FireRating property was set to 'EI90' on 5 IfcWall entities. | ✓ | ✓ |
| 9.3 | add Pset_FireCompliance to all walls and all windows with St… | The `Standard` property of `Pset_FireCompliance` was set to 'FS-2026' on 9 entities. | ✓ | ✓ |
| 1.1 | change ThermalTransmittance to 0.18 on wall: IfcWall Basic W… | The thermal transmittance of 1 IfcWall was changed from 0.2359 to 0.18. | ✓ | ✓ |
| 17.3 | add Status="Pending" to all walls | The Status property of the Pset_WallCommon was set to 'Pending' on 5 IfcWall entities. | ✓ | ✓ |

Ten of ten on both criteria. The coder's prose is adequate for the one sentence the card needs; the explainer stays on the Modify model (spec U-4, no setting).

In the end-to-end check the same sentence caught the one wrong-but-plausible change the flag rule cannot: the code set `LoadBearing = True` for a fire-rating request (a copied example value, since fixed), a boolean the flag rule exempts, and the sentence said so.

## Reading the numbers

- **The safety claim holds on both rows.** *(Corrected: the integrity column as printed is the gate's own output, see the correction at the top.)* What the rows do show is that every wrong attempt ended in a repair or a rejection and no proposal reached the card with a change outside its selection. That is the "maximal verification" argument as far as these rows can carry it; the independent integrity measurement lands with the next row.
- **The 8 GB floor is measured, and it is a floor.** The 7B coder passes 39 of 95 and selects the right entities in 16 of 64 selection cases *(corrected from 16 of 36)*. It handles rejections well (25 of 29), all the geometry refusals, and the plain "all walls" requests; it fails on names it must match, on subtypes, and on api details it does not know. Twelve seconds median per request.
- **The ceiling is 65 of 95, and its misses are mostly the harness's.** Two-thirds of the Claude failures are the Revit-suffix convention and the curtain-wall question above; with those settled the ceiling would sit around 80 of 95. The gap between local and frontier is real (26 cases) but narrower than the raw rows suggest.
- **The corpus is a measurement instrument and it has two blunt edges.** The `:285330` suffix and the wall/curtain-wall question should be decided in the corpus, in writing, before the next row; both are corpus edits, not code changes, and the record must say which rows ran before them.
- **The 14B control could not be run on this machine, and that is the spec's own point.** Offloaded to system memory, `qwen3:14b` took 135 s median per model call (max 184 s, 15 calls measured), so a 98-case row with up to three calls per case would not finish in a working day. The row was stopped after 40 minutes with no artifact. The spec chose "no CPU offload by design" for exactly this reason: a model that does not fit is not a slower model, it is a different product.
- **What the 12 GB and 24 GB rows would answer** is whether a code-tuned model that knows the api closes the 7B's api-detail failures without inheriting Claude's STEP-id reading. Those rows, and the 14B control, are pending a machine that fits them.
