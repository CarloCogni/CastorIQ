# core/management/commands/dump_context.py
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                     CASTOR — dump_context Management Command               ║
║               Smart Project Context Dumper for LLM-Assisted Development    ║
╚══════════════════════════════════════════════════════════════════════════════╝

PURPOSE:
    Generates a focused, token-efficient snapshot of your codebase that you can
    paste into an LLM chat session. Supports full dumps, app-specific dumps,
    AST skeleton mode (signatures only), project tree, selective docs, git diff
    mode, grep filtering, presets, and token estimation.

    Output is saved to _output/ with timestamps for history tracking.

QUICK REFERENCE:
    ┌────────────────────────────────────────────────────────────────────────┐
    │  python manage.py dump_context                     Full project dump  │
    │  python manage.py dump_context --apps writeback    Single app         │
    │  python manage.py dump_context --skeleton          AST skeletons only │
    │  python manage.py dump_context --tree              Project tree only  │
    │  python manage.py dump_context --docs all          Include all docs   │
    │  python manage.py dump_context --diff HEAD~3       Changed files only │
    │  python manage.py dump_context --grep FireRating   Files with pattern │
    │  python manage.py dump_context --preset writeback  Named preset       │
    │  python manage.py dump_context --compact           ~30-40% smaller    │
    │  python manage.py dump_context --estimate          Token count only   │
    └────────────────────────────────────────────────────────────────────────┘

USAGE EXAMPLES:

    1. FULL PROJECT DUMP:
       $ python manage.py dump_context

    2. DUMP SPECIFIC APPS:
       $ python manage.py dump_context --apps writeback chat

    3. AST SKELETON MODE (signatures only, ~80% smaller):
       $ python manage.py dump_context --skeleton

    4. PROJECT TREE (full structure with line counts + token estimates):
       $ python manage.py dump_context --tree
       Shows the entire project directory tree from the repo root.
       Great for giving an LLM spatial awareness without any code tokens.

    5. TREE + SKELETON (the orientation combo):
       $ python manage.py dump_context --tree --skeleton
       Tree for structure, skeletons for API surface.

    6. SELECTIVE DOCS:
       $ python manage.py dump_context --docs all
       $ python manage.py dump_context --docs architecture conventions
       $ python manage.py dump_context --docs writeback
       Includes docs from docs/ by name, folder, or all. No .md needed.

    7. GIT DIFF MODE (only changed files):
       $ python manage.py dump_context --diff HEAD~3
       $ python manage.py dump_context --diff main
       Only includes files that changed since a git ref. Perfect for
       "here's what I changed, help me debug" sessions.

    8. GREP MODE (files containing a pattern):
       $ python manage.py dump_context --grep ModificationProposal
       Only includes Python files that contain the pattern. Combine with
       --skeleton for everything else.

    9. MODELS ONLY:
       $ python manage.py dump_context --models-only

    10. FULL CODE FOR ONE APP, SKELETON FOR THE REST:
        $ python manage.py dump_context --apps writeback --full-apps writeback
        Full code for writeback, AST skeletons for everything else.

    11. PRESETS (named combos):
        $ python manage.py dump_context --preset writeback
        $ python manage.py dump_context --preset overview
        $ python manage.py dump_context --preset models
        See BUILT-IN PRESETS below, or define custom ones in .dump_presets.json

    12. TOKEN ESTIMATION:
        $ python manage.py dump_context --estimate
        $ python manage.py dump_context --estimate --apps writeback

    13. LIST PRESETS:
        $ python manage.py dump_context --list-presets

COMBINING FLAGS — REAL WORKFLOW EXAMPLES:

    "I'm starting a new LLM chat to work on Tier 2 validation":
    $ python manage.py dump_context --preset writeback

    "I need the LLM to understand my whole project structure":
    $ python manage.py dump_context --tree --docs architecture

    "Debug this issue — here's what I changed recently":
    $ python manage.py dump_context --diff HEAD~5 --docs all

    "Find everything related to RAV":
    $ python manage.py dump_context --grep "RAV\|guardian\|verification"

    "Quick data model context":
    $ python manage.py dump_context --preset models

BUILT-IN PRESETS:
    ┌──────────────┬──────────────────────────────────────────────────────┐
    │  writeback   │ --apps writeback --skeleton --full-apps writeback   │
    │              │ --docs writeback guardian                            │
    ├──────────────┼──────────────────────────────────────────────────────┤
    │  overview    │ --tree --skeleton --docs all                        │
    ├──────────────┼──────────────────────────────────────────────────────┤
    │  models      │ --models-only --docs architecture data-models       │
    ├──────────────┼──────────────────────────────────────────────────────┤
    │  rag         │ --apps embeddings documents chat --skeleton          │
    │              │ --full-apps embeddings --docs rag-pipeline           │
    ├──────────────┼──────────────────────────────────────────────────────┤
    │  ifc         │ --apps ifc_processor --skeleton                     │
    │              │ --full-apps ifc_processor --docs ifc-processor       │
    └──────────────┴──────────────────────────────────────────────────────┘

CUSTOM PRESETS (.dump_presets.json in project root):
    {
      "my-preset": {
        "apps": ["writeback", "chat"],
        "skeleton": true,
        "full_apps": ["writeback"],
        "docs": ["architecture", "writeback"],
        "tree": true
      }
    }

OUTPUT:
    Files are saved to: src/core/management/commands/_output/
    Naming: YYYY-MM-DD__HH-MM-SS__dump-context__<descriptor>.txt
    A symlink _latest.txt always points to the most recent dump.

