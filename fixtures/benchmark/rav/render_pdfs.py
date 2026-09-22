# fixtures/benchmark/rav/render_pdfs.py
"""
Render the RAV corpus documents (docs/*.md) to PDF.

Reuses the sample-project renderer so the benchmark documents look like the
demo documents: same letterhead, same table styling. The .md files are the
editable source; the .pdf files are what ``manage.py benchmark_rav --setup``
uploads.

Run:
    uv run python fixtures/benchmark/rav/render_pdfs.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS_DIR = HERE / "docs"
SAMPLE_RENDERER = HERE.parent.parent / "sample-project" / "render_pdfs.py"


def _load_sample_renderer():
    spec = importlib.util.spec_from_file_location("sample_render_pdfs", SAMPLE_RENDERER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    renderer = _load_sample_renderer()
    sources = sorted(DOCS_DIR.glob("*.md"))
    if not sources:
        print(f"no .md sources under {DOCS_DIR}", file=sys.stderr)
        return 1
    for source in sources:
        out = renderer.render_one(source)
        print(f"rendered {out.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
