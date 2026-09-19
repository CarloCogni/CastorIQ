# FMP delivery — tracking folder

Single place for Final Master's Project delivery readiness. **Due 27 Sep 2026,
15:59** (Canvas: MAICEN-1125-M10, 10 pts; unlock prerequisites: both
satisfaction surveys + second group feedback survey — submit those early).

Rules for this folder:

- Evidence lives where it lives (`docs/evaluation/`, `docs/`, `fixtures/`,
  code, the report). This folder only *points* at it — nothing is duplicated
  here, so nothing here can go stale silently.
- [`rubric-map.md`](rubric-map.md) is the working document: rubric criterion →
  existing evidence → open gaps, each with a status marker.
- [`next-steps.md`](next-steps.md) is the checklist to submission: what only
  Carlo can do, what to ask teammates for and where to upload it, and the
  final checks before the tag.
- Delivery artifacts drafted specifically for submission (report outline,
  figure list) may live here; anything reusable moves to `docs/` proper.

Layout:

- `delivery-docs/`: what gets submitted. **Not tracked by git since
  2026-09-19** (`.gitignore`); the Word files are the team's master copy and
  live on disk and in the team's share. Current files: the team's
  `…_v2-to-v3_REVIEW.docx` (their edits on the generated v2), the corrected
  `…_MAIN_v4.docx` and `…_APPENDICES_v4.docx` (the files to export to PDF),
  the testing spreadsheet (`FMP testing logs-v2.xlsx`, exported to
  `docs/evaluation/testing-log/` which is the tracked copy) and the judging
  criteria.
- `report-sections/`: the memory's text, one Markdown file per section, plus
  `appendix-e.md`. This is where the words are edited.
- `tools/`: `rewrite_memory.py` and `rewrite_appendices.py` built the v2
  files from the sections in the originals' own formatting and checked every
  evaluative number against `docs/evaluation/`. **Frozen since 2026-09-16**:
  the Word files are edited directly. Their helpers are still used read-only
  (`docx_markdown.body_text` for the word count, the `FRACTION` / `DECIMAL`
  scan for unsourced figures); `code_links.py` turns Appendix F's references
  into links at the tag. `docs/evaluation/recount.py` recomputes the memory's
  tables from `runs/`.
- `figures/`: Figures 1, 3 and A.7 with the commands that regenerate them.

## Future work (not in the FMP scope)

- Fine-tuning a local model on execution-verified IfcOpenShell code, and a training
  suite that works on any base model (`qwen3-ifc` → `qwen4-ifc`):
  [`../brainstorming/ifc_code_model_training.md`](../brainstorming/ifc_code_model_training.md).
