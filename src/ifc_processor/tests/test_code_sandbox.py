# ifc_processor/tests/test_code_sandbox.py
"""Tests for the generated-code sandbox primitives and the subprocess runner.

The subprocess tests spawn a real Python interpreter against the fixture IFC —
that is the point. The previous in-process `signal.SIGALRM` timeout was a
silent no-op on Windows, so `test_infinite_loop_is_killed` is the regression
test that matters most here.
"""

import shutil
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element as element_util
import pytest

from ifc_processor.services.code_sandbox import (
    FORBIDDEN_PATTERNS,
    MAX_CODE_LENGTH,
    CodeSandboxError,
    CodeSandboxTimeoutError,
    build_restricted_globals,
    result_to_changes,
    run_code_subprocess,
    validate_code,
    validate_result,
)

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "simple_wall.ifc"


@pytest.fixture
def ifc_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "sandbox.ifc"
    shutil.copy(FIXTURE_PATH, dest)
    return dest


_VALID_CODE = """
def modify_ifc(model):
    wall = model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")
    wall.Name = "Renamed By Sandbox"
    return {
        "summary": "renamed one wall",
        "changes": [{
            "global_id": "2O2Fr$t4X7Zf8NOew3FLOH",
            "entity_name": "Renamed By Sandbox",
            "ifc_type": "IfcWall",
            "description": "renamed",
            "old_value": "TestWall-001",
            "new_value": "Renamed By Sandbox",
        }],
    }
"""


# ── validate_code ─────────────────────────────────────────────────


def test_validate_code_accepts_minimal_valid_code():
    validate_code("def modify_ifc(model):\n    return {'summary': '', 'changes': []}")


@pytest.mark.parametrize("bad", ["", None, 123])
def test_validate_code_rejects_empty_or_non_string(bad):
    with pytest.raises(CodeSandboxError):
        validate_code(bad)


def test_validate_code_rejects_oversized_code():
    with pytest.raises(CodeSandboxError, match="too long"):
        validate_code("def modify_ifc(model):\n" + "#x\n" * MAX_CODE_LENGTH)


def test_validate_code_requires_entry_point():
    with pytest.raises(CodeSandboxError, match="modify_ifc"):
        validate_code("def something_else(model):\n    return {}")


@pytest.mark.parametrize("snippet", ["import os", "import subprocess", "exec(", "eval("])
def test_validate_code_rejects_forbidden_patterns(snippet):
    with pytest.raises(CodeSandboxError, match="forbidden pattern"):
        validate_code(f"def modify_ifc(model):\n    {snippet}\n    return {{}}")


def test_forbidden_patterns_is_a_single_shared_constant():
    """The planner and the executor must enforce the same list."""
    from writeback.services import tier3_planner

    assert tier3_planner.FORBIDDEN_PATTERNS is FORBIDDEN_PATTERNS


# ── validate_result ───────────────────────────────────────────────


def test_validate_result_accepts_valid_shape():
    validate_result({"summary": "ok", "changes": []})


@pytest.mark.parametrize(
    "bad",
    [
        "not a dict",
        {"changes": []},  # no summary
        {"summary": "s"},  # no changes
        {"summary": "s", "changes": "nope"},  # changes not a list
        {"summary": "s", "changes": ["not a dict"]},  # item not a dict
        {"summary": "s", "changes": [{"global_id": "x"}]},  # item missing keys
    ],
)
def test_validate_result_rejects_bad_shapes(bad):
    with pytest.raises(CodeSandboxError):
        validate_result(bad)


def test_result_to_changes_maps_to_entity_change():
    changes = result_to_changes(
        {
            "summary": "s",
            "changes": [
                {
                    "global_id": "G1",
                    "entity_name": "Wall",
                    "ifc_type": "IfcWall",
                    "description": "did a thing",
                    "old_value": "a",
                    "new_value": "b",
                }
            ],
        }
    )
    assert len(changes) == 1
    assert changes[0].pset == "(code)"
    assert changes[0].property == "did a thing"
    assert (changes[0].old_value, changes[0].new_value) == ("a", "b")


# ── restricted globals ────────────────────────────────────────────


def test_restricted_globals_block_disallowed_imports():
    safe_import = build_restricted_globals()["__builtins__"]["__import__"]
    with pytest.raises(ImportError, match="not allowed"):
        safe_import("os")


