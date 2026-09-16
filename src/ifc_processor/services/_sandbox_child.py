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
exit 0 : ``result_path`` holds ``{"ok": true, "targets": [...], "diff": {...}}``
exit 1 : failed; ``result_path`` holds ``{"ok": false, "error_type", "error", …}``

What the child does: open the scratch copy, snapshot it, exec the block, run
``targets = select(model)``, run ``modify(model, targets)``, snapshot again,
diff, write the scratch copy. The diff is computed here, by the harness, not
reported by the code. A ``select`` that returns one entity instead of a list
is accepted as a list of one. With zero targets ``modify``, the second
snapshot and the write are skipped: the parent discards that copy anyway.

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
from ifc_processor.services.ifc_diff import IfcSnapshot, diff_snapshots  # noqa: E402


def _write_result(result_path: str, payload: dict) -> None:
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, default=str)


def _global_ids(targets) -> list[str]:
    """Validate ``select``'s return value and reduce it to GlobalIds."""
    if targets is None or isinstance(targets, (str, bytes)):
        raise code_sandbox.CodeSandboxError("select(model) must return a list of entities")
    ids: list[str] = []
    for entity in targets:
        gid = getattr(entity, "GlobalId", None)
        if not gid:
            raise code_sandbox.CodeSandboxError(
                f"select(model) returned a non-rooted value: {entity!r}"
            )
        ids.append(gid)
    return list(dict.fromkeys(ids))


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
        before = IfcSnapshot.from_model(model, path=ifc_path)

        # One dict for globals and locals: module-level imports in the block
        # must be visible from inside select() and modify().
        # The eight helpers, ifcopenshell, ifcopenshell.api,
        # ifcopenshell.util.element and the api modules the sheet names (with
        # the model implied) are in scope by name, so a block that drops its
        # import lines, or calls spatial.assign_container(...) as the sheet
        # writes it, still runs; the restricted __import__ gates anything else.
        compiled = compile(code, "<generated>", "exec")
        namespace: dict = code_sandbox.build_restricted_globals()
        namespace.update(code_sandbox.prebound_names())
        namespace.update(code_sandbox.bound_api_modules(model))
        exec(compiled, namespace)  # noqa: S102

        select_fn, modify_fn = namespace.get("select"), namespace.get("modify")
        if not callable(select_fn) or not callable(modify_fn):
            raise code_sandbox.CodeSandboxError(
                "Code must define callable 'select(model)' and 'modify(model, targets)'"
            )

        selected = select_fn(model)
        if hasattr(selected, "GlobalId"):
            selected = [selected]  # one entity, not a list: the prompt allows the singular
        elif hasattr(selected, "is_a") and hasattr(selected, "id"):
            # A lone non-rooted entity iterates as its attributes; say so instead.
            raise code_sandbox.CodeSandboxError(
                f"select(model) returned a single {selected.is_a()}, which has no GlobalId; "
                "return a list of rooted entities (elements, spaces, storeys, types)"
            )
        targets = list(selected or [])
        target_ids = _global_ids(targets)
        if target_ids:
            modify_fn(model, targets)
            diff = diff_snapshots(before, IfcSnapshot.from_model(model, path=ifc_path))
            # The child owns the file: it opened the copy, so it writes the copy.
            model.write(ifc_path)
        else:
            diff = diff_snapshots(before, before)

        _write_result(result_path, {"ok": True, "targets": target_ids, "diff": diff.as_dict()})
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
