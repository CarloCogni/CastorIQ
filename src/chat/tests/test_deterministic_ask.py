# chat/tests/test_deterministic_ask.py
"""Tests for the deterministic Ask recipe table.

Routing (pattern matching + term normalization) is pure logic; executor
integration runs on factory data. A false-positive match silently routes a
question away from RAG, so the negative cases matter as much as the hits.
"""

from __future__ import annotations

import pytest

from chat.services.deterministic_ask import (
    RECIPES,
    _normalize_count_term,
    match_and_execute,
)

# ── Term normalization ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("doors", "IfcDoor"),
        ("door", "IfcDoor"),
        # Qualified terms must NOT ground to the bare type — counting every
        # door and labelling it "verified" answers a different question.
        ("fire doors", None),
        ("the walls", "IfcWall"),
        ("storeys", "IfcBuildingStorey"),
        ("stories", "IfcBuildingStorey"),
        ("levels", "IfcBuildingStorey"),
        ("rooms", "IfcSpace"),
        ("IfcWallStandardCase", "IfcWallStandardCase"),
        ("bananas", None),
        ("", None),
    ],
)
def test_normalize_count_term(term, expected):
    """NL terms ground to IFC types; unknown terms return None, never a guess."""
    assert _normalize_count_term(term) == expected


# ── Pattern routing (no DB): which recipe would fire ───────────────────


def _matching_recipe(text: str):
    for recipe in RECIPES:
        if any(p.search(text) for p in recipe.patterns):
            return recipe.question_class
    return None


@pytest.mark.parametrize(
    ("text", "expected_class"),
    [
        ("How many doors are in the model?", "entity count"),
        ("number of windows in the building", "entity count"),
        ("What is the total wall area?", "total wall area"),
        ("total floor area of the building", "total floor/slab area"),
        ("What is the total volume?", "total volume"),
        ("window to wall ratio?", "window-to-wall ratio"),
        ("What is the WWR?", "window-to-wall ratio"),
        ("List the storeys of the building", "storey list"),
        ("Are there duplicate GlobalIds?", "duplicate GlobalIds"),
        ("Which IFC schema version is this model?", "IFC schema version"),
        # Phrasings previously caught by the retired ifc_inventory keywords:
        ("What elements are in the model?", "element type breakdown"),
        ("What kind of elements does the model contain?", "element type breakdown"),
        ("List all elements", "element type breakdown"),
        ("Show me the types of elements", "element type breakdown"),
        ("Give me the model inventory", "element type breakdown"),
    ],
)
def test_recipes_match_their_questions(text, expected_class):
    """Each flagship phrasing routes to its recipe."""
    assert _matching_recipe(text) == expected_class


@pytest.mark.parametrize(
    "text",
    [
        "Tell me about the fire safety strategy",
        "What material are the walls made of?",
        "Who is the architect?",
        "Summarize the specification",
        # The t2-materials phrasing that the retired keyword intent hijacked —
        # it must reach vector retrieval, where the material-rich descriptions live.
        "What materials are used in the building elements, such as the walls and slabs?",
    ],
)
def test_qualitative_questions_do_not_match(text):
    """Narrative questions must fall through to RAG."""
    assert _matching_recipe(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "How many doors are under warranty?",
        "How many walls are on level 2?",
        "Number of doors in the north facade",
        "How many doors are made of steel?",
    ],
)
def test_qualified_count_questions_decline(text):
    """A count question with a qualifier must NOT return an unqualified total."""
    from chat.services.deterministic_ask import RECIPES, _count_executor

    count_recipe = RECIPES[0]
    hit = next(p.search(text) for p in count_recipe.patterns if p.search(text))
    # The guard fires before the service is touched, so None suffices here.
    result = _count_executor(None, hit)
    assert result["value"] is None


@pytest.mark.parametrize(
    "text",
    [
        "How many doors are in the model?",
        "How many walls are there?",
        "Number of windows in the building",
    ],
)
def test_unqualified_count_questions_pass_the_context_guard(text):
    """Plain count phrasings must still reach the executor."""
    from chat.services.deterministic_ask import RECIPES, _count_context_is_qualified

    count_recipe = RECIPES[0]
    hit = next(p.search(text) for p in count_recipe.patterns if p.search(text))
    assert not _count_context_is_qualified(hit)


@pytest.mark.django_db
def test_how_many_elements_returns_type_breakdown():
    """'How many elements' has no single type — the breakdown answers it."""
    from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

    ifc_file = IFCFileFactory(schema_version="IFC4")
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcDoor")

    outcome = match_and_execute(ifc_file.project, "How many elements are in the model?")
    assert outcome is not None
    assert outcome.question_class == "entity count"
    assert outcome.result["value"] == 2
    assert outcome.result["rows"]


# ── End-to-end guards and execution (DB) ───────────────────────────────


@pytest.mark.django_db
def test_document_questions_never_route_deterministically():
    """'How many documents mention walls' is a doc question — RAG's job."""
    from environments.tests.factories import ProjectFactory

    project = ProjectFactory()
    assert match_and_execute(project, "How many documents mention walls?") is None


@pytest.mark.django_db
def test_project_without_model_falls_through():
    """No completed IFC file → no deterministic path at all."""
    from environments.tests.factories import ProjectFactory

    project = ProjectFactory()
    assert match_and_execute(project, "How many doors are there?") is None


@pytest.mark.django_db
def test_count_executes_with_subtype_expansion():
    """Counting walls includes IfcWallStandardCase rows via schema metadata."""
    from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

    ifc_file = IFCFileFactory(schema_version="IFC4")
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWallStandardCase")
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcDoor")

    outcome = match_and_execute(ifc_file.project, "How many walls are in the model?")
    assert outcome is not None
    assert outcome.result["value"] == 2
    assert "IfcWallStandardCase" in outcome.result["method"]


@pytest.mark.django_db
def test_missing_quantities_fall_back_to_rag():
    """A wall-area question on a model without Qto psets returns None."""
    from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

    ifc_file = IFCFileFactory(schema_version="IFC4")
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")  # no Qto props

    assert match_and_execute(ifc_file.project, "What is the total wall area?") is None


@pytest.mark.django_db
def test_facts_block_contains_value_method_and_source():
    """The narration prompt block carries value, method, and provenance."""
    from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

    ifc_file = IFCFileFactory(schema_version="IFC4")
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcDoor")

    outcome = match_and_execute(ifc_file.project, "How many doors are there?")
    block = outcome.to_facts_block()
    assert "VERIFIED COMPUTED FACTS" in block
    assert "Value: 1" in block
    assert "Method:" in block
    assert "do not recompute" in block.lower()
