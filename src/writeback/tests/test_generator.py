# writeback/tests/test_generator.py
"""Code extraction and the generate / repair prompts, with a canned model."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ifc_processor.services import castor_select
from writeback.services.generator import (
    HELPER_SIGNATURES,
    NUM_CTX,
    NUM_PREDICT,
    CodeGenerator,
    extract_code_block,
)
from writeback.services.grounding import Grounding

VALID = "def select(model):\n    return []\n\ndef modify(model, targets):\n    pass\n"


def test_extract_code_block_reads_a_python_fence():
    """The block inside ```python … ``` is returned without the fence."""
    assert extract_code_block(f"Sure:\n```python\n{VALID}```\nDone.") == VALID.strip()


def test_extract_code_block_accepts_an_unfenced_answer():
    """A model that forgets the fence but writes valid code still passes."""
    assert extract_code_block(VALID) == VALID.strip()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "I cannot do that.",
        "```python\ndef select(model):\n    return []\n```",
        "```python\ndef modify(model, targets):\n    pass\n```",
        "```python\ndef select(model)\n    return []\n```",
    ],
)
def test_extract_code_block_returns_none_without_both_functions(text):
    """Missing select, missing modify, or a syntax error is a repair, not a crash."""
    assert extract_code_block(text) is None


def test_extract_code_block_skips_a_first_fence_without_the_functions():
    """An explanatory snippet before the real block does not win."""
    text = f"```python\nprint('hi')\n```\n```python\n{VALID}```"
    assert extract_code_block(text) == VALID.strip()


def test_helper_signatures_are_rendered_from_the_library():
    """Every helper name and its parameters appear in the prompt text."""
    for name in castor_select.__all__:
        assert name + "(" in HELPER_SIGNATURES
    assert "elements_in_storey(model, name: str)" in HELPER_SIGNATURES


def _grounding():
    return Grounding(
        text='## Storeys\n- "Ground Floor" · 0',
        matched_types=("IfcWall",),
        storey_count=1,
        space_count=0,
    )


def test_generate_passes_num_ctx_and_purpose_modify_once():
    """The single get_llm call carries purpose='modify' and the NUM_CTX constant."""
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="```python\n" + VALID + "```")
    with patch("writeback.services.generator.get_llm", return_value=llm) as get_llm:
        gen = CodeGenerator(user=None)
        gen.generate("Set fire rating to EI60", _grounding())
        gen.repair("Set fire rating to EI60", _grounding(), VALID, "boom")

    get_llm.assert_called_once_with(
        user=None, purpose="modify", temperature=0.0, num_ctx=NUM_CTX, num_predict=NUM_PREDICT
    )
    assert llm.invoke.call_count == 2


def test_repair_prompt_contains_previous_code_and_error_only_once():
    """The repair prompt is the normal prompt plus the code and the one error string."""
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="x")
    with patch("writeback.services.generator.get_llm", return_value=llm):
        CodeGenerator().repair(
            "req", _grounding(), "def select(model): ...", "scope violation on W6"
        )

    human = llm.invoke.call_args.args[0][1].content
    assert "## Previous attempt" in human
    assert "def select(model): ..." in human
    assert "scope violation on W6" in human
    assert human.count("## Request") == 1


def test_generate_prompt_contains_grounding_helpers_and_sheet():
    """Instructions + grounding + helper signatures + api sheet, nothing else needed."""
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="x")
    with patch("writeback.services.generator.get_llm", return_value=llm):
        CodeGenerator().generate("Set fire rating to EI60", _grounding())

    human = llm.invoke.call_args.args[0][1].content
    assert '"Ground Floor"' in human
    assert "elements_in_storey(" in human
    assert "pset.edit_pset(" in human


def test_extract_rejection_reads_a_reject_line_and_ignores_code_answers():
    """A REJECT: line without a code block is a declared refusal; a fenced answer never is."""
    from writeback.services.generator import extract_rejection

    assert extract_rejection("REJECT: this changes geometry, which is out of scope") == (
        "this changes geometry, which is out of scope"
    )
    assert extract_rejection("Sure.\n```python\n" + VALID + "```") is None
    assert extract_rejection("I cannot do that.") is None


def test_prompt_recipe_uses_the_entitys_own_pset():
    """add_pset then unshare when shared; the type's pset is never reached by id."""
    from writeback.services.generator import SYSTEM_PROMPT

    assert "own = pset.add_pset(product=item" in SYSTEM_PROMPT
    assert "own = pset.unshare_pset(products=[item], pset=own)" in SYSTEM_PROMPT
    assert 'by_id(pset["id"])' not in SYSTEM_PROMPT
    assert "pset = pset." not in SYSTEM_PROMPT, "a variable named pset shadows the api module"


