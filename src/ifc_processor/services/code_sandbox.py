# ifc_processor/services/code_sandbox.py
"""
Sandbox primitives for executing generated IfcOpenShell code.

Pure library code — no Django, no LLM. It lives in ``ifc_processor`` so the
journal executor (also pure library) can run generated code without importing
from ``writeback``.

This module owns the ONE definition of :data:`FORBIDDEN_PATTERNS`. The code
generator and the executor both import it, so the textual guard cannot drift
between "what we told the model not to write" and "what we refuse to run".

**On the strength of this sandbox:** it is a speed bump, not a jail. The code
runs in-process with a curated ``__builtins__`` and an import whitelist, but
``type`` is reachable and Python's object graph is not sealed, so a determined
prompt-injection could plausibly escape. The load-bearing protections are
elsewhere: the code only ever touches a *copy* of the IFC file, its return
value is schema-validated, it runs in a subprocess with a hard timeout, and a
human must acknowledge the code before it executes at all.
"""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element

from .ifc_writer import EntityChange

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30
MAX_CODE_LENGTH = 15_000

#: Extra wall-clock allowance for interpreter startup + the ifcopenshell
#: import, which cost ~0.5–1.5s on Windows. Added on top of the code budget.
_SPAWN_BUDGET_SECONDS = 20

#: Launched by absolute path (never ``-m``) so cwd and venv layout don't matter.
_CHILD_SCRIPT = Path(__file__).with_name("_sandbox_child.py")

#: Textual guard, shared by the generator prompt-check and the executor.
#: (regex, human-readable label)
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bimport\s+os\b", "import os"),
    (r"\bimport\s+sys\b", "import sys"),
    (r"\bimport\s+subprocess\b", "import subprocess"),
    (r"\bimport\s+shutil\b", "import shutil"),
    (r"\bimport\s+pathlib\b", "import pathlib"),
    (r"\bimport\s+socket\b", "import socket"),
    (r"\bimport\s+urllib\b", "import urllib"),
    (r"\bimport\s+http\b", "import http"),
    (r"\bimport\s+requests\b", "import requests"),
    (r"\b__import__\s*\(", "__import__()"),
    (r"\bexec\s*\(", "exec()"),
    (r"\beval\s*\(", "eval()"),
    (r"\bcompile\s*\(", "compile()"),
    (r"\bglobals\s*\(", "globals()"),
    (r"\bgetattr\s*\(", "getattr()"),
    (r"\bsetattr\s*\(", "setattr()"),
    (r"(?<!\w)open\s*\(", "open()"),
    (r"\bmodel\.write\b", "model.write()"),
)

#: Keys every entry of a result's ``changes`` list must carry.
REQUIRED_CHANGE_KEYS = ("global_id", "entity_name", "ifc_type", "description")


class CodeSandboxError(Exception):
    """Generated code failed a sandbox safety layer or raised at runtime."""


class CodeSandboxTimeoutError(CodeSandboxError):
    """Generated code exceeded its wall-clock budget."""


def validate_code(code: str) -> None:
    """Static checks before the code is ever compiled.

    Raises :class:`CodeSandboxError` on empty/oversized code, a missing
    ``modify_ifc`` entry point, or any forbidden pattern.
    """
    if not code or not isinstance(code, str):
        raise CodeSandboxError("Code is empty or not a string")

    if len(code) > MAX_CODE_LENGTH:
        raise CodeSandboxError(f"Code too long ({len(code)} chars, max {MAX_CODE_LENGTH})")

    if "def modify_ifc" not in code:
        raise CodeSandboxError("Code must define a 'modify_ifc' function")

    for pattern, label in FORBIDDEN_PATTERNS:
        if re.search(pattern, code):
            raise CodeSandboxError(f"Code contains forbidden pattern: {label}")


def validate_result(result: object) -> None:
    """Check the dict ``modify_ifc`` returned matches the required schema."""
    if not isinstance(result, dict):
        raise CodeSandboxError(f"modify_ifc() must return a dict, got {type(result).__name__}")

    if "summary" not in result:
        raise CodeSandboxError("Result missing required key: 'summary'")

    changes = result.get("changes")
    if not isinstance(changes, list):
        raise CodeSandboxError("Result 'changes' must be a list")

    for index, item in enumerate(changes):
        if not isinstance(item, dict):
            raise CodeSandboxError(f"Change #{index} must be a dict")
        for key in REQUIRED_CHANGE_KEYS:
            if key not in item:
                raise CodeSandboxError(f"Change #{index} missing required key: {key!r}")


def result_to_changes(result: dict) -> list[EntityChange]:
    """Map a validated result dict onto legacy ``EntityChange`` rows."""
    return [
        EntityChange(
            global_id=str(item.get("global_id", "UNKNOWN")),
            entity_name=str(item.get("entity_name", "")),
            ifc_type=str(item.get("ifc_type", "UNKNOWN")),
            pset="(code)",
            property=str(item.get("description", "")),
            old_value=str(item.get("old_value", "")),
            new_value=str(item.get("new_value", "")),
        )
        for item in result.get("changes", [])
    ]


