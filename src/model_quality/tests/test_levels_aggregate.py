# model_quality/tests/test_levels_aggregate.py
"""Regression tests for storey aggregation in the Level Panel.

`apply_levels_to_ifc` used to call ``aggregate.assign_object`` with the
singular ``product=`` keyword. IfcOpenShell 0.8.x requires ``products``
(a list), so the call raised TypeError — which a broad ``except Exception``
swallowed into an error string. Storeys were created but silently left
unaggregated (no IfcRelAggregates to the building) while the Level record
still saved ``ifc_storey_global_id`` as if it had worked.

These pin the API contract the fix depends on, so an IfcOpenShell upgrade
that changes the signature again fails here rather than in production.
"""

import shutil
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import pytest

FIXTURE_PATH = (
    Path(__file__).parents[1].parent / "ifc_processor" / "tests" / "fixtures" / "simple_wall.ifc"
)


@pytest.fixture
def model(tmp_path: Path):
    dest = tmp_path / "levels.ifc"
    shutil.copy(FIXTURE_PATH, dest)
    return ifcopenshell.open(str(dest))


def _create_storey(model, name: str):
    return ifcopenshell.api.run(
        "root.create_entity", model, ifc_class="IfcBuildingStorey", name=name
    )


@pytest.mark.slow
def test_new_storey_is_aggregated_to_the_building(model):
    """The plural `products=[...]` form actually links storey → building."""
    building = model.by_type("IfcBuilding")[0]
    storey = _create_storey(model, "Level 99")

    ifcopenshell.api.run(
        "aggregate.assign_object",
        model,
        relating_object=building,
        products=[storey],
    )

    aggregated = [
        obj
        for rel in building.IsDecomposedBy
        for obj in rel.RelatedObjects
        if obj.GlobalId == storey.GlobalId
    ]
    assert aggregated, "New storey must appear in the building's IsDecomposedBy"


@pytest.mark.slow
def test_singular_product_keyword_is_rejected(model):
    """Document the exact call that silently broke: `product=` is a TypeError.

    If a future IfcOpenShell re-accepts it this test fails loudly, which is
    the signal to revisit the call site rather than discover it in prod.
    """
    building = model.by_type("IfcBuilding")[0]
    storey = _create_storey(model, "Level 98")

    with pytest.raises(TypeError):
        ifcopenshell.api.run(
            "aggregate.assign_object",
            model,
            relating_object=building,
            product=storey,
        )
