# ifc_processor/tests/test_code_sandbox.py
"""Tests for the generated-code sandbox primitives and the subprocess runner.

The subprocess tests spawn a real Python interpreter against the fixture IFC —
that is the point. `test_infinite_loop_is_killed` is the regression test that
matters most here: an in-process timeout was a silent no-op on Windows.
"""

import shutil
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element as element_util
import pytest

from ifc_processor.services.code_sandbox import (
    MAX_CODE_LENGTH,
    CodeSandboxError,
    CodeSandboxTimeoutError,
    build_restricted_globals,
    run_code_subprocess,
    validate_code,
)

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "simple_wall.ifc"


@pytest.fixture
def ifc_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "sandbox.ifc"
    shutil.copy(FIXTURE_PATH, dest)
    return dest


_RENAME_CODE = """
def select(model):
    return [model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")]

def modify(model, targets):
    for wall in targets:
        wall.Name = "Renamed By Sandbox"
"""


# ── validate_code ─────────────────────────────────────────────────


def test_validate_code_accepts_minimal_valid_code():
    """Both entry points present and nothing forbidden passes."""
    validate_code("def select(model):\n    return []\n\ndef modify(model, targets):\n    pass\n")


@pytest.mark.parametrize("bad", ["", None, 123])
def test_validate_code_rejects_empty_or_non_string(bad):
    """Empty or non-string code is refused before anything runs."""
    with pytest.raises(CodeSandboxError):
        validate_code(bad)


def test_validate_code_rejects_oversized_code():
    """A block over the size cap is refused."""
    with pytest.raises(CodeSandboxError, match="too long"):
        validate_code("def select(model):\n" + "#x\n" * MAX_CODE_LENGTH)


@pytest.mark.parametrize("missing", ["select", "modify"])
def test_validate_code_requires_both_entry_points(missing):
    """A block defining only one of select / modify is refused."""
    other = "modify" if missing == "select" else "select"
    with pytest.raises(CodeSandboxError, match=missing):
        validate_code(f"def {other}(model):\n    return []\n")


@pytest.mark.parametrize("snippet", ["import os", "import subprocess", "exec(", "eval("])
def test_validate_code_rejects_forbidden_patterns(snippet):
    """The textual guard catches the classic escape hatches."""
    with pytest.raises(CodeSandboxError, match="forbidden pattern"):
        validate_code(f"def select(model):\n    {snippet}\n\ndef modify(model, targets):\n    pass")


# ── restricted globals ────────────────────────────────────────────


def test_restricted_globals_block_disallowed_imports():
    """`os` is not importable from generated code."""
    safe_import = build_restricted_globals()["__builtins__"]["__import__"]
    with pytest.raises(ImportError, match="not allowed"):
        safe_import("os")


def test_restricted_globals_block_django():
    """The child runs without Django, and generated code may not pull it in."""
    safe_import = build_restricted_globals()["__builtins__"]["__import__"]
    with pytest.raises(ImportError, match="not allowed"):
        safe_import("django")


def test_restricted_globals_allow_ifcopenshell_and_castor_select():
    """The helper library is whitelisted under its short name."""
    safe_import = build_restricted_globals()["__builtins__"]["__import__"]
    assert safe_import("ifcopenshell") is not None
    module = safe_import("castor_select", fromlist=("by_type",))
    assert callable(module.by_type)


# ── subprocess runner ─────────────────────────────────────────────


@pytest.mark.slow
def test_subprocess_returns_targets_and_diff_and_writes_the_file(ifc_copy: Path):
    """The harness reports the selection and the measured diff; the file is written."""
    result = run_code_subprocess(ifc_copy, _RENAME_CODE, timeout=30)

    assert result["targets"] == [WALL1_GUID]
    rows = result["diff"]["attribute_changes"]
    assert [(r["global_id"], r["prop"], r["after"]) for r in rows] == [
        (WALL1_GUID, "Name", "Renamed By Sandbox")
    ]
    model = ifcopenshell.open(str(ifc_copy))
    assert model.by_guid(WALL1_GUID).Name == "Renamed By Sandbox"


