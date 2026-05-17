#!/usr/bin/env python3
# .claude/hooks/ruff_on_edit.py
"""PostToolUse hook: run ruff on the just-edited file if it's a Python file under src/.

Reads the Claude Code hook JSON payload from stdin, gates by path, and invokes
`uv tool run ruff` (a.k.a. `uvx`) so it works regardless of project venv sync state.
Always exits 0 — formatting must never block the user's work.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _resolve_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "uv.exe"
    return str(fallback) if fallback.exists() else None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not file_path:
        return 0

    project_root = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or ""
    if not project_root:
        return 0

    try:
        target = Path(file_path).resolve()
        rel = target.relative_to(Path(project_root).resolve())
    except (ValueError, OSError):
        return 0

    if not rel.parts or rel.parts[0] != "src" or target.suffix != ".py":
        return 0

    uv = _resolve_uv()
    if not uv:
        return 0

    for cmd in (
        [uv, "tool", "run", "ruff", "check", str(target), "--fix", "--quiet"],
        [uv, "tool", "run", "ruff", "format", str(target), "--quiet"],
    ):
        result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)
        err = (result.stderr or "").strip()
        if err:
            sys.stderr.write(err + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
