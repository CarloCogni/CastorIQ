# Next steps to submission — checklist

Owner: Carlo. Due **27 Sep 2026, 15:59**. Tick items here; evidence lives where
it lives (`docs/evaluation/`, `runs/`, `fixtures/`). Updated 2026-09-19.

## 1. Things only you can do

- [ ] **Canvas unlock.** Submit the FMP satisfaction survey (Pablo), the M9U4
      survey (Guillermo) and the second group feedback survey. The assignment
      stays locked until all three are in. Do this first.
- [x] **Memory rebuilt for the V3 write path, as v2** (2026-09-16).
      `delivery-docs/CastorIQ_Final_Memory_MAIN_v2.docx` and
      `…_APPENDICES_v2.docx` (the team draft is v1) were generated from `report-sections/*.md` by
      `tools/rewrite_memory.py` and `tools/rewrite_appendices.py` (commands in
      each script's docstring); the scripts still write `_v3` names, and the
      files were renamed to `_v2` by hand. The memory build refuses to save
      if a figure in the abstract, §5 or §6 is not in a record under
      `docs/evaluation/`, if a withdrawn figure or V2 term survives, or if the
      body passes 5,000 words (4,977 now; tables, glossary and references
      excluded).
- [x] **Review copies for the team** (2026-09-16): `…_MAIN_v2_REVIEW.docx`
      and `…_APPENDICES_v2_REVIEW.docx`, built with `--review`. They restore
      the draft's four review colours where they still apply, the colour
      legend, a "what changed and why" table, and Word tracked changes against
      the team's originals; revision tracking is on for teammates' own edits.
      Three of the draft's six footnotes still applied and are back in the
      clean files (STEP format; OCR figures, reworded to the vendor source;
      security test commit); the other three belonged to removed text.
- [x] **The Word files are the master copy now** (decided 2026-09-16).
      Teammates edit the Word files directly; submission is PDF. Do not rerun
      `tools/rewrite_memory.py` or `rewrite_appendices.py`: they would
      overwrite the team's edits and write `_v3` names. `report-sections/`
      stays as the record of the v2 text.
- [x] **v4 built from the team's v2-to-v3 files** (2026-09-19). The team's
      `…_v2-to-v3_REVIEW.docx` files (Erez's re-run written into Appendix E.4
      and E.5, three memory paragraphs) were corrected into
      `…_MAIN_v4.docx` and `…_APPENDICES_v4.docx`; the REVIEW files are
      untouched. What changed: the memory was 5,113 counted words and is 4,991
      (§4 narrative cut, additions tightened); Appendix E.4's "rows 98–106"
      did not exist in the spreadsheet and are now C1–C9 with the real
      `maria.csv` / `erez.csv` rows; Table E.3 carries Erez's paired protocol
      (A1–C4); E.5 gains the Guardian dominant-row verdict, the Ask read-path
      recall and the non-deterministic decline; §3.2 states the third flag
      condition and the two Guardian queries; §5.3 / Table 5.5 read 2,695 + 21;
      Table 5.1 cites both expert records; §5.4, §6.1, §6.3 and §7.2 updated.
      Record: `docs/evaluation/2026-09-19-expert-retest-v3.md`. The
      `delivery-docs/` folder is gitignored since 2026-09-19, so the Word and
      xlsx files live only on disk and in the team's share; the CSV export is
      the tracked copy of the log.
- [ ] **Team review of the v4 documents.** Edit the `_v4` files; submit them
      exported to PDF (18 + 34 pages on 2026-09-19). Word count check before
      export (must stay ≤ 5,000):
      ```bash
      uv run --with python-docx python -c "import sys; sys.path.insert(0,'docs/fmp-delivery/tools'); import docx_markdown as md; from docx import Document; d=Document('docs/fmp-delivery/delivery-docs/CastorIQ_Final_Memory_MAIN_v4.docx'); print(sum(len(t.split()) for _,t in md.body_text(d,('Glossary','References','Appendices'))))"
      ```
- [ ] **Maria: tidy the Maria sheet before the last export.** Rows 59–69 are
      a column-shifted copy of 70–80 (row 69 ends with a pasted chat
      sentence), row 57 a copy of 58, rows 56–57 dated 2026-09-19/20. Delete
      the copies, fix the dates, then re-run
      `uv run --with openpyxl python docs/evaluation/testing-log/export.py <workbook>`.
      Row numbers will shift, so the record and Appendix E citations must be
      re-checked after that export, or the copies are left in place and the
      citations stand.
- [ ] **Erez: A2 (flagged-proposal approval) is still unmeasured** (row 148);
      the "set the fire rating to 60 minutes" prompt produces a flagged row and
      is the one to use. Optional before the tag.
- [ ] **Maria checks the corrected references.** In the review copy each
      draft reference is struck through beside its replacement (Text2BIM is
      Du et al.; MCP4IFC is Nithyanantham et al.; the rework paper is Love and
      Li; the FMI and PlanGrid report says 48 %; OmniDocBench co-authors; the
      ReAct / LangGraph sentence removed because the code does not use it).
      If she has a source for 52 %, cite that source instead. Every §8 line was edited; each
      person confirms their own. Pavla: §1, the ISO references, Appendix D.
      Islam: §4.4 (Schedule). Maria: §5.4 (her Solibri rows, `maria.csv`
      row 14; her rows 70–82 in Appendix E.4). Erez: §5.4, §6.3 and Appendix E
      (his rows 83–150, Table E.3).
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
- Appendix F's links point at the tag with line numbers read on 2026-09-16.
  If `src/` does not change before the tag they open on the right line; if it
  does, a link may open a few lines off. Accepted; no rebuild rule.
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

- [x] Sheet received 2026-09-16 (sheet Erez rows 84–97) and exported with row
      ids to `docs/evaluation/testing-log/erez.csv`.
- [x] Record written: `docs/evaluation/2026-09-16-expert-testing-v3.md`.
      It is a narrative log, so **no Cohen's κ** against the bake-off is
      possible; the record says so.
- [x] Re-run received 2026-09-19 (`FMP testing logs-v2.xlsx`: Erez rows
      98–150, Maria rows 52–82), re-exported, scored in
      `docs/evaluation/2026-09-19-expert-retest-v3.md`. Erez rows 108–150 are
      the paired protocol of `expert-rerun-protocol.md` (A1–C4) run once by
      one rater across `353775b → 872adff`. κ is still open, now for want of a
      second rater on the same items, not for want of a protocol.
- [ ] If a second rater scores Erez's B1 set or Maria's Round 1 prompts before
      the tag, that gives the κ row. Otherwise the memory cites both records
      and lists κ as open.

## 4. Still open, not blocking

- [ ] Expert labelling of ~40 outputs with κ (the rubric map's longest pole).
      Erez's sheet above is the first half of it.
- [ ] Memory diagrams (pipeline flow, RAG pipeline, RAV loop) and referencing.
      Figures can be generated from the JSON artifacts under `runs/`.
- [ ] 14B / 30B Modify rows: need a 12 GB / 24 GB machine. State as pending.
- [ ] hit@k / MRR for Ask; adversarial sandbox corpus. State as future work.

## 5. Before you press submit

- [ ] The word-count command above prints ≤ 5,000 on the final `_MAIN_v4.docx`
      (the build script is frozen; this check replaces its UNSOURCED /
      PROBLEM gate). Every new figure must be in a dated
      `docs/evaluation/` record before it goes into the Word file.
- [ ] Table 5.5 still matches `pytest --collect-only` at the tagged commit
      (2,695 in `src/` + 21 in `tests/e2e` on 2026-09-19; only the write-back
      and Playwright rows moved since 2026-09-16).
- [ ] `cd src && uv run pytest -q -p no:warnings` is green and
      `uv run ruff check` is clean at the tagged commit.
- [ ] The memory's and Appendix F's links open. They 404 until the tag is
      pushed; open a few line anchors to confirm they land on the definition.
