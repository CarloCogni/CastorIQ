import json
import logging
import math
import re
import shutil
import signal
import tempfile
import traceback
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element

from ifc_processor.services.ifc_writer import EntityChange

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30
MAX_CODE_LENGTH = 15_000


class Tier3ExecutionError(Exception):
    """Raised when Tier 3 code execution fails at any safety layer."""

    pass


class Tier3TimeoutError(Tier3ExecutionError):
    """Raised when generated code exceeds the execution timeout."""

    pass


class Tier3Executor:
    """
    Executes LLM-generated IfcOpenShell code against a copy of the IFC file.

    Safety layers:
        1. Code runs on a COPY — original is untouched until success.
        2. Forbidden pattern check (redundant with planner, defence in depth).
        3. Restricted globals — only whitelisted modules available.
        4. Timeout — kills execution after N seconds.
        5. Return value validation — must match expected schema.
    """

    def __init__(self, ifc_path: str | Path, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self.ifc_path = Path(ifc_path)
        if not self.ifc_path.exists():
            raise Tier3ExecutionError(f"IFC file not found: {self.ifc_path}")
        self.timeout = timeout
        # Populated by execute() after a successful run. Holds the raw dict
        # returned by modify_ifc() — {"summary": str, "changes": list[dict]}.
        # Used by the D5 evaluation harness to score post-state matches
        # against the actual generated summary (not the EntityChange list,
        # which discards the summary string).
        self.last_result: dict | None = None

    def execute(self, code: str) -> list[EntityChange]:
        """
        Execute generated code on a file copy.

        Returns list of EntityChange on success.
        Raises Tier3ExecutionError on any failure — original file is untouched.
        """
        # 1. Validate code
        self._validate_code(code)

        # 2. Create temp copy
        tmp_path = self._create_temp_copy()

        try:
            # 3. Open the copy
            model = ifcopenshell.open(str(tmp_path))

            # 4. Execute code in restricted environment
            result = self._run_sandboxed(code, model)

            # 5. Validate return value
            self._validate_result(result)

            # 6. Save the modified copy
            model.write(str(tmp_path))

            # 7. Overwrite original with modified copy
            shutil.copy2(tmp_path, self.ifc_path)

            # 8. Convert to EntityChange list
            changes = self._result_to_changes(result)
            self.last_result = result

            logger.info(
                f"Tier3 execution success: {len(changes)} changes, "
                f"summary: {result.get('summary', '?')}"
            )

            return changes

        except Tier3ExecutionError:
            raise
        except Exception as e:
            logger.exception(f"Tier3 execution failed: {e}")
            raise Tier3ExecutionError(f"Code execution failed: {type(e).__name__}: {e}") from e
        finally:
            # Clean up temp file
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    def _create_temp_copy(self) -> Path:
        """Create a temporary copy of the IFC file."""
        suffix = self.ifc_path.suffix  # .ifc
        fd, tmp_str = tempfile.mkstemp(suffix=suffix, prefix="castor_t3_")

        # Close the file descriptor — we only need the path
        import os

        os.close(fd)

        tmp_path = Path(tmp_str)
        shutil.copy2(self.ifc_path, tmp_path)

        logger.debug(f"Tier3: created temp copy at {tmp_path}")
        return tmp_path

    def _validate_code(self, code: str) -> None:
        """Defence-in-depth: re-check forbidden patterns before execution."""
        if not code or not isinstance(code, str):
            raise Tier3ExecutionError("Code is empty or not a string")

        if len(code) > MAX_CODE_LENGTH:
            raise Tier3ExecutionError(f"Code too long ({len(code)} chars, max {MAX_CODE_LENGTH})")

        if "def modify_ifc" not in code:
            raise Tier3ExecutionError("Code must define a 'modify_ifc' function")

        # Same forbidden patterns as planner — defence in depth
        forbidden = [
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
        ]

        for pattern, label in forbidden:
            if re.search(pattern, code):
                raise Tier3ExecutionError(f"Code contains forbidden pattern: {label}")

    def _build_restricted_globals(self) -> dict:
        """
        Build the globals dict for exec(). Only whitelisted modules
        are available inside the generated code.
        """
        allowed_modules = {
            "ifcopenshell": ifcopenshell,
            "ifcopenshell.api": ifcopenshell.api,
            "ifcopenshell.util": ifcopenshell.util,
            "ifcopenshell.util.element": ifcopenshell.util.element,
            "ifcopenshell.guid": self._try_import("ifcopenshell.guid"),
            "ifcopenshell.util.placement": self._try_import("ifcopenshell.util.placement"),
            "math": math,
            "re": re,
            "json": json,
        }

        restricted = {"__builtins__": self._safe_builtins(allowed_modules)}

        return restricted

    @staticmethod
    def _try_import(module_name: str):
        """Attempt to import a module, return None if not available."""
        try:
            parts = module_name.split(".")
            mod = __import__(module_name)
            for part in parts[1:]:
                mod = getattr(mod, part)
            return mod
        except (ImportError, AttributeError):
            return None

    @staticmethod
    def _safe_builtins(allowed_modules: dict) -> dict:
        """
        Curated set of safe builtins. No file I/O, no code generation,
        no attribute manipulation.
        """
        import builtins

        allowed = [
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
            "str",
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
        ]

        safe = {}
        for name in allowed:
            obj = getattr(builtins, name, None)
            if obj is not None:
                safe[name] = obj

        # Capture allowed_modules in closure — no dependency on globals at call time
        def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
            # Check the module (or any parent/child) is allowed
            is_allowed = False
            for allowed_name, mod in allowed_modules.items():
                if mod is None:
                    continue
                if (
                    name == allowed_name
                    or name.startswith(allowed_name + ".")
                    or allowed_name.startswith(name + ".")
                ):
                    is_allowed = True
                    break

            if not is_allowed:
                raise ImportError(
                    f"Import '{name}' is not allowed in Tier 3 code. "
                    f"Allowed: {', '.join(k for k, v in allowed_modules.items() if v is not None)}"
                )

            # "from X.Y import Z" style (fromlist is non-empty):
            # return the deepest named module
            if fromlist:
                if name in allowed_modules and allowed_modules[name] is not None:
                    return allowed_modules[name]

            # "import X.Y.Z" style (fromlist is empty):
            # Python expects the TOP-LEVEL package back, then resolves
            # sub-attributes via dot access (e.g. ifcopenshell.api)
            top_level = name.split(".")[0]
            if top_level in allowed_modules and allowed_modules[top_level] is not None:
                return allowed_modules[top_level]

            # Fallback: return the exact module if available
            if name in allowed_modules and allowed_modules[name] is not None:
                return allowed_modules[name]

            raise ImportError(
                f"Import '{name}' resolved but module not available. "
                f"Allowed: {', '.join(k for k, v in allowed_modules.items() if v is not None)}"
            )

        safe["__import__"] = _restricted_import

        return safe

    # Custom __import__ that only allows whitelisted modules
    def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: N805
        globals = globals or {}
        allowed_modules = globals.get("_allowed_modules", {})

        # Check the module (or any parent/child) is allowed
        is_allowed = False
        for allowed_name in allowed_modules:
            if allowed_modules[allowed_name] is None:
                continue
            if (
                name == allowed_name
                or name.startswith(allowed_name + ".")
                or allowed_name.startswith(name + ".")
            ):
                is_allowed = True
                break

        if not is_allowed:
            raise ImportError(
                f"Import '{name}' is not allowed in Tier 3 code. "
                f"Allowed: {', '.join(k for k, v in allowed_modules.items() if v is not None)}"
            )

        # "from X.Y import Z" style (fromlist is non-empty):
        # return the deepest named module
        if fromlist:
            if name in allowed_modules and allowed_modules[name] is not None:
                return allowed_modules[name]

        # "import X.Y.Z" style (fromlist is empty):
        # Python expects the TOP-LEVEL package back, then resolves
        # sub-attributes via dot access (e.g. ifcopenshell.api)
        top_level = name.split(".")[0]
        if top_level in allowed_modules and allowed_modules[top_level] is not None:
            return allowed_modules[top_level]

        # Fallback: return the exact module if available
        if name in allowed_modules and allowed_modules[name] is not None:
            return allowed_modules[name]

        raise ImportError(
            f"Import '{name}' resolved but module not available. "
            f"Allowed: {', '.join(k for k, v in allowed_modules.items() if v is not None)}"
        )

    def _run_sandboxed(self, code: str, model) -> dict:
        """
        Execute the code string with restricted globals and a timeout.
        Returns the result dict from modify_ifc().
        """
        restricted_globals = self._build_restricted_globals()
        local_namespace = {}

        # Compile the code
        try:
            compiled = compile(code, "<tier3_generated>", "exec")
        except SyntaxError as e:
            raise Tier3ExecutionError(f"Generated code has syntax error: {e}")

        # Set timeout (Unix only — on Windows this is a no-op)
        old_handler = None
        try:

            def _timeout_handler(signum, frame):
                raise Tier3TimeoutError(f"Code execution exceeded {self.timeout}s timeout")

            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(self.timeout)
        except (AttributeError, OSError):
            # signal.SIGALRM not available (Windows) — skip timeout
            logger.warning("SIGALRM not available — running without timeout")

        try:
            # Execute: defines the modify_ifc function
            exec(compiled, restricted_globals, local_namespace)

            # Extract the function
            modify_fn = local_namespace.get("modify_ifc")
            if modify_fn is None:
                raise Tier3ExecutionError("Code did not define a 'modify_ifc' function")

            if not callable(modify_fn):
                raise Tier3ExecutionError("'modify_ifc' is not callable")

            # Call it with the model
            result = modify_fn(model)

            return result

        except Tier3ExecutionError:
            raise
        except Tier3TimeoutError:
            raise
        except ValueError as e:
            # ValueError is expected for "entity not found" etc.
            raise Tier3ExecutionError(f"Code raised ValueError: {e}")
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Tier3 code execution error:\n{tb}")
            raise Tier3ExecutionError(f"Code execution error: {type(e).__name__}: {e}")
        finally:
            # Cancel timeout
            try:
                signal.alarm(0)
                if old_handler is not None:
                    signal.signal(signal.SIGALRM, old_handler)
            except (AttributeError, OSError):
                pass

    @staticmethod
    def _validate_result(result) -> None:
        """Ensure the function returned the expected schema."""
        if not isinstance(result, dict):
            raise Tier3ExecutionError(
                f"modify_ifc() must return a dict, got {type(result).__name__}"
            )

        if "summary" not in result:
            raise Tier3ExecutionError("Return dict must include 'summary' key")

        if "changes" not in result:
            raise Tier3ExecutionError("Return dict must include 'changes' key")

        changes = result["changes"]
        if not isinstance(changes, list):
            raise Tier3ExecutionError(f"'changes' must be a list, got {type(changes).__name__}")

        required_keys = {"global_id", "entity_name", "ifc_type", "description"}
        for i, change in enumerate(changes):
            if not isinstance(change, dict):
                raise Tier3ExecutionError(f"changes[{i}] must be a dict")
            missing = required_keys - set(change.keys())
            if missing:
                raise Tier3ExecutionError(f"changes[{i}] missing required keys: {missing}")

    @staticmethod
    def _result_to_changes(result: dict) -> list[EntityChange]:
        """Convert the code's return dicts to EntityChange dataclass instances."""
        changes = []
        for item in result.get("changes", []):
            changes.append(
                EntityChange(
                    global_id=str(item.get("global_id", "UNKNOWN")),
                    entity_name=str(item.get("entity_name", "")),
                    ifc_type=str(item.get("ifc_type", "")),
                    pset="(code)",
                    property=str(item.get("description", "")),
                    old_value=str(item.get("old_value", "")),
                    new_value=str(item.get("new_value", "")),
                )
            )
        return changes