TOKEN BUDGET GUIDELINES:
    ┌──────────────────────┬────────────────────────────┐
    │  Context Window      │  Suggested --max-tokens    │
    ├──────────────────────┼────────────────────────────┤
    │  Claude Sonnet (200k)│  30000-50000               │
    │  Claude Opus (200k)  │  30000-50000               │
    │  Llama 3.1:8b (128k) │  10000-20000               │
    │  GPT-4o (128k)       │  15000-30000               │
    └──────────────────────┴────────────────────────────┘
    Rule of thumb: leave 60-70% of context for conversation + response.
"""

import ast
import fnmatch
import json
import logging
import os
import re
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


# ── Token Estimation ───────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~1 token per 4 characters for code."""
    return len(text) // 4


def format_tokens(count: int) -> str:
    """Human-readable token count."""
    if count >= 1000:
        return f"{count / 1000:.1f}k"
    return str(count)


# ── AST Skeleton Extraction ───────────────────────────────────────────────

def extract_skeleton(file_path: str) -> str:
    """
    Extract an AST skeleton from a Python file.

    Returns class definitions with field types and method signatures,
    and top-level function signatures — without implementation bodies.
    This preserves the STRUCTURE of the code for LLM understanding
    while cutting ~80% of the tokens.
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            source = f.read()
    except Exception:
        return f"# Could not read {file_path}\n"

    if not source.strip():
        return "# (Empty file)\n"

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return f"# SyntaxError: {e}\n"

    lines = []

    # Collect top-level imports (condensed)
    imports = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            names = ', '.join(a.name for a in node.names)
            imports.append(f"from {module} import {names}")
    if imports:
        if len(imports) <= 10:
            for imp in imports:
                if not imp.startswith('from'):
                    lines.append(f"import {imp}")
                else:
                    lines.append(imp)
        else:
            for imp in imports[:8]:
                if not imp.startswith('from'):
                    lines.append(f"import {imp}")
                else:
                    lines.append(imp)
            lines.append(f"# ... and {len(imports) - 8} more imports")
        lines.append("")

    # Process top-level definitions
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            lines.append(_extract_class(node, source))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(_extract_function(node, source, indent=0))
        elif isinstance(node, ast.Assign):
            segment = ast.get_source_segment(source, node)
            if segment and len(segment) < 120:
                lines.append(segment)

    return "\n".join(lines) + "\n"


def _extract_function(node, source: str, indent: int = 0) -> str:
    """Extract a function signature with its docstring."""
    prefix = "    " * indent
    async_prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""

    args_segment = ast.get_source_segment(source, node.args)
    if args_segment is None:
        args_parts = []
        for arg in node.args.args:
            annotation = ""
            if arg.annotation:
                ann_seg = ast.get_source_segment(source, arg.annotation)
                annotation = f": {ann_seg}" if ann_seg else ""
            args_parts.append(f"{arg.arg}{annotation}")
        args_segment = ", ".join(args_parts)

    returns = ""
    if node.returns:
        ret_seg = ast.get_source_segment(source, node.returns)
        if ret_seg:
            returns = f" -> {ret_seg}"

    decorators = []
    for dec in node.decorator_list:
        dec_seg = ast.get_source_segment(source, dec)
        if dec_seg:
            decorators.append(f"{prefix}@{dec_seg}")

    docstring = ""
    if (node.body and isinstance(node.body[0], ast.Expr) and
            isinstance(node.body[0].value, (ast.Constant, ast.Str))):
        doc_value = node.body[0].value
        if isinstance(doc_value, ast.Constant):
            raw_doc = str(doc_value.value)
        else:
            raw_doc = doc_value.s
        if len(raw_doc) > 150:
            raw_doc = raw_doc[:147] + "..."
        docstring = f'\n{prefix}    """{raw_doc}"""'

    result_parts = decorators
    result_parts.append(
        f"{prefix}{async_prefix}def {node.name}({args_segment}){returns}:{docstring}"
    )
    if not docstring:
        result_parts[-1] += " ..."

    return "\n".join(result_parts)


def _extract_class(node, source: str) -> str:
    """Extract a class with its bases, fields, and method signatures."""
    bases = []
    for base in node.bases:
        base_seg = ast.get_source_segment(source, base)
        if base_seg:
            bases.append(base_seg)
    bases_str = f"({', '.join(bases)})" if bases else ""

    lines = []
    for dec in node.decorator_list:
        dec_seg = ast.get_source_segment(source, dec)
        if dec_seg:
            lines.append(f"@{dec_seg}")

    lines.append(f"class {node.name}{bases_str}:")

    if (node.body and isinstance(node.body[0], ast.Expr) and
            isinstance(node.body[0].value, (ast.Constant, ast.Str))):
        doc_value = node.body[0].value
        raw_doc = (
            str(doc_value.value) if isinstance(doc_value, ast.Constant) else doc_value.s
        )
        if len(raw_doc) > 150:
            raw_doc = raw_doc[:147] + "..."
        lines.append(f'    """{raw_doc}"""')

    has_content = False

    for item in node.body:
        if isinstance(item, ast.Expr) and isinstance(item.value, (ast.Constant, ast.Str)):
            continue

        if isinstance(item, ast.Assign):
            segment = ast.get_source_segment(source, item)
            if segment:
                if len(segment) > 120:
                    segment = segment[:117] + "..."
                lines.append(f"    {segment.strip()}")
                has_content = True

        elif isinstance(item, ast.AnnAssign):
            segment = ast.get_source_segment(source, item)
            if segment and len(segment) < 120:
                lines.append(f"    {segment.strip()}")
                has_content = True

        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(_extract_function(item, source, indent=1))
            has_content = True

        elif isinstance(item, ast.ClassDef):
            inner_bases = []
            for base in item.bases:
                seg = ast.get_source_segment(source, base)
                if seg:
                    inner_bases.append(seg)
            inner_bases_str = f"({', '.join(inner_bases)})" if inner_bases else ""
            lines.append(f"    class {item.name}{inner_bases_str}: ...")
            has_content = True

    if not has_content:
        lines.append("    pass")

    return "\n".join(lines) + "\n"


# ── Project Tree Generator ────────────────────────────────────────────────

def generate_project_tree(
    root_dir: Path,
    ignore_dirs: set,
    ignore_files: set,
    include_exts: set,
    max_depth: int = 10,
) -> str:
    """
    Generate a project directory tree with line counts and token estimates.

    Returns a formatted tree string like:
        castor/
        ├── src/
        │   ├── config/
        │   │   ├── settings/
        │   │   │   ├── base.py          (245 lines, ~3.2k tokens)
        ...
    """
    lines = []
    _tree_walk(root_dir, root_dir, ignore_dirs, ignore_files, include_exts,
               lines, prefix="", max_depth=max_depth, current_depth=0)
    return "\n".join(lines)


def _tree_walk(
    current: Path,
    root: Path,
    ignore_dirs: set,
    ignore_files: set,
    include_exts: set,
    lines: list,
    prefix: str,
    max_depth: int,
    current_depth: int,
):
    """Recursive tree walker with visual connectors."""
    if current_depth > max_depth:
        lines.append(f"{prefix}└── ... (max depth reached)")
        return

    try:
        entries = sorted(current.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return

    # Filter entries
    filtered = []
    for entry in entries:
        if entry.name.startswith('.') and entry.name not in ('.env.example',):
            continue
        if entry.is_dir():
            if entry.name in ignore_dirs:
                continue
            filtered.append(entry)
        else:
            if entry.name in ignore_files:
                continue
            filtered.append(entry)

    for i, entry in enumerate(filtered):
        is_last = (i == len(filtered) - 1)
        connector = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")

        if entry.is_dir():
            lines.append(f"{prefix}{connector}{entry.name}/")
            _tree_walk(entry, root, ignore_dirs, ignore_files, include_exts,
                       lines, child_prefix, max_depth, current_depth + 1)
        else:
            # File metadata
            meta = ""
            ext = entry.suffix.lower()
            if ext in include_exts or ext in {'.md', '.txt', '.cfg', '.toml', '.yml', '.yaml'}:
                try:
                    content = entry.read_text(encoding='utf-8', errors='ignore')
                    line_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
                    tokens = estimate_tokens(content)
                    meta = f"  ({line_count} lines, ~{format_tokens(tokens)} tokens)"
                except Exception:
                    meta = ""

            lines.append(f"{prefix}{connector}{entry.name}{meta}")


# ── Compact Tree Generator ─────────────────────────────────────────────────

def generate_compact_tree(
    root_dir: Path,
    ignore_dirs: set,
    ignore_files: set,
    include_exts: set,
    max_depth: int = 10,
) -> str:
    """
    Generate a token-efficient project tree using 2-space indentation.

    No unicode box-drawing characters — saves ~50% tokens vs pretty tree.
    Example:
      src/
        config/
          settings/
            base.py              245 lines  ~3.2k tok
            local.py              18 lines  ~0.2k tok
        writeback/
          models.py              271 lines  ~2.4k tok
    """
    lines = []
    _compact_tree_walk(
        root_dir, ignore_dirs, ignore_files, include_exts,
        lines, depth=0, max_depth=max_depth,
    )
    return "\n".join(lines)


def _compact_tree_walk(
    current: Path,
    ignore_dirs: set,
    ignore_files: set,
    include_exts: set,
    lines: list,
    depth: int,
    max_depth: int,
):
    """Recursive compact tree walker — 2-space indentation, no connectors."""
    if depth > max_depth:
        lines.append(f"{'  ' * depth}... (max depth)")
        return

    try:
        entries = sorted(current.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return

    for entry in entries:
        if entry.name.startswith('.') and entry.name not in ('.env.example',):
            continue
        if entry.is_dir():
            if entry.name in ignore_dirs:
                continue
            lines.append(f"{'  ' * depth}{entry.name}/")
            _compact_tree_walk(
                entry, ignore_dirs, ignore_files, include_exts,
                lines, depth + 1, max_depth,
            )
        else:
            if entry.name in ignore_files:
                continue
            meta = ""
            ext = entry.suffix.lower()
            if ext in include_exts or ext in {'.md', '.txt', '.cfg', '.toml', '.yml', '.yaml'}:
                try:
                    content = entry.read_text(encoding='utf-8', errors='ignore')
                    lc = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
                    tok = estimate_tokens(content)
                    meta = f"  {lc} lines  ~{format_tokens(tok)} tok"
                except Exception:
                    pass
            lines.append(f"{'  ' * depth}{entry.name}{meta}")


# ── Text Compression (--compact) ──────────────────────────────────────────

def strip_comments(source: str) -> str:
    """
    Remove comments and docstrings from Python source code via AST.

    Preserves all functional code and structure. Removes:
    - Single-line comments (# ...)
    - Module/class/function docstrings (triple-quoted strings)
    Saves ~15-25% tokens on typical Django code.
    """
    # Step 1: Remove docstrings via AST
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # If we can't parse, just strip line comments
        return _strip_line_comments(source)

    # Collect line ranges of docstrings to remove
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr) and
                    isinstance(node.body[0].value, (ast.Constant, ast.Str))):
                doc_node = node.body[0]
                for line_no in range(doc_node.lineno, doc_node.end_lineno + 1):
                    docstring_lines.add(line_no)

    # Step 2: Process line by line
    result_lines = []
    for i, line in enumerate(source.splitlines(), 1):
        # Skip docstring lines
        if i in docstring_lines:
            continue

        # Strip inline comments (but not strings containing #)
        stripped = _strip_inline_comment(line)

        # Skip pure comment lines
        if stripped.strip() == '':
            # Was this a comment-only line? (original had content)
            if line.strip() and line.strip().startswith('#'):
                continue
            # Keep blank lines (collapsed separately)
            result_lines.append('')
            continue

        result_lines.append(stripped)

    return '\n'.join(result_lines)


def _strip_line_comments(source: str) -> str:
    """Fallback: strip # comments without AST (for non-parseable files)."""
    lines = []
    for line in source.splitlines():
        stripped = _strip_inline_comment(line)
        if stripped.strip() == '' and line.strip().startswith('#'):
            continue
        lines.append(stripped)
    return '\n'.join(lines)


def _strip_inline_comment(line: str) -> str:
    """
    Remove trailing # comment from a line, respecting strings.

    'hello # comment' → 'hello'
    'x = "a # b"'     → 'x = "a # b"'  (inside string, preserved)
    '# full comment'   → ''
    """
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '\\' and i + 1 < len(line):
            i += 2  # skip escaped character
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '#' and not in_single and not in_double:
            return line[:i].rstrip()
        i += 1
    return line


def collapse_blank_lines(text: str) -> str:
    """Collapse multiple consecutive blank lines into a single one."""
    return re.sub(r'\n{3,}', '\n\n', text)


# ── Git Diff ──────────────────────────────────────────────────────────────

def get_git_changed_files(project_root: Path, ref: str) -> set[str]:
    """
    Get files changed since a git ref (branch, tag, or HEAD~N).

    Returns a set of file paths relative to the project root.
    """
    try:
        result = subprocess.run(
            ['git', 'diff', '--name-only', ref],
            capture_output=True, text=True, cwd=project_root,
        )
        if result.returncode != 0:
            # Try as a branch comparison
            result = subprocess.run(
                ['git', 'diff', '--name-only', f'{ref}...HEAD'],
                capture_output=True, text=True, cwd=project_root,
            )
        if result.returncode != 0:
            raise CommandError(
                f"Git diff failed: {result.stderr.strip()}\n"
                f"Make sure '{ref}' is a valid git ref (branch, tag, or HEAD~N)."
            )
        files = {f.strip() for f in result.stdout.strip().split('\n') if f.strip()}

        # Also include uncommitted changes
        staged = subprocess.run(
            ['git', 'diff', '--name-only', '--cached'],
            capture_output=True, text=True, cwd=project_root,
        )
        if staged.returncode == 0:
            files |= {f.strip() for f in staged.stdout.strip().split('\n') if f.strip()}

        unstaged = subprocess.run(
            ['git', 'diff', '--name-only'],
            capture_output=True, text=True, cwd=project_root,
        )
        if unstaged.returncode == 0:
            files |= {f.strip() for f in unstaged.stdout.strip().split('\n') if f.strip()}

        return files
    except FileNotFoundError:
        raise CommandError("Git is not installed or not in PATH.")


# ── Grep ──────────────────────────────────────────────────────────────────

def file_matches_grep(file_path: Path, pattern: str) -> bool:
    """Check if a file contains a regex pattern."""
    try:
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        return bool(re.search(pattern, content))
    except Exception:
        return False


# ── Presets ───────────────────────────────────────────────────────────────

DEFAULT_PRESETS = {
    "_comment": "Presets for dump_context. Edit freely — this is the single source of truth.",
    "writeback": {
        "apps": ["writeback"],
        "skeleton": True,
        "full_apps": ["writeback"],
        "docs": ["writeback", "guardian"],
    },
    "overview": {
        "tree": True,
        "skeleton": True,
        "docs": ["all"],
    },
    "models": {
        "models_only": True,
        "docs": ["architecture", "data-models"],
    },
    "rag": {
        "apps": ["embeddings", "documents", "chat"],
        "skeleton": True,
        "full_apps": ["embeddings"],
        "docs": ["rag-pipeline"],
    },
    "ifc": {
        "apps": ["ifc_processor"],
        "skeleton": True,
        "full_apps": ["ifc_processor"],
        "docs": ["ifc-processor"],
    },
}

PRESETS_FILENAME = '.dump_presets.json'


def _get_presets_path(project_root: Path) -> Path:
    """Return the path to the presets config file."""
    return project_root / PRESETS_FILENAME


def init_presets(project_root: Path) -> Path:
    """Create the presets JSON file with defaults. Returns the file path."""
    config_path = _get_presets_path(project_root)
    with open(config_path, 'w') as f:
        json.dump(DEFAULT_PRESETS, f, indent=2)
    return config_path


def load_presets(project_root: Path) -> dict:
    """
    Load presets from the JSON config file.

    If the file doesn't exist, creates it with defaults automatically.
    Keys starting with '_' (like _comment) are ignored.
    """
    config_path = _get_presets_path(project_root)

    if not config_path.exists():
        init_presets(project_root)
        logger.info(f"Created {PRESETS_FILENAME} with default presets.")

    try:
        with open(config_path, 'r') as f:
            all_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load {PRESETS_FILENAME}: {e} — using defaults.")
        all_data = dict(DEFAULT_PRESETS)

    # Filter out meta keys (keys starting with '_')
    return {k: v for k, v in all_data.items() if not k.startswith('_')}


# ── Docs Resolver ─────────────────────────────────────────────────────────

def resolve_docs(docs_dir: Path, doc_names: list[str]) -> list[Path]:
    """
    Resolve doc names to file paths.

    Supports:
      - "all" → everything in docs/
      - "architecture" → docs/architecture.md
      - "writeback" → docs/writeback/ (all files inside)
      - "tier1-reference" → docs/writeback/tier1-reference.md (fuzzy find)
    """
    if not docs_dir.exists():
        return []

    if doc_names == ["all"] or "all" in doc_names:
        # Collect all .md files recursively
        return sorted(docs_dir.rglob("*.md"))

    resolved = []
    all_md_files = list(docs_dir.rglob("*.md"))

    for name in doc_names:
        name_clean = name.replace('.md', '').strip()

        # Check if it's a directory name
        candidate_dir = docs_dir / name_clean
        if candidate_dir.is_dir():
            resolved.extend(sorted(candidate_dir.rglob("*.md")))
            continue

        # Check exact file match at any depth
        matched = False
        for md_file in all_md_files:
            stem = md_file.stem
            if stem == name_clean:
                resolved.append(md_file)
                matched = True
                break

        if not matched:
            # Fuzzy: check if name is contained in the stem
            for md_file in all_md_files:
                if name_clean.lower() in md_file.stem.lower():
                    resolved.append(md_file)
                    matched = True
                    break

        if not matched:
            logger.warning(f"Doc not found: '{name}' — skipping.")

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for p in resolved:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


# ── Output Naming ─────────────────────────────────────────────────────────

def generate_output_filename(options: dict) -> str:
    """Generate a descriptive timestamped filename based on active options."""
    timestamp = datetime.now().strftime("%Y-%m-%d__%H-%M-%S")
    parts = ["dump-context"]

    if options.get('preset'):
        parts.append(f"preset-{options['preset']}")
    else:
        if options.get('compact'):
            parts.append("compact")
        if options.get('tree'):
            parts.append("tree")
        if options.get('skeleton'):
            parts.append("skeleton")
        if options.get('models_only'):
            parts.append("models")
        if options.get('apps'):
            parts.append("-".join(options['apps'][:3]))
            if len(options['apps']) > 3:
                parts.append(f"+{len(options['apps']) - 3}more")
        if options.get('diff'):
            ref = options['diff'].replace('~', '').replace('/', '-')
            parts.append(f"diff-{ref}")
        if options.get('grep'):
            # Sanitize grep pattern for filename
            safe = re.sub(r'[^a-zA-Z0-9_-]', '', options['grep'][:20])
            parts.append(f"grep-{safe}")
        if options.get('docs'):
            if options['docs'] == ['all']:
                parts.append("docs-all")
            else:
                parts.append(f"docs-{len(options['docs'])}")

    descriptor = "__".join(parts)
    return f"{timestamp}__{descriptor}.txt"


# ── Main Command ──────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = (
        'Smart project context dumper for LLM-assisted development.\n'
        'Run with --help for full usage, or read the module docstring for examples.'
    )

    # ── Configuration ──────────────────────────────────────────────────
    INCLUDE_EXTS = {'.py', '.html', '.css', '.js', '.json', '.md'}

    IGNORE_DIRS = {
        '__pycache__', 'migrations', '.git', '.venv', 'venv', 'env',
        'static', 'staticfiles', 'media', 'node_modules',
        '.idea', '.vscode', '_output',
    }

    IGNORE_FILES = {
        'db.sqlite3', '.env', 'poetry.lock', 'uv.lock',
        'package-lock.json', 'project_context.txt',
    }

    def add_arguments(self, parser):
        # ── Filtering ──
        parser.add_argument(
            '--apps', nargs='+', type=str, default=None,
            help='Only include these Django app directories. E.g.: --apps writeback chat',
        )
        parser.add_argument(
            '--skeleton', action='store_true', default=False,
            help='AST skeleton mode: extract class/function signatures only (~80%% smaller).',
        )
        parser.add_argument(
            '--full-apps', nargs='+', type=str, default=None,
            help='When combined with --skeleton, these apps get FULL code instead of skeletons.',
        )
        parser.add_argument(
            '--models-only', action='store_true', default=False,
            help='Only include models.py from each app.',
        )
        parser.add_argument(
            '--files', nargs='+', type=str, default=None,
            help='Only include files with these exact names. E.g.: --files views.py serializers.py',
        )
        parser.add_argument(
            '--types', nargs='+', type=str, default=None,
            help='Only include files with these extensions (without dot). E.g.: --types py html',
        )

        # ── New features ──
        parser.add_argument(
            '--tree', action='store_true', default=False,
            help='Include full project directory tree with line counts and token estimates.',
        )
        parser.add_argument(
            '--tree-only', action='store_true', default=False,
            help='Output ONLY the project tree, no code files.',
        )
        parser.add_argument(
            '--tree-depth', type=int, default=10,
            help='Maximum depth for tree traversal (default: 10).',
        )
        parser.add_argument(
            '--docs', nargs='*', default=None,
            help='Include doc files from docs/. Use "all" for everything, or list names. '
                 'E.g.: --docs all | --docs architecture writeback',
        )
        parser.add_argument(
            '--diff', type=str, default=None,
            help='Only include files changed since this git ref. E.g.: --diff HEAD~3 | --diff main',
        )
        parser.add_argument(
            '--grep', type=str, default=None,
            help='Only include files containing this regex pattern. E.g.: --grep ModificationProposal',
        )
        parser.add_argument(
            '--preset', type=str, default=None,
            help='Use a named preset. E.g.: --preset writeback | --preset overview',
        )
        parser.add_argument(
            '--list-presets', action='store_true', default=False,
            help='List all available presets and exit.',
        )
        parser.add_argument(
            '--init-presets', action='store_true', default=False,
            help='Reset .dump_presets.json to defaults. WARNING: overwrites existing file.',
        )

        # ── Output control ──
        parser.add_argument(
            '--compact', action='store_true', default=False,
            help='Token-saving mode: compact tree, strip comments/docstrings, collapse blanks, '
                 'shorter headers. Saves ~30-40%% tokens with zero information loss for the LLM.',
        )
        parser.add_argument(
            '--estimate', action='store_true', default=False,
            help='Only estimate token count, do not write the output file.',
        )
        parser.add_argument(
            '--max-tokens', type=int, default=None,
            help='Maximum token budget. Output is truncated with a warning if exceeded.',
        )
        parser.add_argument(
            '--include-tests', action='store_true', default=False,
            help='Include test files (excluded by default).',
        )
        parser.add_argument(
            '-o', '--output', type=str, default=None,
            help='Custom output filename. Saved in _output/ directory.',
        )
        parser.add_argument(
            '--no-header', action='store_true', default=False,
            help='Skip the summary header in the output.',
        )
        # -- Admin files --
        parser.add_argument(
            '--include-admin', action='store_true', default=False,
            help='Include admin*.py files (excluded by default to save tokens).',
        )

    def handle(self, *args, **options):
        # ── Resolve paths ──────────────────────────────────────────────
        # settings.BASE_DIR = src/  →  project_root = parent (castor/)
        src_dir = settings.BASE_DIR
        project_root = src_dir.parent

        # Output directory: alongside the command file
        output_dir = Path(__file__).parent / '_output'
        output_dir.mkdir(exist_ok=True)

        # ── Init presets ───────────────────────────────────────────────
        if options['init_presets']:
            config_path = init_presets(project_root)
            self.stdout.write(self.style.SUCCESS(
                f"\n✅ Created {config_path}"
            ))
            self.stdout.write("   Edit this file to add, remove, or modify presets.")
            self.stdout.write("   Keys starting with '_' are ignored (use for comments).\n")
            return

        # ── List presets ───────────────────────────────────────────────
        if options['list_presets']:
            presets = load_presets(project_root)
            config_path = _get_presets_path(project_root)
            self.stdout.write(self.style.SUCCESS(f"\n📋 Presets from: {config_path}\n"))
            for name, config in sorted(presets.items()):
                self.stdout.write(f"  {self.style.WARNING(name)}")
                for key, value in config.items():
                    self.stdout.write(f"    {key}: {value}")
                self.stdout.write("")
            self.stdout.write(f"  Edit {PRESETS_FILENAME} to customize.\n")
            return

        # ── Apply preset (if specified) ────────────────────────────────
        if options['preset']:
            presets = load_presets(project_root)
            preset_name = options['preset']
            if preset_name not in presets:
                available = ', '.join(sorted(presets.keys()))
                raise CommandError(
                    f"Unknown preset: '{preset_name}'. Available: {available}"
                )
            preset = presets[preset_name]
            # Preset values are defaults — CLI flags override
            for key, value in preset.items():
                opt_key = key.replace('-', '_')
                # Only apply preset value if CLI didn't set it
                if opt_key in options:
                    cli_default = self._get_cli_default(opt_key)
                    if options[opt_key] == cli_default:
                        options[opt_key] = value

        # ── Unpack options ─────────────────────────────────────────────
        apps_filter = options.get('apps')
        full_apps = set(options.get('full_apps') or [])
        skeleton_mode = options.get('skeleton', False)
        models_only = options.get('models_only', False)
        files_filter = options.get('files')
        types_filter = options.get('types')
        include_tree = options.get('tree', False) or options.get('tree_only', False)
        tree_only = options.get('tree_only', False)
        tree_depth = options.get('tree_depth', 10)
        docs_names = options.get('docs')
        diff_ref = options.get('diff')
        grep_pattern = options.get('grep')
        estimate_only = options.get('estimate', False)
        max_tokens = options.get('max_tokens')
        compact = options.get('compact', False)
        include_tests = options.get('include_tests', False)
        output_name = options.get('output')
        no_header = options.get('no_header', False)

        if not include_tests:
            self.IGNORE_DIRS.add('tests')

        if types_filter:
            self.INCLUDE_EXTS = {f'.{t.lstrip(".")}' for t in types_filter}

        # ── Resolve git diff files ─────────────────────────────────────
        diff_files = None
        if diff_ref:
            diff_files = get_git_changed_files(project_root, diff_ref)
            self.stdout.write(
                f"🔀 Git diff from '{diff_ref}': {len(diff_files)} changed files"
            )


        # ── Print scan info ────────────────────────────────────────────
        self.stdout.write(f"\n📂 Project root: {project_root}")
        self.stdout.write(f"📂 Source dir:   {src_dir}")
        if options.get('preset'):
            self.stdout.write(f"📦 Preset: {options['preset']}")
        if apps_filter:
            self.stdout.write(f"📦 Apps filter: {', '.join(apps_filter)}")
        if skeleton_mode:
            label = "SKELETON mode"
            if full_apps:
                label += f" (full code for: {', '.join(full_apps)})"
            self.stdout.write(f"🦴 {label}")
        if models_only:
            self.stdout.write("📋 Models only")
        if grep_pattern:
            self.stdout.write(f"🔍 Grep: {grep_pattern}")
        if include_tree:
            self.stdout.write(f"🌲 Tree: depth={tree_depth}")
        if docs_names is not None:
            self.stdout.write(f"📖 Docs: {docs_names or ['all']}")
        if compact:
            self.stdout.write("📦 Compact mode (stripped comments, collapsed blanks, short headers)")

        # ── Collect content ────────────────────────────────────────────
        sections = []
        file_count = 0

        # 1. Project tree
        if include_tree:
            if compact:
                tree_content = generate_compact_tree(
                    project_root, self.IGNORE_DIRS, self.IGNORE_FILES,
                    self.INCLUDE_EXTS, max_depth=tree_depth,
                )
                sections.append(
                    f"--- PROJECT TREE ---\n\n"
                    f"{project_root.name}/\n"
                    f"{tree_content}\n"
                )
            else:
                tree_content = generate_project_tree(
                    project_root, self.IGNORE_DIRS, self.IGNORE_FILES,
                    self.INCLUDE_EXTS, max_depth=tree_depth,
                )
                sections.append(
                    f"{'=' * 70}\n"
                    f"📁 PROJECT TREE\n"
                    f"{'=' * 70}\n\n"
                    f"{project_root.name}/\n"
                    f"{tree_content}\n"
                )

        if tree_only:
            # Skip all code scanning, jump to output
            full_output = self._assemble_output(
                sections, file_count, project_root, options, no_header,
            )
            self._write_output(
                full_output, file_count, output_dir, output_name, options,
                estimate_only, max_tokens,
            )
            return

        # 2. Documentation files
        if docs_names is not None:
            docs_dir = project_root / 'docs'
            # If --docs with no arguments, treat as "all"
            if not docs_names:
                docs_names = ["all"]
            doc_paths = resolve_docs(docs_dir, docs_names)
            for doc_path in doc_paths:
                try:
                    content = doc_path.read_text(encoding='utf-8', errors='ignore')
                except Exception as e:
                    content = f"# Error reading: {e}"

                try:
                    rel = doc_path.relative_to(project_root)
                except ValueError:
                    rel = doc_path

                if compact:
                    content = collapse_blank_lines(content)
                    sections.append(
                        f"\n--- DOC: {rel} ---\n\n"
                        f"{content}\n"
                    )
                else:
                    sections.append(
                        f"\n{'=' * 70}\n"
                        f"📄 DOC: {rel}\n"
                        f"{'=' * 70}\n\n"
                        f"{content}\n"
                    )
                file_count += 1
                self.stdout.write(f"  📄 {rel}")

        # 3. Walk the source code
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]

            for file in sorted(files):
                if file in self.IGNORE_FILES:
                    continue

                _, ext = os.path.splitext(file)
                if ext.lower() not in self.INCLUDE_EXTS:
                    continue

                file_path = Path(root) / file

                try:
                    rel_path = file_path.relative_to(src_dir)
                except ValueError:
                    rel_path = file_path

                rel_str = str(rel_path)

                # Also compute path relative to project root for git diff
                try:
                    rel_from_root = file_path.relative_to(project_root)
                except ValueError:
                    rel_from_root = file_path

                # ── App filter ──
                if apps_filter:
                    parts = rel_path.parts
                    if not any(app in parts for app in apps_filter):
                        continue

                # ── Models-only filter ──
                if models_only and file != 'models.py':
                    continue

                # ── Specific files filter ──
                if files_filter and file not in files_filter:
                    continue

                # ── Admin filter ──
                if not options.get('include_admin', False):
                    if fnmatch.fnmatch(file, 'admin*.py'):
                        continue

                # ── Git diff filter ──
                if diff_files is not None:
                    if str(rel_from_root) not in diff_files:
                        continue

                # ── Grep filter ──
                if grep_pattern and ext.lower() == '.py':
                    if not file_matches_grep(file_path, grep_pattern):
                        continue

                # ── Read content ──
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                except Exception as e:
                    content = f"# Error reading: {e}"

                if not content.strip():
                    content = "# (Empty file)"

                # ── Skeleton or full? ──
                use_skeleton = False
                if skeleton_mode and ext.lower() == '.py':
                    parts = rel_path.parts
                    if full_apps and any(app in parts for app in full_apps):
                        use_skeleton = False
                    else:
                        use_skeleton = True

                if use_skeleton:
                    content = extract_skeleton(str(file_path))
                    mode_tag = " [SKELETON]"
                else:
                    mode_tag = ""
                    # Apply compact optimizations to full code
                    if compact and ext.lower() == '.py':
                        content = strip_comments(content)
                    if compact:
                        content = collapse_blank_lines(content)

                line_count = content.count('\n')
                token_est = estimate_tokens(content)

                if compact:
                    sections.append(
                        f"\n--- {rel_path}{mode_tag}  ({line_count} lines, ~{format_tokens(token_est)} tok) ---\n\n"
                        f"{content}\n"
                    )
                else:
                    sections.append(
                        f"\n{'=' * 70}\n"
                        f"FILE: {rel_path}{mode_tag}  ({line_count} lines, ~{format_tokens(token_est)} tokens)\n"
                        f"{'=' * 70}\n\n"
                        f"{content}\n"
                    )
                file_count += 1

        # ── Assemble and write ─────────────────────────────────────────
        full_output = self._assemble_output(
            sections, file_count, project_root, options, no_header,
        )
        self._write_output(
            full_output, file_count, output_dir, output_name, options,
            estimate_only, max_tokens,
        )

    def _assemble_output(
        self, sections: list, file_count: int, project_root: Path,
        options: dict, no_header: bool,
    ) -> str:
        """Assemble all sections into the final output string."""
        if no_header:
            return "\n".join(sections)

        # Build a descriptive header
        mode_parts = []
        if options.get('compact'):
            mode_parts.append("compact")
        if options.get('tree') or options.get('tree_only'):
            mode_parts.append("tree")
        if options.get('skeleton'):
            mode_parts.append("skeleton")
        if options.get('models_only'):
            mode_parts.append("models-only")
        if options.get('diff'):
            mode_parts.append(f"diff:{options['diff']}")
        if options.get('grep'):
            mode_parts.append(f"grep:{options['grep']}")
        if not mode_parts:
            mode_parts.append("full")

        header_lines = [
            f"CASTOR PROJECT CONTEXT DUMP",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Mode:      {' + '.join(mode_parts)}",
        ]
        if options.get('preset'):
            header_lines.append(f"Preset:    {options['preset']}")
        if options.get('apps'):
            header_lines.append(f"Apps:      {', '.join(options['apps'])}")
        if options.get('docs') is not None:
            header_lines.append(f"Docs:      {options.get('docs') or ['all']}")

        header_lines.append(f"Files:     {file_count}")

        if not options.get('compact'):
            header_lines.insert(2, f"Project:   {project_root}")
            header_lines.append(f"{'=' * 70}")

        header = "\n".join(header_lines)
        return header + "\n" + "\n".join(sections)

    def _write_output(
        self, full_output: str, file_count: int, output_dir: Path,
        output_name: str | None, options: dict, estimate_only: bool,
        max_tokens: int | None,
    ):
        """Handle token estimation, truncation, and file writing."""
        tokens = estimate_tokens(full_output)
        chars = len(full_output)
        lines_count = full_output.count('\n')

        self.stdout.write(f"\n📊 Stats:")
        self.stdout.write(f"   Files:  {file_count}")
        self.stdout.write(f"   Lines:  {lines_count:,}")
        self.stdout.write(f"   Chars:  {chars:,}")
        self.stdout.write(f"   Tokens: ~{tokens:,} (estimated)")

        if estimate_only:
            self.stdout.write(
                self.style.SUCCESS("\n✅ Estimation complete (no file written).")
            )
            return

        # Token budget check
        if max_tokens and tokens > max_tokens:
            self.stdout.write(self.style.WARNING(
                f"\n⚠️  Output exceeds budget ({tokens:,} > {max_tokens:,} tokens)."
            ))
            char_budget = max_tokens * 4
            full_output = full_output[:char_budget]
            full_output += (
                f"\n\n{'=' * 70}\n"
                f"⚠️  OUTPUT TRUNCATED at ~{max_tokens:,} tokens.\n"
                f"Original would have been ~{tokens:,} tokens.\n"
                f"Use --apps, --skeleton, or --grep to reduce size.\n"
                f"{'=' * 70}\n"
            )
            tokens = max_tokens

        # Determine filename
        if output_name:
            filename = output_name
        else:
            filename = generate_output_filename(options)

        output_path = output_dir / filename

        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(full_output)

            # Update _latest.txt symlink
            latest_path = output_dir / '_latest.txt'
            try:
                if latest_path.exists() or latest_path.is_symlink():
                    latest_path.unlink()
                latest_path.symlink_to(output_path.name)
            except OSError:
                # Symlinks may not work on all platforms — fall back to copy
                try:
                    import shutil
                    shutil.copy2(output_path, latest_path)
                except Exception:
                    pass  # Not critical

            self.stdout.write(self.style.SUCCESS(
                f"\n✅ Dumped {file_count} files (~{tokens:,} tokens)"
            ))
            self.stdout.write(self.style.SUCCESS(f"📄 {output_path}"))
            self.stdout.write(f"   Latest: {latest_path}")

        except Exception as e:
            raise CommandError(f"Error writing output: {e}")

    @staticmethod
    def _get_cli_default(key: str):
        """Return the default value for a CLI option (used for preset merging)."""
        defaults = {
            'apps': None,
            'skeleton': False,
            'full_apps': None,
            'models_only': False,
            'files': None,
            'types': None,
            'tree': False,
            'tree_only': False,
            'tree_depth': 10,
            'docs': None,
            'diff': None,
            'grep': None,
            'estimate': False,
            'compact': False,
            'init_presets': False,
            'max_tokens': None,
            'include_tests': False,
            'output': None,
            'no_header': False,
            'include_admin': False,

        }
        return defaults.get(key)