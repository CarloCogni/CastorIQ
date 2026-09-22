# Expert re-run protocol — Modify V3, human row

Purpose: turn the teammate's re-run of the earlier (V2-era) manual test set
against the V3 pipeline into scored evidence the memory can cite beside the
machine rows, instead of an impression. Written 2026-09-15 for the 27 Sep
submission. The person running it does not need to read any code.

## What changed, and why the two rows are not comparable

The V2 log scored whether the tier router understood the sentence. V3 has no
router: the model writes the change as code, the code runs on a copy, and
what it did is measured as a diff (`docs/writeback_V3/overview.md`). A prompt
that "passed" on V2 because the right tier was chosen may fail on V3 because
the code selected the wrong wall; a prompt that failed on V2 may pass on V3.
Neither direction says which pipeline is better at the same thing, because
they do not measure the same thing. The memory states this once and moves on.

What V3 measures that V2 could not: **integrity** — nothing outside the
selection changed in the written file (46 of 46 in the 2026-09-15 bake-off,
by an independent re-read of the copy). That is the claim to test by hand.

## Setup

- Branch: `main` at or after commit b7e6c20 (V3). Note the commit hash in the log.
- Model: the site default Modify model (Settings → About this build shows it). Note it.
- Project: any project with the sample house `fixtures/benchmark/Ifc4_SampleHouse.ifc`
  processed, so results overlap the benchmark corpus.

## Per prompt, record these columns (one row per prompt)

| Column | How to fill it |
|---|---|
| `prompt` | verbatim, the same sentence as in the V2 log |
| `v2_outcome` | from the old log: pass / fail / n.a. |
| `outcome` | `proposal` · `rejected` (the card says why) · `already so` · `error` |
| `targets_ok` | the card lists the entities; are they exactly the ones the sentence meant? y / n / partly |
| `diff_ok` | the aggregated rows show the property, before → after, count; is that the change asked for? y / n |
| `flagged_rows` | how many rows carry a flag badge, and did the badge make sense? |
| `explanation_ok` | the one-sentence blind explanation: does it state property, value and count, and nothing the rows do not show? y / n |
| `integrity` | after approving, open the file's history: does the commit touch only the listed entities? y / n (spot-check 5 of the approved ones, not all) |
| `guardian` | verdict shown (confirmed / conflict / no info / skipped / failed) and whether it was right |
| `time_s` | from Send to card |
| `notes` | anything odd, verbatim error text |

Approve at most the prompts you would approve as an engineer; reject the rest
and record it. Do not repair a wrong proposal by rephrasing: log it and move
to the next prompt. A rephrase is a new row.

## Scoring, done by whoever writes the record

- Pass = `outcome` proposal with `targets_ok` y and `diff_ok` y, or a rejection
  the corpus expected.
- Where a prompt matches a case in `fixtures/benchmark/pipeline-test-prompts.txt`,
  put the harness verdict for that case (from the committed bake-off artifact)
  beside the human verdict. Agreement is reported as raw agreement and
  Cohen's κ over the overlapping cases; that is the expert-validation item in
  `rubric-map.md` §5.
- The record is a new dated file in `docs/evaluation/`, immutable, with the
  V2 row cited as history and the non-comparability paragraph above repeated
  in one sentence.

## What a weaker human row would mean

If the human row passes fewer prompts than the V2 log did, the record says
so. It also says what the failures were (wrong selection, api error,
rejection) using the same taxonomy as the bake-off, and whether any approved
change touched anything outside its selection. The second question is the
one V3 was built to answer; the first is the ceiling of the local model.