@pytest.mark.slow
def test_zero_targets_skips_modify(ifc_copy: Path):
    """With an empty selection modify never runs, so the diff is empty."""
    code = _RENAME_CODE.replace('return [model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")]', "return []")

    result = run_code_subprocess(ifc_copy, code, timeout=30)

    assert result["targets"] == []
    assert result["diff"]["attribute_changes"] == []
    assert result["diff"]["property_changes"] == []


@pytest.mark.slow
def test_mutating_select_shows_in_the_diff(ifc_copy: Path):
    """The snapshot is taken before select, so a select that writes is measured."""
    code = """
def select(model):
    wall = model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")
    wall.Name = "Mutated In Select"
    return [wall]

def modify(model, targets):
    pass
"""
    result = run_code_subprocess(ifc_copy, code, timeout=30)

    assert [r["after"] for r in result["diff"]["attribute_changes"]] == ["Mutated In Select"]


@pytest.mark.slow
def test_select_returning_non_entities_is_an_error(ifc_copy: Path):
    """A selection that is not a list of rooted entities is reported as a failure."""
    code = "def select(model):\n    return ['not an entity']\n\ndef modify(model, targets):\n    pass\n"

    with pytest.raises(CodeSandboxError, match="non-rooted"):
        run_code_subprocess(ifc_copy, code, timeout=30)


@pytest.mark.slow
def test_castor_select_helpers_import_inside_the_child(ifc_copy: Path):
    """`from castor_select import …` works through the runtime whitelist."""
    code = """
from castor_select import by_type

def select(model):
    return by_type(model, "IfcWall")

def modify(model, targets):
    for wall in targets:
        wall.Description = "touched"
"""
    result = run_code_subprocess(ifc_copy, code, timeout=30)

    assert WALL1_GUID in result["targets"]
    assert any(r["prop"] == "Description" for r in result["diff"]["attribute_changes"])


@pytest.mark.slow
def test_infinite_loop_is_killed(ifc_copy: Path):
    """Runaway generated code is killed by the wall-clock budget."""
    code = "def select(model):\n    while True:\n        pass\n\ndef modify(model, targets):\n    pass\n"

    with pytest.raises(CodeSandboxTimeoutError, match="budget"):
        run_code_subprocess(ifc_copy, code, timeout=2)


@pytest.mark.slow
def test_code_raising_is_reported_not_hung(ifc_copy: Path):
    """A traceback inside the child comes back as a CodeSandboxError message."""
    code = (
        "def select(model):\n    raise ValueError('entity not found')\n\n"
        "def modify(model, targets):\n    pass\n"
    )

    with pytest.raises(CodeSandboxError, match="entity not found"):
        run_code_subprocess(ifc_copy, code, timeout=30)


@pytest.mark.slow
def test_stdout_noise_does_not_corrupt_the_result(ifc_copy: Path):
    """`print` is allowed and ifcopenshell chatters; the result travels in a file."""
    noisy = _RENAME_CODE.replace(
        "def select(model):", "def select(model):\n    print('NOISE ' * 500)"
    )

    result = run_code_subprocess(ifc_copy, noisy, timeout=30)

    assert result["targets"] == [WALL1_GUID]


@pytest.mark.slow
def test_no_result_file_is_left_behind(ifc_copy: Path):
    """The result sidecar is unlinked after a run."""
    run_code_subprocess(ifc_copy, _RENAME_CODE, timeout=30)
    assert list(ifc_copy.parent.glob("*.sandbox-result.json")) == []


@pytest.mark.slow
def test_forbidden_code_fails_before_spawning(ifc_copy: Path):
    """Static validation runs before any process is spawned; the file is untouched."""
    before = ifc_copy.read_bytes()
    with pytest.raises(CodeSandboxError, match="forbidden pattern"):
        run_code_subprocess(
            ifc_copy, "def select(model):\n    import os\n\ndef modify(model, targets):\n    pass\n"
        )
    assert ifc_copy.read_bytes() == before


@pytest.mark.slow
def test_sandboxed_code_cannot_import_os(ifc_copy: Path):
    """The import whitelist holds inside the child, not just in validation."""
    code = (
        "def select(model):\n"
        "    name = 'o' + 's'\n"
        "    mod = __builtins__['__import__'](name)\n"
        "    return []\n\n"
        "def modify(model, targets):\n    pass\n"
    )
    with pytest.raises(CodeSandboxError):
        run_code_subprocess(ifc_copy, code, timeout=30)


