# Next steps to submission — checklist

Owner: Carlo. Due **27 Sep 2026, 15:59**. Tick items here; evidence lives where
it lives (`docs/evaluation/`, `runs/`, `fixtures/`). Updated 2026-09-15.

## 1. Things only you can do

- [ ] **Canvas unlock.** Submit the FMP satisfaction survey (Pablo), the M9U4
      survey (Guillermo) and the second group feedback survey. The assignment
      stays locked until all three are in. Do this first.
- [ ] **Paste the evaluation section into the memory.** Source:
      `docs/fmp-delivery/report-section-5-evaluation.md` (replaces §5.2 and
      §5.3 entirely, including the old Table 1). Keep the pointer paragraph at
      the top; it names the `fmp-final` tag.
- [ ] **Commit or discard the 33 leftover files** from the V3 docs pass
      (`git status`: CLAUDE.md, README.md, docs/architecture.md, docs/guardian.md,
      the two extended rubric maps, …). Nothing in the evaluation work depends
      on them, but the tag should not sit on a dirty tree.
- [ ] **Cut the tag on the final commit**, after Maria's and Erez's material is in:
      ```bash
      git tag -a fmp-final -m "FMP submission, 27 Sep 2026"
      git push origin main fmp-final
      ```
      Check that `https://github.com/CarloCogni/CastorIQ/tree/fmp-final/docs/evaluation`
      and `…/tree/fmp-final/runs` open in a browser. Optional: a GitHub
      release on the tag, and a Zenodo DOI if you want a citable reference.

## 2. The 55-item Guardian set (the 68.6 % / 43 % row)

Without the labelled file the row cannot stay in the memory. To keep it:

- [ ] Find the file (spreadsheet or CSV from the July memory's §4.2.2).
- [ ] Put it at `fixtures/benchmark/rav/independent-set/<original-name>.csv`
      and add a `README.md` beside it with: who labelled it, when, the model
      and Guardian version it was run against, the three classes, and how the
      68.6 % and 43 % were computed (which column is the truth, which the
      verdict). Commit both.
- [ ] Tell me "the independent set is in"; I will write a scorer that
      reproduces the two numbers from the file, so the row becomes as
      reproducible as the others, and add it to the memory's §5.2.2 as a
      second table.
- RAV code was untouched by V3, so the set stays valid. It does **not** wait
  on Erez's Modify re-run.

## 3. Erez's V3 re-run (Modify, human row)

Send him this: *"Follow `docs/fmp-delivery/expert-rerun-protocol.md`. One row
per prompt, the columns in the table there. When you're done, upload the sheet
as `docs/fmp-delivery/erez-v3-rerun.csv` (or .xlsx) in a PR, plus the commit
hash and the model name from Settings → About this build."*

- [ ] Sheet received and committed.
- [ ] Tell me; I will score it against the bake-off artifact where prompts
      overlap the corpus (raw agreement and Cohen's κ), write the dated record
      in `docs/evaluation/`, and add the human row to the memory's §5.2.1.
- If his row is weaker than his V2 log, that goes in as stated; V2 and V3 do
  not measure the same thing and the memory says so in one sentence.

## 4. Still open, not blocking

- [ ] Expert labelling of ~40 outputs with κ (the rubric map's longest pole).
      Erez's sheet above is the first half of it.
- [ ] Memory diagrams (pipeline flow, RAG pipeline, RAV loop) and referencing.
      Figures can be generated from the JSON artifacts under `runs/`.
- [ ] 14B / 30B Modify rows: need a 12 GB / 24 GB machine. State as pending.
- [ ] hit@k / MRR for Ask; adversarial sandbox corpus. State as future work.

## 5. Before you press submit

- [ ] Every number in the memory's §5 grep-matches a file under
      `docs/evaluation/` or `runs/` (the section was written that way; check
      nothing was retyped while pasting).
- [ ] `cd src && uv run pytest -q -p no:warnings` is green and
      `uv run ruff check` is clean at the tagged commit.
- [ ] The memory's pointer URL opens.
