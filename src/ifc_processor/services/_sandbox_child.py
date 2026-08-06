# ifc_processor/services/_sandbox_child.py
"""
Child process entry point for the generated-code sandbox.

Launched by :func:`code_sandbox.run_code_subprocess` as a plain script — NOT
``python -m`` — so it works regardless of the parent's cwd or how the venv
was activated. It bootstraps ``sys.path`` from its own location and imports
only Django-free modules.

Protocol
--------
stdin  : one JSON object ``{ifc_path, code, result_path, timeout}``
stdout : diagnostics only (ifcopenshell warnings, the code's own ``print``)
exit 0 : the run succeeded AND ``result_path`` holds ``{"ok": true, "result": …}``
exit 1 : failed; ``result_path`` holds ``{"ok": false, "error_type", "error", …}``

The result travels in a *file*, never stdout — ``print`` is available to the
generated code and ifcopenshell chatters on stdout, so parsing stdout would
turn a successful run into a spurious crash.
"""

from __future__ import annotations

import json
import os
import sys
import traceback

# Bootstrap: src/ is three levels up (ifc_processor/services/_sandbox_child.py).
_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from ifc_processor.services import code_sandbox  # noqa: E402


def _write_result(result_path: str, payload: dict) -> None:
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, default=str)


def main() -> int:
    try:
        job = json.loads(sys.stdin.read())
        ifc_path = job["ifc_path"]
        code = job["code"]
        result_path = job["result_path"]
    except Exception:  # noqa: BLE001 — nowhere to report; the parent sees exit 2
        traceback.print_exc(file=sys.stderr)
        return 2

    try:
        # Re-validate in the child: defence in depth, and the child may be
        # launched directly in tests.
        code_sandbox.validate_code(code)

        import ifcopenshell

        model = ifcopenshell.open(ifc_path)

        compiled = compile(code, "<tier3_generated>", "exec")
        namespace: dict = {}
        exec(compiled, code_sandbox.build_restricted_globals(), namespace)  # noqa: S102

        modify_fn = namespace.get("modify_ifc")
        if modify_fn is None or not callable(modify_fn):
            raise code_sandbox.CodeSandboxError("Code did not define a callable 'modify_ifc'")

        result = modify_fn(model)
        code_sandbox.validate_result(result)

        # The child owns the file: it opened the copy, so it writes the copy.
        model.write(ifc_path)

        _write_result(result_path, {"ok": True, "result": result})
        return 0

    except Exception as exc:  # noqa: BLE001 — every failure is reported as data
        _write_result(
            result_path,
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
