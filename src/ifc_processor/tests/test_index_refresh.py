# ifc_processor/tests/test_index_refresh.py
"""`refresh_entities` upserts from the file and deletes rows the file no longer has."""

import shutil
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import pytest
from django.core.files import File

from ifc_processor.models import IFCElementType, IFCEntity
from ifc_processor.services.index_refresh import refresh_entities
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
STOREY_GUID = "2QUC4Ju095PxII7fiX8qqs"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "simple_wall.ifc"
HOUSE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark" / "Ifc4_SampleHouse.ifc"
WALL_TYPE_GUID = "2ru7YPT4T9MuTpOS4FRzxX"


@pytest.fixture
def ifc_file(tmp_path):
    """An IFCFile whose storage file is a real copy of the simple wall fixture."""
    copy = tmp_path / "wall.ifc"
    shutil.copy(FIXTURE_PATH, copy)
    ifc_file = IFCFileFactory()
    with open(copy, "rb") as fh:
        ifc_file.file.save("wall.ifc", File(fh), save=True)
    return ifc_file


@pytest.mark.django_db
def test_refresh_upserts_a_present_entity_from_the_file(ifc_file):
    """A GlobalId in the file lands in the index with the parser's property shape."""
    refreshed, removed = refresh_entities(ifc_file, [WALL1_GUID])

    assert (refreshed, removed) == (1, 0)
    row = IFCEntity.objects.get(ifc_file=ifc_file, global_id=WALL1_GUID)
    assert row.ifc_type == "IfcWall"
    assert row.name == "TestWall-001"


@pytest.mark.django_db
def test_refresh_deletes_a_row_the_file_no_longer_has(ifc_file):
    """A GlobalId missing from the file removes its stale index row."""
    IFCEntityFactory(ifc_file=ifc_file, global_id="GONE-000001")

    refreshed, removed = refresh_entities(ifc_file, ["GONE-000001"])

    assert (refreshed, removed) == (0, 1)
    assert not IFCEntity.objects.filter(global_id="GONE-000001").exists()


@pytest.mark.django_db
def test_refresh_survives_an_unreadable_file(ifc_file):
    """An unreadable file logs and returns zeros; the index is left alone."""
    Path(ifc_file.file.path).write_bytes(b"not an ifc")

    assert refresh_entities(ifc_file, [WALL1_GUID]) == (0, 0)


@pytest.mark.django_db
def test_refresh_never_indexes_a_property_set_or_relationship(ifc_file):
    """The diff's population can be passed as is: a class the parser does not index is skipped."""
    model = ifcopenshell.open(ifc_file.file.path)
    pset = ifcopenshell.api.run(
        "pset.add_pset", model, product=model.by_guid(WALL1_GUID), name="Pset_Custom"
    )
    rel = pset.DefinesOccurrence[0]
    model.write(ifc_file.file.path)

    assert refresh_entities(ifc_file, [pset.GlobalId, rel.GlobalId]) == (0, 0)
    assert not IFCEntity.objects.filter(ifc_file=ifc_file).exists()


@pytest.mark.django_db
def test_refresh_links_the_direct_spatial_container(ifc_file):
    """An upserted row gets the same spatial_container a full parse would give it."""
    storey_entity = IFCEntityFactory(
        ifc_file=ifc_file, global_id=STOREY_GUID, ifc_type="IfcBuildingStorey", name="Level 1"
    )
    storey = IFCSpatialElementFactory(
        ifc_file=ifc_file, entity=storey_entity, spatial_type="building_storey"
    )

    refresh_entities(ifc_file, [WALL1_GUID])

    assert (
        IFCEntity.objects.get(ifc_file=ifc_file, global_id=WALL1_GUID).spatial_container == storey
    )


@pytest.mark.slow
@pytest.mark.django_db
def test_refresh_of_a_type_object_reaches_its_row_and_every_occurrence(tmp_path):
    """A type pset edit refreshes the IFCElementType row and the occurrences that inherit it."""
    ifc_file = IFCFileFactory()
    with open(HOUSE_PATH, "rb") as fh:
        ifc_file.file.save("house.ifc", File(fh), save=True)
    model = ifcopenshell.open(ifc_file.file.path)
    wall_type = model.by_guid(WALL_TYPE_GUID)
    occurrences = [e.GlobalId for e in ifcopenshell.util.element.get_types(wall_type)]
    pset = ifcopenshell.api.run("pset.add_pset", model, product=wall_type, name="Pset_WallCommon")
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"FireRating": "EI60"})
    model.write(ifc_file.file.path)

    refreshed, removed = refresh_entities(ifc_file, [WALL_TYPE_GUID])

    assert (refreshed, removed) == (1 + len(occurrences), 0)
    type_row = IFCElementType.objects.get(ifc_file=ifc_file, global_id=WALL_TYPE_GUID)
    assert type_row.properties["Pset_WallCommon.FireRating"] == "EI60"
    for gid in occurrences:
        row = IFCEntity.objects.get(ifc_file=ifc_file, global_id=gid)
        assert row.properties["Type.Pset_WallCommon.FireRating"] == "EI60"
    assert not IFCEntity.objects.filter(ifc_file=ifc_file, global_id=WALL_TYPE_GUID).exists()