def test_prompt_has_a_creation_recipe_and_does_not_refuse_creation():
    """select() returns the project, modify() calls root.create_entity; a creation is a change."""
    from writeback.services.generator import SYSTEM_PROMPT

    assert 'select() returns model.by_type("IfcProject")' in SYSTEM_PROMPT
    assert 'root.create_entity(ifc_class="<IfcClass>", name="<name>")' in SYSTEM_PROMPT
    assert "never model.create_entity" in SYSTEM_PROMPT
    assert "group.assign_group(products=targets, group=zone)" in SYSTEM_PROMPT
    assert "names nothing to change or\ncreate" in SYSTEM_PROMPT
    assert "names no identifiable target" not in SYSTEM_PROMPT


_HOUSE = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark" / "Ifc4_SampleHouse.ifc"


def test_prompt_recipe_edits_one_entity_when_its_pset_is_shared(tmp_path):
    """The prompt's own example, run in the sandbox on a member whose pset 20 members share.

    ``add_pset`` returns the shared pset; without the unshare step the edit lands on
    all 20 and the scope check rejects a request the spec allows (review 5).
    """
    import shutil

    import ifcopenshell
    import ifcopenshell.util.element as element_util

    from ifc_processor.services.code_sandbox import run_code_subprocess
    from writeback.services.generator import SYSTEM_PROMPT
    from writeback.services.verifier import scope_error

    model = ifcopenshell.open(str(_HOUSE))
    shared = next(
        rel
        for rel in model.by_type("IfcRelDefinesByProperties")
        if len(rel.RelatedObjects) > 1 and rel.RelatingPropertyDefinition.Name == "Construction"
    )
    member = shared.RelatedObjects[0]
    assert member.is_a("IfcMember")
    assert len(element_util.get_elements_by_pset(shared.RelatingPropertyDefinition)) == 20

    # The example's modify() verbatim; its select() is a placeholder (the 20
    # members share one Name), so the selection is pinned to one member here.
    example = extract_code_block(SYSTEM_PROMPT)
    code = (
        f'def select(model):\n    return [model.by_guid("{member.GlobalId}")]\n\n'
        + example[example.index("def modify") :]
    )
    for placeholder, value in {
        "<Pset name>": "Construction",
        "<Property>": "Castor_Test",
        "<value>": "shared-pset-check",
    }.items():
        code = code.replace(placeholder, value)
    scratch = tmp_path / "house.ifc"
    shutil.copy2(_HOUSE, scratch)

    result = run_code_subprocess(scratch, code)

    assert result["targets"] == [member.GlobalId]
    assert scope_error(result["diff"], result["targets"]) is None
    rows = result["diff"]["property_changes"]
    assert [(r["global_id"], r["pset"], r["prop"], r["after"]) for r in rows] == [
        (member.GlobalId, "Construction", "Castor_Test", "shared-pset-check")
    ]


def test_a_model_timeout_is_a_model_unavailable_error():
    """Still a ModificationError for the views; its own class for the benchmark and the classifier."""
    from writeback.services.errors import ModelUnavailableError, ModificationError

    with (
        patch("writeback.services.generator.get_llm", return_value=MagicMock()),
        patch("writeback.services.generator.safe_invoke", side_effect=TimeoutError()),
    ):
        with pytest.raises(ModelUnavailableError, match="did not answer") as excinfo:
            CodeGenerator().generate("req", _grounding())
    assert isinstance(excinfo.value, ModificationError)


def test_an_unreachable_provider_is_a_model_unavailable_error():
    from writeback.services.errors import ModelUnavailableError

    with (
        patch("writeback.services.generator.get_llm", return_value=MagicMock()),
        patch("writeback.services.generator.safe_invoke", side_effect=ConnectionError("refused")),
    ):
        with pytest.raises(ModelUnavailableError, match="could not be reached"):
            CodeGenerator().generate("req", _grounding())


def test_typed_llm_errors_pass_through_untouched():
    """Budget, BYOK and kill-switch errors keep their type: the views word those themselves."""
    from core.llm import TokenBudgetExceededError

    with (
        patch("writeback.services.generator.get_llm", return_value=MagicMock()),
        patch(
            "writeback.services.generator.safe_invoke",
            side_effect=TokenBudgetExceededError("cap", used=1, cap=1),
        ),
    ):
        with pytest.raises(TokenBudgetExceededError):
            CodeGenerator().generate("req", _grounding())


def test_prompt_allows_adding_a_not_yet_set_property():
    """The names rule offers not-yet-set properties and forbids writing to a neighbour."""
    from writeback.services.generator import SYSTEM_PROMPT

    assert 'A property listed as "not yet set" may be added' in SYSTEM_PROMPT
    assert "never to a different property with a similar meaning" in SYSTEM_PROMPT