@pytest.mark.slow
def test_sandboxed_code_cannot_import_django(ifc_copy: Path):
    """Django is not on the whitelist even though it is importable in the parent."""
    code = (
        "def select(model):\n    import django\n    return []\n\n"
        "def modify(model, targets):\n    pass\n"
    )
    with pytest.raises(CodeSandboxError, match="not allowed"):
        run_code_subprocess(ifc_copy, code, timeout=30)


@pytest.mark.slow
def test_pset_write_through_sandbox_persists_and_is_in_the_diff(ifc_copy: Path):
    """A realistic edit via the ifcopenshell API round-trips to disk and into the diff."""
    code = """
import ifcopenshell.api

def select(model):
    return [model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")]

def modify(model, targets):
    for wall in targets:
        pset = ifcopenshell.api.run("pset.add_pset", model, product=wall, name="Pset_Sandbox")
        ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"Checked": True})
"""
    result = run_code_subprocess(ifc_copy, code, timeout=30)

    model = ifcopenshell.open(str(ifc_copy))
    psets = element_util.get_psets(model.by_guid(WALL1_GUID))
    assert psets["Pset_Sandbox"]["Checked"] is True
    rows = result["diff"]["property_changes"]
    assert [(r["pset"], r["prop"], r["after"]) for r in rows] == [("Pset_Sandbox", "Checked", True)]


@pytest.mark.slow
def test_helpers_are_in_scope_without_an_import(ifc_copy: Path):
    """A block that calls by_type(...) with no import line runs: the helpers are pre-bound."""
    code = """
def select(model):
    return by_type(model, "IfcWall")

def modify(model, targets):
    for wall in targets:
        wall.Description = "no import needed"
"""
    result = run_code_subprocess(ifc_copy, code, timeout=30)
    assert WALL1_GUID in result["targets"]


@pytest.mark.slow
def test_ifcopenshell_api_and_element_are_in_scope_without_imports(ifc_copy: Path):
    """`element.get_pset` and `ifcopenshell.api.run` work with no import lines in the block."""
    code = """
def select(model):
    return by_type(model, "IfcWall")

def modify(model, targets):
    for wall in targets:
        pset = element.get_pset(wall, "Pset_NoImport")
        if not pset:
            pset = ifcopenshell.api.run("pset.add_pset", model, product=wall, name="Pset_NoImport")
        ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"Ok": True})
"""
    result = run_code_subprocess(ifc_copy, code, timeout=30)
    rows = result["diff"]["property_changes"]
    assert rows and {(r["pset"], r["prop"], r["after"]) for r in rows} == {
        ("Pset_NoImport", "Ok", True)
    }
    assert {r["global_id"] for r in rows} == set(result["targets"])


@pytest.mark.slow
def test_select_may_return_one_entity_instead_of_a_list(ifc_copy: Path):
    """The prompt says select() returns the storey for a creation; the singular is accepted."""
    code = _RENAME_CODE.replace(
        'return [model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")]',
        'return model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")',
    )

    result = run_code_subprocess(ifc_copy, code, timeout=30)

    assert result["targets"] == [WALL1_GUID]


@pytest.mark.slow
def test_zero_targets_leaves_the_file_bytes_untouched(ifc_copy: Path):
    """No targets: no modify, no second snapshot, no write."""
    before = ifc_copy.read_bytes()
    code = _RENAME_CODE.replace('return [model.by_guid("2O2Fr$t4X7Zf8NOew3FLOH")]', "return []")

    run_code_subprocess(ifc_copy, code, timeout=30)

    assert ifc_copy.read_bytes() == before


def test_budget_grows_with_the_file(ifc_copy: Path):
    """Open, two snapshots and the write scale with the file; the budget follows."""
    from ifc_processor.services.code_sandbox import DEFAULT_TIMEOUT_SECONDS, budget_for

    assert budget_for(ifc_copy) >= DEFAULT_TIMEOUT_SECONDS
    big = ifc_copy.with_name("big.ifc")
    big.write_bytes(b"0" * (10 * 1_048_576))
    assert budget_for(big) == DEFAULT_TIMEOUT_SECONDS + 20


