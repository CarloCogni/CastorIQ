# fixtures/sample-project/render_pdfs.py
"""
One-shot renderer for the sample-project design docs.

Reads each .md sibling under docs/, parses the YAML-ish frontmatter for
project / discipline / document_no / revision / phase / issue_date, renders
the Markdown body to HTML via the `markdown` lib (already a project dep),
wraps it in a styled letterhead-ish HTML template, and writes a PDF next
to it via PyMuPDF's Story API.

Run:
    cd fixtures/sample-project && python render_pdfs.py

Outputs are committed alongside the .md sources. The .md files stay the
editable canonical source; the .pdf files are the deployable artefacts
the provisioning command picks up.

No new dependencies — `markdown` and `pymupdf` are both already in
pyproject.toml.
"""

from __future__ import annotations

import re
from pathlib import Path

import markdown as md
import pymupdf

DOCS_DIR = Path(__file__).resolve().parent / "docs"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a `---\\nkey: value\\n...\\n---\\nbody` document into (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    head = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    meta: dict[str, str] = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body


# Letterhead-ish wrapper. PyMuPDF's Story is happy with vanilla HTML +
# inline-ish CSS. Tables render acceptably; lists and headings work fine.
HTML_TEMPLATE = """\
<html>
  <head>
    <style>
      body {{ font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1a1a1a; line-height: 1.45; }}
      h1 {{ font-size: 18pt; font-weight: 700; margin: 8pt 0 4pt; color: #0b2a4a; }}
      h2 {{ font-size: 12pt; font-weight: 700; margin: 14pt 0 4pt; color: #0b2a4a; border-bottom: 0.5pt solid #c8d3df; padding-bottom: 2pt; }}
      h3 {{ font-size: 11pt; font-weight: 700; margin: 10pt 0 4pt; color: #0b2a4a; }}
      p  {{ margin: 4pt 0; }}
      ul, ol {{ margin: 4pt 0 6pt 18pt; padding: 0; }}
      li {{ margin: 1pt 0; }}
      table {{ border-collapse: collapse; margin: 6pt 0; width: 100%; }}
      th, td {{ border: 0.4pt solid #95a3b3; padding: 3pt 5pt; text-align: left; vertical-align: top; font-size: 9pt; }}
      th {{ background: #e6ecf3; font-weight: 700; }}
      blockquote {{ border-left: 2pt solid #c8a13a; background: #fdf6df; margin: 8pt 0; padding: 6pt 10pt; font-size: 9pt; color: #5a4814; }}
      blockquote p {{ margin: 2pt 0; }}
      code {{ font-family: 'Courier New', monospace; font-size: 9pt; background: #f0f2f5; padding: 0 2pt; }}
      hr {{ border: none; border-top: 0.3pt solid #c8d3df; margin: 12pt 0; }}
      .header {{ border-bottom: 1pt solid #0b2a4a; padding-bottom: 6pt; margin-bottom: 12pt; }}
      .header .title {{ font-size: 9pt; color: #5a6776; letter-spacing: 0.5pt; text-transform: uppercase; }}
      .header .project {{ font-size: 13pt; font-weight: 700; color: #0b2a4a; margin-top: 2pt; }}
      .meta-row {{ font-size: 8pt; color: #5a6776; margin-top: 4pt; }}
      .meta-row span {{ margin-right: 12pt; }}
      .meta-row b {{ color: #1a1a1a; font-weight: 600; }}
      .footer-note {{ font-size: 8pt; color: #5a6776; font-style: italic; margin-top: 18pt; text-align: center; }}
    </style>
  </head>
  <body>
    <div class="header">
      <div class="title">{discipline} · {phase}</div>
      <div class="project">{project}</div>
      <div class="meta-row">
        <span><b>Doc:</b> {document_no}</span>
        <span><b>Rev:</b> {revision}</span>
        <span><b>Issued:</b> {issue_date}</span>
        <span><b>By:</b> {prepared_by}</span>
      </div>
    </div>
    {body_html}
  </body>
</html>
"""


def _render_html(meta: dict[str, str], body_md: str) -> str:
    body_html = md.markdown(body_md, extensions=["tables", "fenced_code"])
    return HTML_TEMPLATE.format(
        project=meta.get("project", ""),
        discipline=meta.get("discipline", ""),
        document_no=meta.get("document_no", ""),
        revision=meta.get("revision", ""),
        phase=meta.get("phase", ""),
        issue_date=meta.get("issue_date", ""),
        prepared_by=meta.get("prepared_by", ""),
        body_html=body_html,
    )


def _html_to_pdf(html: str, out_path: Path) -> None:
    """Render `html` into a multi-page A4 PDF at `out_path` via PyMuPDF Story.

    The PyMuPDF Story API requires a DocumentWriter — `Story.draw()` takes
    the device returned by `DocumentWriter.begin_page()`, not a Page object.
    """
    story = pymupdf.Story(html=html)
    media = pymupdf.paper_rect("a4")
    margin = 50
    where = pymupdf.Rect(margin, margin, media.width - margin, media.height - margin)
    writer = pymupdf.DocumentWriter(str(out_path))
    more = 1
    while more:
        device = writer.begin_page(media)
        more, _ = story.place(where)
        story.draw(device, None)
        writer.end_page()
    writer.close()


def _strip_pre_code_fences(body: str) -> str:
    """Strip ```markdown … ``` fences that appear inside the disclaimer
    blockquote so the rendered PDF doesn't show an inert fenced block."""
    # No-op for the current docs; placeholder for future tightening.
    return body


def render_one(md_path: Path) -> Path:
    raw = md_path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)
    body = _strip_pre_code_fences(body)
    html = _render_html(meta, body)
    out = md_path.with_suffix(".pdf")
    _html_to_pdf(html, out)
    return out


def main() -> None:
    targets = sorted(DOCS_DIR.glob("*.md"))
    if not targets:
        print(f"No .md files found in {DOCS_DIR}.")
        return
    for t in targets:
        out = render_one(t)
        size_kb = out.stat().st_size // 1024
        print(f"  {t.name} -> {out.name} ({size_kb} KB)")


if __name__ == "__main__":
    main()