def test_restricted_globals_allow_ifcopenshell():
    safe_import = build_restricted_globals()["__builtins__"]["__import__"]
    assert safe_import("ifcopenshell") is not None


# ── subprocess runner ─────────────────────────────────────────────


@pytest.mark.slow
def test_subprocess_runs_code_and_writes_the_file(ifc_copy: Path):
    result = run_code_subprocess(ifc_copy, _VALID_CODE, timeout=30)

    assert result["summary"] == "renamed one wall"
    # The child wrote the file, so re-opening shows the change.
    model = ifcopenshell.open(str(ifc_copy))
    assert model.by_guid(WALL1_GUID).Name == "Renamed By Sandbox"


@pytest.mark.slow
def test_infinite_loop_is_killed(ifc_copy: Path):
    """THE regression test: the old SIGALRM timeout was a no-op on Windows,
    so runaway generated code could hang the worker forever."""
    code = "def modify_ifc(model):\n    while True:\n        pass\n"

    with pytest.raises(CodeSandboxTimeoutError, match="budget"):
        run_code_subprocess(ifc_copy, code, timeout=2)


@pytest.mark.slow
def test_code_raising_is_reported_not_hung(ifc_copy: Path):
    code = "def modify_ifc(model):\n    raise ValueError('entity not found')\n"

    with pytest.raises(CodeSandboxError, match="entity not found"):
        run_code_subprocess(ifc_copy, code, timeout=30)


@pytest.mark.slow
def test_stdout_noise_does_not_corrupt_the_result(ifc_copy: Path):
    """`print` is in the safe builtins and ifcopenshell chatters on stdout —
    the result travels in a file precisely so this can't break parsing."""
    noisy = _VALID_CODE.replace(
        "def modify_ifc(model):",
        "def modify_ifc(model):\n    print('NOISE ' * 500)",
    )

    result = run_code_subprocess(ifc_copy, noisy, timeout=30)
    assert result["summary"] == "renamed one wall"


@pytest.mark.slow
def test_bad_result_shape_is_rejected(ifc_copy: Path):
    code = "def modify_ifc(model):\n    return {'summary': 'x'}\n"

    with pytest.raises(CodeSandboxError):
        run_code_subprocess(ifc_copy, code, timeout=30)


@pytest.mark.slow
def test_no_result_file_is_left_behind(ifc_copy: Path):
    run_code_subprocess(ifc_copy, _VALID_CODE, timeout=30)
    assert list(ifc_copy.parent.glob("*.sandbox-result.json")) == []


@pytest.mark.slow
def test_forbidden_code_fails_before_spawning(ifc_copy: Path):
    before = ifc_copy.read_bytes()
    with pytest.raises(CodeSandboxError, match="forbidden pattern"):
        run_code_subprocess(ifc_copy, "def modify_ifc(model):\n    import os\n", timeout=30)
    assert ifc_copy.read_bytes() == before


@pytest.mark.slow
def test_sandboxed_code_cannot_import_os(ifc_copy: Path):
    """The import whitelist holds inside the child, not just in validation."""
    # Build the import dynamically so the textual guard doesn't catch it —
    # this exercises the runtime whitelist.
    code = (
        "def modify_ifc(model):\n"
        "    name = 'o' + 's'\n"
        "    mod = __builtins__['__import__'](name)\n"
        "    return {'summary': 'should not get here', 'changes': []}\n"
    )
    with pytest.raises(CodeSandboxError):
        run_code_subprocess(ifc_copy, code, timeout=30)


@pytest.mark.slow
def test_pset_write_through_sandbox_persists(ifc_copy: Path):
    """A realistic edit via the ifcopenshell API round-trips to disk."""
    code = """
def modify_ifc(model):
    import ifcopenshell.api
    wall = model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")
    pset = ifcopenshell.api.run("pset.add_pset", model, product=wall, name="Pset_Sandbox")
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"Checked": True})
    return {"summary": "added pset", "changes": [{
        "global_id": "2O2Fr$t4X7Zf8NOew3FLOH", "entity_name": "TestWall-001",
        "ifc_type": "IfcWall", "description": "added Pset_Sandbox"}]}
"""
    run_code_subprocess(ifc_copy, code, timeout=30)

    model = ifcopenshell.open(str(ifc_copy))
    psets = element_util.get_psets(model.by_guid(WALL1_GUID))
    assert psets["Pset_Sandbox"]["Checked"] is True
