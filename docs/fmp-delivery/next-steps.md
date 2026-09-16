# Next steps to submission — checklist

Owner: Carlo. Due **27 Sep 2026, 15:59**. Tick items here; evidence lives where
it lives (`docs/evaluation/`, `runs/`, `fixtures/`). Updated 2026-09-16.

## 1. Things only you can do

- [ ] **Canvas unlock.** Submit the FMP satisfaction survey (Pablo), the M9U4
      survey (Guillermo) and the second group feedback survey. The assignment
      stays locked until all three are in. Do this first.
- [x] **Memory rebuilt for V3** (2026-09-16). `delivery-docs/CastorIQ_Final_Memory_MAIN_v3.docx`
      and `…_APPENDICES_v3.docx` are generated from `report-sections/*.md` by
      `tools/rewrite_memory.py` and `tools/rewrite_appendices.py` (commands in
      each script's docstring). Edit the Markdown and rerun; a hand edit to a
      `_v3.docx` is lost on the next rebuild. The memory build refuses to save
      if a figure in the abstract, §5 or §6 is not in a record under
      `docs/evaluation/`, if a withdrawn figure or V2 term survives, or if the
      body passes 5,000 words (4,977 now; tables, glossary and references
      excluded).
- [x] **Review copies for the team** (2026-09-16): `…_MAIN_v3_REVIEW.docx`
      and `…_APPENDICES_v3_REVIEW.docx`, built with `--review`. They restore
      the draft's four review colours where they still apply, the colour
      legend, a "what changed and why" table, and Word tracked changes against
      the team's originals; revision tracking is on for teammates' own edits.
      Three of the draft's six footnotes still applied and are back in the
      clean files (STEP format; OCR figures, reworded to the vendor source;
      security test commit); the other three belonged to removed text.
- [ ] **Team review of the v3 documents.** Send the two `_REVIEW` files.
      Teammates comment in Word; move accepted wording into
      `report-sections/*.md` and rebuild (edits made in the docx are not
      carried over automatically). Submit only the files without `_REVIEW`
      in their names. Every §8 line was edited; each
      person confirms their own. Pavla: §1, the ISO references, Appendix D.
      Islam: §4.4 (Schedule). Maria: §5.4 (her Solibri rows, `maria.csv`
      row 14). Erez: §5.4, §6.3 and Appendix E (his rows 83–97).
- [ ] **Appendices B and C.** The July memory promised B (screenshots) and C
      (Scheduling guide); the appendices file holds only A and D. Add them and
      list them in `report-sections/11-appendices.md`, or leave them out (the
      current text lists A, D and E only).
- [x] **References corrected** (checked against the sources 2026-09-16):
      Text2BIM is Du, Esser, Nousias and Borrmann (2024), not Chen et al.;
      MCP4IFC is Nithyanantham et al. (2025), not Ning et al.; the rework
      paper is Love and Li (2000), not Love, Irani and Edwards; the
      PlanGrid/FMI report gives 48 %, not 52 %; OmniDocBench authors fixed;
      ReAct dropped (nothing in `src/` uses LangGraph); Cohen (1960),
      IfcOpenShell and pgvector added.
- [x] **Leftover files from the V3 docs pass**: none left (`git status`
      2026-09-16 shows only the delivery work).
- [x] **Every §5.2 figure traceable to code** (2026-09-16). Appendix F maps
      each table to its command, scorer, corpus, tests, record and run file,
      with line-level links at the tag. `uv run python docs/evaluation/recount.py`
      recomputes Tables 5.2 to 5.4 from `runs/` with no model, and the memory
      build refuses a figure it cannot recount. The five Modify run files were
      never in git until this date; they are now. The "key entities reached"
      row is backed by `benchmark_rav --coverage` and the record
      `2026-09-16-rav-retrieval-coverage.md` (11/15 → 15/15 reproduced; 5/15 →
      15/15 by every constraining document added).
- [ ] **Rebuild both documents on the commit you tag**, because Appendix F's
      line anchors are read at build time:
      `uv run --with python-docx python docs/fmp-delivery/tools/rewrite_memory.py --review`
      and the same for `rewrite_appendices.py`; commit the regenerated files.
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

## 3. Erez's V3 testing (Modify, human row)

- [x] Sheet received 2026-09-16 (`delivery-docs/FMP-testing-logs.xlsx`, sheet
      Erez rows 84–97) and exported with row ids to
      `docs/evaluation/testing-log/erez.csv`.
- [x] Record written: `docs/evaluation/2026-09-16-expert-testing-v3.md`.
      It is a narrative log, not the per-prompt sheet of
      `expert-rerun-protocol.md`, so **no Cohen's κ** against the bake-off is
      possible; the record says so and the protocol stays open.
- [ ] If Erez has time before the tag: the per-prompt sheet from the protocol,
      on the sample house, gives the κ row. Otherwise the memory cites the
      record as expert testing and lists κ as open.

## 4. Still open, not blocking

- [ ] Expert labelling of ~40 outputs with κ (the rubric map's longest pole).
      Erez's sheet above is the first half of it.
- [ ] Memory diagrams (pipeline flow, RAG pipeline, RAV loop) and referencing.
      Figures can be generated from the JSON artifacts under `runs/`.
- [ ] 14B / 30B Modify rows: need a 12 GB / 24 GB machine. State as pending.
- [ ] hit@k / MRR for Ask; adversarial sandbox corpus. State as future work.

## 5. Before you press submit

- [ ] `uv run --with python-docx python docs/fmp-delivery/tools/rewrite_memory.py`
      saves without an UNSOURCED or PROBLEM line on the final sources, and
      the submitted file is the one it wrote.
- [ ] Table 5.5 still matches `pytest --collect-only` at the tagged commit
      (2,676 in `src/` + 19 in `tests/e2e` on 2026-09-16, after the coverage
      tests).
- [ ] `cd src && uv run pytest -q -p no:warnings` is green and
      `uv run ruff check` is clean at the tagged commit.
- [ ] The memory's and Appendix F's links open. They 404 until the tag is
      pushed; open a few line anchors to confirm they land on the definition.
