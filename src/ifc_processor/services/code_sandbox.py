# ifc_processor/services/code_sandbox.py
"""
Sandbox primitives for executing generated IfcOpenShell code.

Pure library code — no Django, no LLM. It lives in ``ifc_processor`` so the
sandbox child (also pure library) can run generated code without importing
from ``writeback``.

This module owns the ONE definition of :data:`FORBIDDEN_PATTERNS`. The code
generator and the executor both import it, so the textual guard cannot drift
between "what we told the model not to write" and "what we refuse to run".

**On the strength of this sandbox:** it is a speed bump, not a jail. The code
runs in-process with a curated ``__builtins__`` and an import whitelist, but
``type`` is reachable and Python's object graph is not sealed, so a determined
prompt-injection could plausibly escape. ``getattr`` and ``hasattr`` are
allowed for that reason: attribute access is unrestricted anyway, and both
are ordinary idioms in the code the model writes. The load-bearing protections are
elsewhere: the code only ever touches a *copy* of the IFC file, what it did
to that copy is measured by the harness as a before/after diff, it runs in a
subprocess with a hard timeout, and a human approves the diff before the copy
replaces the original.
"""

from __future__ import annotations

import builtins
import importlib
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

from . import castor_select

logger = logging.getLogger(__name__)

#: Budget for the generated code itself on a small file.
DEFAULT_TIMEOUT_SECONDS = 30
#: Added per MiB of IFC: the child also opens the file, snapshots it twice and
#: writes it back, and each of those scales with the file (measured about
#: 0.5 s per MiB for open + two snapshots on the sample house).
SECONDS_PER_MIB = 2
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
    (r"\bsetattr\s*\(", "setattr()"),
    (r"(?<!\w)open\s*\(", "open()"),
    (r"\bmodel\.write\b", "model.write()"),
    (
        r"\bmodel\.create_entity\s*\(",
        "model.create_entity() (no GlobalId; use root.create_entity(ifc_class=..., name=...))",
    ),
)

#: The ifcopenshell.api modules the api sheet names, bound in the child by these
#: names with the model implied, so a call written as the sheet writes it runs.
API_MODULES = (
    "pset",
    "root",
    "spatial",
    "aggregate",
    "type",
    "attribute",
    "classification",
    "material",
    "group",
)

#: Both entry points the generated block must define.
REQUIRED_FUNCTIONS = ("select", "modify")


class CodeSandboxError(Exception):
    """Generated code failed a sandbox safety layer or raised at runtime."""


class CodeSandboxTimeoutError(CodeSandboxError):
    """Generated code exceeded its wall-clock budget."""


def validate_code(code: str) -> None:
    """Static checks before the code is ever compiled.

    Raises :class:`CodeSandboxError` on empty/oversized code, a missing
    ``select`` / ``modify`` entry point, or any forbidden pattern.
    """
    if not code or not isinstance(code, str):
        raise CodeSandboxError("Code is empty or not a string")

    if len(code) > MAX_CODE_LENGTH:
        raise CodeSandboxError(f"Code too long ({len(code)} chars, max {MAX_CODE_LENGTH})")

    for name in REQUIRED_FUNCTIONS:
        if f"def {name}(" not in code:
            raise CodeSandboxError(f"Code must define a '{name}' function")

    for pattern, label in FORBIDDEN_PATTERNS:
        if re.search(pattern, code):
            raise CodeSandboxError(f"Code contains forbidden pattern: {label}")


def _shadowing_hint(error_type: str, detail: str) -> str:
    """Name the api module a local variable shadowed (``pset = pset.add_pset(...)``)."""
    if error_type != "UnboundLocalError":
        return detail
    shadowed = next((m for m in API_MODULES if f"'{m}'" in detail), None)
    if shadowed is None:
        return detail
    return (
        f"{detail} (the variable '{shadowed}' shadows the api module '{shadowed}'; "
        "give the variable another name)"
    )


def budget_for(ifc_path: str | Path) -> int:
    """Wall-clock seconds for a run on ``ifc_path``: the base budget plus a per-MiB share."""
    size_mib = Path(ifc_path).stat().st_size / 1_048_576
    return DEFAULT_TIMEOUT_SECONDS + math.ceil(size_mib * SECONDS_PER_MIB)