def test_getattr_and_hasattr_are_ordinary_builtins_in_generated_code():
    """Both are common idioms; the ban bought nothing while attribute access stays unrestricted."""
    from ifc_processor.services.code_sandbox import build_restricted_globals, prebound_names

    code = (
        "def select(model):\n"
        "    return [e for e in model.by_type('IfcWall') if hasattr(e, 'Name') and getattr(e, 'Name', None)]\n"
        "def modify(model, targets):\n"
        "    pass\n"
    )
    validate_code(code)  # no forbidden pattern
    namespace = build_restricted_globals()
    namespace.update(prebound_names())
    exec(compile(code, "<t>", "exec"), namespace)  # noqa: S102
    assert namespace["__builtins__"]["getattr"] is getattr
    assert namespace["__builtins__"]["hasattr"] is hasattr


# ── the api sheet's notation, bound in the child ──────────────────


@pytest.mark.slow
def test_sheet_notation_runs_with_the_model_implied(ifc_copy: Path):
    """`root.create_entity(ifc_class=..., name=...)` as the sheet writes it: no import, no model."""
    code = """
def select(model):
    return model.by_type("IfcProject")

def modify(model, targets):
    zone = root.create_entity(ifc_class="IfcZone", name="Acoustic Zone 1")
    attribute.edit_attributes(product=zone, attributes={"Description": "sheet notation"})
"""
    result = run_code_subprocess(ifc_copy, code, timeout=30)

    assert list(result["diff"]["added_objects"].values()) == ["IfcZone"]
    model = ifcopenshell.open(str(ifc_copy))
    assert model.by_type("IfcZone")[0].Description == "sheet notation"


@pytest.mark.slow
def test_sheet_notation_accepts_the_model_as_first_argument(ifc_copy: Path):
    """The long habit `pset.add_pset(model, ...)` is not an error: the model is dropped once."""
    code = """
def select(model):
    return by_type(model, "IfcWall")

def modify(model, targets):
    for wall in targets:
        own = pset.add_pset(model, product=wall, name="Pset_Bound")
        pset.edit_pset(pset=own, properties={"Ok": True})
"""
    result = run_code_subprocess(ifc_copy, code, timeout=30)

    rows = result["diff"]["property_changes"]
    assert {(r["pset"], r["prop"], r["after"]) for r in rows} == {("Pset_Bound", "Ok", True)}


@pytest.mark.slow
def test_type_stays_the_builtin_inside_generated_code(ifc_copy: Path):
    """Binding the sheet's `type` module must not break `type(x)`."""
    code = """
def select(model):
    return by_type(model, "IfcWall")

def modify(model, targets):
    assert type(targets) is list
    for wall in targets:
        wall.Description = type(wall).__name__
"""
    result = run_code_subprocess(ifc_copy, code, timeout=30)

    rows = result["diff"]["attribute_changes"]
    assert rows and {(r["prop"], r["after"]) for r in rows} == {("Description", "entity_instance")}


def test_model_create_entity_is_refused_before_spawning(ifc_copy: Path):
    """A raw create makes an entity with no GlobalId the diff cannot see; the error names the fix."""
    code = """
def select(model):
    return model.by_type("IfcProject")

def modify(model, targets):
    model.create_entity("IfcZone", Name="Acoustic Zone 1")
"""
    with pytest.raises(CodeSandboxError, match=r"root\.create_entity"):
        run_code_subprocess(ifc_copy, code, timeout=30)


@pytest.mark.slow
def test_shadowing_an_api_module_names_the_module_in_the_error(ifc_copy: Path):
    """`pset = pset.add_pset(...)` is an UnboundLocalError; the repair text says why."""
    code = """
def select(model):
    return by_type(model, "IfcWall")

def modify(model, targets):
    for wall in targets:
        pset = pset.add_pset(product=wall, name="Pset_Shadow")
"""
    with pytest.raises(CodeSandboxError, match="shadows the api module 'pset'"):
        run_code_subprocess(ifc_copy, code, timeout=30)