def run_code_subprocess(
    ifc_path: str | Path,
    code: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Run generated code against ``ifc_path`` in a separate process.

    The child opens the file, runs ``modify_ifc(model)``, and writes the file
    back — so nothing unpicklable (an ``ifcopenshell.file``) has to cross the
    process boundary; only paths and strings do.

    ``subprocess.run(timeout=…)`` is the real, cross-platform wall-clock
    bound. The previous in-process ``signal.SIGALRM`` guard silently did
    nothing on Windows, so runaway code could hang forever.

    Args:
        ifc_path: The file the child should open and modify (a temp copy —
                  never the original).
        code:     Generated source defining ``modify_ifc(model)``.
        timeout:  Budget for the code itself; interpreter startup and the
                  ifcopenshell import get ``_SPAWN_BUDGET_SECONDS`` on top.

    Returns:
        The validated result dict the generated code returned.

    Raises:
        CodeSandboxTimeoutError: the child exceeded its budget (it is killed).
        CodeSandboxError:        validation, execution, or protocol failure.
    """
    ifc_path = Path(ifc_path)
    validate_code(code)  # fail before paying for a process spawn

    result_path = ifc_path.with_suffix(ifc_path.suffix + ".sandbox-result.json")
    job = json.dumps(
        {
            "ifc_path": str(ifc_path),
            "code": code,
            "result_path": str(result_path),
            "timeout": timeout,
        }
    )

    try:
        completed = subprocess.run(  # noqa: S603 — fixed interpreter + fixed script
            [sys.executable, "-u", str(_CHILD_SCRIPT)],
            input=job,
            capture_output=True,
            text=True,
            timeout=timeout + _SPAWN_BUDGET_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        result_path.unlink(missing_ok=True)
        raise CodeSandboxTimeoutError(
            f"Generated code exceeded the {timeout}s budget and was terminated."
        ) from e

    try:
        payload = _read_result_file(result_path, completed)
    finally:
        result_path.unlink(missing_ok=True)

    if not payload.get("ok"):
        detail = payload.get("error") or "unknown error"
        error_type = payload.get("error_type") or "Error"
        if payload.get("traceback"):
            logger.error("Sandboxed code failed:\n%s", payload["traceback"])
        raise CodeSandboxError(f"Generated code failed: {error_type}: {detail}")

    result = payload.get("result")
    validate_result(result)
    return result


def _read_result_file(result_path: Path, completed) -> dict:
    """Read the child's result file, or explain why there isn't one."""
    if not result_path.exists():
        stderr_tail = (completed.stderr or "").strip()[-2000:]
        raise CodeSandboxError(
            f"Sandbox produced no result (exit {completed.returncode}). "
            f"{stderr_tail or 'No error output.'}"
        )
    try:
        return json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise CodeSandboxError(f"Sandbox result file was unreadable: {e}") from e


def build_restricted_globals() -> dict:
    """Globals for ``exec()`` — only whitelisted modules are importable."""
    allowed_modules = {
        "ifcopenshell": ifcopenshell,
        "ifcopenshell.api": ifcopenshell.api,
        "ifcopenshell.util": ifcopenshell.util,
        "ifcopenshell.util.element": ifcopenshell.util.element,
        "ifcopenshell.guid": _try_import("ifcopenshell.guid"),
        "ifcopenshell.util.placement": _try_import("ifcopenshell.util.placement"),
        "math": math,
        "re": re,
        "json": json,
    }
    return {"__builtins__": _safe_builtins(allowed_modules)}


# ── Internals ──────────────────────────────────────────────────────

_SAFE_BUILTIN_NAMES = (
    # Types
    "True",
    "False",
    "None",
    "int",
    "float",
    "str",
    "bool",
    "bytes",
    "list",
    "dict",
    "tuple",
    "set",
    "frozenset",
    "type",
    # Iteration & ranges
    "range",
    "enumerate",
    "zip",
    "map",
    "filter",
    "sorted",
    "reversed",
    "iter",
    "next",
    # Length & membership
    "len",
    "min",
    "max",
    "sum",
    "abs",
    "round",
    "any",
    "all",
    # String & repr
    "repr",
    "format",
    "print",
    "isinstance",
    "issubclass",
    "id",
    "hash",
    # Exceptions
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "AttributeError",
    "RuntimeError",
    "StopIteration",
)


def _try_import(module_name: str):
    """Import a module by dotted name, or return None if unavailable."""
    try:
        parts = module_name.split(".")
        mod = __import__(module_name)
        for part in parts[1:]:
            mod = getattr(mod, part)
        return mod
    except (ImportError, AttributeError):
        return None


def _safe_builtins(allowed_modules: dict) -> dict:
    """Curated builtins plus a whitelist-enforcing ``__import__``."""
    import builtins

    safe = {}
    for name in _SAFE_BUILTIN_NAMES:
        obj = getattr(builtins, name, None)
        if obj is not None:
            safe[name] = obj

    available = lambda: ", ".join(k for k, v in allowed_modules.items() if v is not None)  # noqa: E731

    # allowed_modules is captured in the closure — no reliance on the
    # caller's globals at import time.
    def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
        is_allowed = any(
            mod is not None
            and (
                name == allowed_name
                or name.startswith(allowed_name + ".")
                or allowed_name.startswith(name + ".")
            )
            for allowed_name, mod in allowed_modules.items()
        )
        if not is_allowed:
            raise ImportError(
                f"Import '{name}' is not allowed in generated code. Allowed: {available()}"
            )

        # "from X.Y import Z" — return the deepest named module.
        if fromlist and allowed_modules.get(name) is not None:
            return allowed_modules[name]

        # "import X.Y.Z" — Python expects the top-level package back and
        # resolves sub-attributes via dot access.
        top_level = name.split(".")[0]
        if allowed_modules.get(top_level) is not None:
            return allowed_modules[top_level]

        if allowed_modules.get(name) is not None:
            return allowed_modules[name]

        raise ImportError(
            f"Import '{name}' resolved but module not available. Allowed: {available()}"
        )

    safe["__import__"] = _restricted_import
    return safe