def run_code_subprocess(
    ifc_path: str | Path,
    code: str,
    *,
    timeout: int | None = None,
) -> dict:
    """Run a generated ``select`` / ``modify`` block against ``ifc_path`` in a child process.

    The child opens the file, snapshots it, runs ``targets = select(model)``
    then ``modify(model, targets)``, snapshots again, diffs, and writes the
    file back — so nothing unpicklable (an ``ifcopenshell.file``) has to cross
    the process boundary; only paths and strings do.

    ``subprocess.run(timeout=…)`` is the real, cross-platform wall-clock
    bound. The previous in-process ``signal.SIGALRM`` guard silently did
    nothing on Windows, so runaway code could hang forever.

    Args:
        ifc_path: The file the child should open and modify (the scratch copy —
                  never the original).
        code:     Generated source defining ``select(model)`` and ``modify(model, targets)``.
        timeout:  Budget for open + snapshots + code + write; defaults to
                  :func:`budget_for` (size-aware). Interpreter startup and the
                  ifcopenshell import get ``_SPAWN_BUDGET_SECONDS`` on top.

    Returns:
        ``{"targets": [GlobalId, ...], "diff": IfcDiff.as_dict()}`` — the
        selection and what the run did to the file, measured by the harness.

    Raises:
        CodeSandboxTimeoutError: the child exceeded its budget (it is killed).
        CodeSandboxError:        validation, execution, or protocol failure.
    """
    ifc_path = Path(ifc_path)
    validate_code(code)  # fail before paying for a process spawn
    if timeout is None:
        timeout = budget_for(ifc_path)

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
        detail = _shadowing_hint(error_type, detail)
        raise CodeSandboxError(f"Generated code failed: {error_type}: {detail}")

    targets, diff = payload.get("targets"), payload.get("diff")
    if not isinstance(targets, list) or not isinstance(diff, dict):
        raise CodeSandboxError("Sandbox result is missing 'targets' or 'diff'")
    return {"targets": targets, "diff": diff}


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


class BoundApiModule:
    """One ``ifcopenshell.api`` module with the model implied.

    ``spatial.assign_container(products=[e], relating_structure=s)`` runs
    ``ifcopenshell.api.spatial.assign_container(model, products=[e], relating_structure=s)``;
    the model may also be passed explicitly as the first argument. The ``type``
    module doubles as the builtin: ``type(x)`` still answers the class, because
    binding the sheet's name must not break ordinary Python.
    """

    def __init__(self, model, name: str) -> None:
        self._model = model
        self._name = name

    def __getattr__(self, function: str):
        module = importlib.import_module(f"ifcopenshell.api.{self._name}")
        target = getattr(module, function)

        def call(*args, **kwargs):
            if args and args[0] is self._model:
                args = args[1:]
            return target(self._model, *args, **kwargs)

        return call

    def __call__(self, *args):
        if self._name != "type":
            raise TypeError(f"'{self._name}' is an api module, not a function")
        return builtins.type(*args)


def bound_api_modules(model) -> dict:
    """``{module name: BoundApiModule}`` for every module the api sheet names."""
    return {name: BoundApiModule(model, name) for name in API_MODULES}


def prebound_names() -> dict:
    """Names generated code may use without importing them.

    The eight ``castor_select`` helpers, ``ifcopenshell`` (with ``.api`` and
    ``.util.element`` loaded), ``api`` and ``element`` as short aliases. Small
    models copy the shape of the prompt's example and drop its import lines;
    binding these keeps that from being a NameError. The api modules need the
    open model and are bound by :func:`bound_api_modules` once it exists.
    """
    names = {name: getattr(castor_select, name) for name in castor_select.__all__}
    names.update(
        {
            "ifcopenshell": ifcopenshell,
            "api": ifcopenshell.api,
            "element": ifcopenshell.util.element,
        }
    )
    return names


def build_restricted_globals() -> dict:
    """Globals for ``exec()`` with a whitelist-enforcing ``__import__``.

    The whitelist gates the ``import`` statement, not attribute access:
    ``from ifcopenshell.util import unit`` resolves when that submodule was
    already loaded by ``ifcopenshell.api``, because Python fetches it from
    the package object the whitelist returned. Every module reachable that
    way is ifcopenshell's own; the guard exists to refuse ``os``, ``sys``,
    ``django`` and friends, and does.
    """
    allowed_modules = {
        "ifcopenshell": ifcopenshell,
        "ifcopenshell.api": ifcopenshell.api,
        "ifcopenshell.util": ifcopenshell.util,
        "ifcopenshell.util.element": ifcopenshell.util.element,
        "ifcopenshell.guid": _try_import("ifcopenshell.guid"),
        "ifcopenshell.util.placement": _try_import("ifcopenshell.util.placement"),
        "ifcopenshell.util.selector": _try_import("ifcopenshell.util.selector"),
        "castor_select": castor_select,
        "ifc_processor.services.castor_select": castor_select,
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
    "hasattr",
    "getattr",
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
