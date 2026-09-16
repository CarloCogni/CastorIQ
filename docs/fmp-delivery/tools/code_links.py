# docs/fmp-delivery/tools/code_links.py
"""Turn ``[[path]]`` and ``[[path::symbol]]`` into links to the repository at the tag.

``[[src/app/module.py::name]]`` becomes a Markdown link to the line that
defines ``name`` (``def``, ``class`` or a module-level assignment) in the
tagged tree; ``[[path::"--flag"]]`` links to the first line containing the
quoted text; ``[[path]]`` links to a file or, with a trailing slash, a
folder. Line numbers are read from the working tree at build time, so the
documents must be rebuilt on the commit that gets tagged. A path or symbol
that does not exist is an error, never a dead link.
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY = "https://github.com/CarloCogni/CastorIQ"
TAG = "fmp-final"
LINK = re.compile(r"\[\[([^\]:]+?)(?:::([^\]]+))?\]\]")


class CodeLinkError(ValueError):
    """A ``[[…]]`` reference names a file or symbol that does not exist."""


def line_of(path: Path, symbol: str) -> int:
    """1-based line that defines ``symbol`` in ``path`` (or contains a quoted literal)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if symbol.startswith('"'):
        pattern = re.compile(re.escape(symbol))
    else:
        name = re.escape(symbol)
        pattern = re.compile(rf"^\s*(?:async\s+def|def|class)\s+{name}\b|^{name}\s*[:=]")
    for number, line in enumerate(lines, start=1):
        if pattern.search(line):
            return number
    raise CodeLinkError(f"{symbol} not found in {path}")


def resolve(text: str, root: Path) -> str:
    """Replace every ``[[…]]`` reference in ``text`` with a Markdown link."""

    def link(match: re.Match) -> str:
        relative, symbol = match.group(1).strip(), match.group(2)
        target = root / relative
        if not target.exists():
            raise CodeLinkError(f"{relative} does not exist")
        if target.is_dir():
            return f"[`{relative.rstrip('/')}/`]({REPOSITORY}/tree/{TAG}/{relative.rstrip('/')})"
        url = f"{REPOSITORY}/blob/{TAG}/{relative}"
        if symbol is None:
            return f"[`{relative}`]({url})"
        symbol = symbol.strip()
        label = f"{target.name}::{symbol.strip(chr(34))}"
        return f"[`{label}`]({url}#L{line_of(target, symbol)})"

    return LINK.sub(link, text)
