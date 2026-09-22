# Memory section sources

One Markdown file per section of `CastorIQ_Final_Memory_MAIN.docx`, in
document order, plus `appendix-e.md` and `appendix-f.md` for the appendices. The words are
reviewed here, in git, not in Word.

Build (from the repository root):

```bash
uv run --with python-docx python docs/fmp-delivery/tools/rewrite_memory.py --review
uv run --with python-docx python docs/fmp-delivery/tools/rewrite_appendices.py --review
```

| Output in `delivery-docs/` | For |
|---|---|
| `CastorIQ_Final_Memory_MAIN_v3.docx`, `…_APPENDICES_v3.docx` | submission: no colours, no review notes, no tracked changes |
| `…_MAIN_v3_REVIEW.docx`, `…_APPENDICES_v3_REVIEW.docx` | the team: colours, the legend and "what changed" table (`review-legend.md`, `review-changes.md`, `review-legend-appendices.md`), and Word tracked changes against the team's original drafts |

Without `--review` only the submission files are written. The memory build
refuses to save on an evaluative number missing from `docs/evaluation/`, a
figure that `docs/evaluation/recount.py` recomputes from the run files but
cannot find in its table row, a withdrawn figure, or more than 5,000 counted
words. The appendices build refuses to save when an Appendix F link names a
missing file or symbol, or a file git does not track. The review build refuses
to save unless accepting every change gives the submission text plus the
review notes, and rejecting every change gives the original draft.

Syntax the tools understand:

- `# Title` (Heading 1), `## n.n Title` (Heading 2), `### Title` (Heading 3);
  in the review notes, `##` is a callout label.
- Blank-line-separated paragraphs; `- ` bullets; `Figure n.` and `Table n.n.`
  captions; GitHub-style tables.
- Inline `**bold**`, `` `code` ``, `[label](url)`.
- `[^N]`: a reference to footnote id N of the original draft (1 STEP format,
  3 OCR figures, 5 security test commit). The clean build drops footnotes
  nothing references; `FOOTNOTE_TEXT` in `rewrite_memory.py` rewords one.
- Review colours, rendered only in the `_REVIEW` copies:
  `{yellow}…{/yellow}` written without a source, `{cyan}…{/cyan}` synthesised
  from the testing log, `{magenta}…{/magenta}` mentor flag not yet addressed,
  `{green}…{/green}` needs a named teammate's check. Wrap whole paragraphs,
  headings or a phrase; do not put a marker inside `**bold**`.
- `[[DRAWING n]]` keeps the draft's existing image n; `[[IMAGE path]]`
  inserts a new one.
- In `appendix-f.md` only: `[[path]]` and `[[path::symbol]]` become links to
  the repository at the tag `fmp-final`, to the line that defines the symbol
  (`tools/code_links.py`). The line numbers are read when the document is
  built, so rebuild on the commit you tag.

Section files 01–08 get a `§ n` label paragraph; 00 and 09–11 do not.
