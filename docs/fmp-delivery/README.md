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

- `delivery-docs/`: what gets submitted. The team's originals
  (`CastorIQ_Final_Memory_MAIN.docx`, `…_APPENDICES.docx`), the testing
  spreadsheet, the judging criteria, the generated `…_v3.docx` files (to
  submit) and the `…_v3_REVIEW.docx` files (for the team only: colours,
  change table, tracked changes against the originals).
- `report-sections/`: the memory's text, one Markdown file per section, plus
  `appendix-e.md`. This is where the words are edited.
- `tools/`: `rewrite_memory.py` and `rewrite_appendices.py` build the
  `_v3.docx` files from the sections in the originals' own formatting and
  check every evaluative number against `docs/evaluation/`; with `--review`
  they also build the review copies, using `docx_redline.py` for the tracked
  changes; `code_links.py` turns Appendix F's references into links at the
  tag. `docs/evaluation/recount.py` recomputes the memory's tables from
  `runs/`.
- `figures/`: Figures 1, 3 and A.7 with the commands that regenerate them.

## Future work (not in the FMP scope)

- Fine-tuning a local model on execution-verified IfcOpenShell code, and a training
  suite that works on any base model (`qwen3-ifc` → `qwen4-ifc`):
  [`../brainstorming/ifc_code_model_training.md`](../brainstorming/ifc_code_model_training.md).
