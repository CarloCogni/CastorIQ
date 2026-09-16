# Memory section sources

One Markdown file per section of `CastorIQ_Final_Memory_MAIN.docx`, in
document order. `../tools/rewrite_memory.py` transports them into the docx
(styles copied from the existing document) and writes `…_MAIN_v3.docx`.
The words are reviewed here, in git, not in Word.

Format the script understands: `# Title` (Heading 1), `## n.n Title`
(Heading 2), blank-line-separated paragraphs (body), a paragraph starting
with `**Term**` (leading bold run), `Figure n.` / `Table n.n.` (captions),
`[[DRAWING n]]` (keep the document's existing image n at this spot),
`[[IMAGE path]]` (insert a new image), and GitHub-style tables.
Section files 01–08 get a `§ n` label paragraph; 00 and 09–11 do not.
