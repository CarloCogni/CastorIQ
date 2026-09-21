# takeoff/tests/test_measurement_resolver_measure01.py
"""R5D-QTO-MEASURE-01 — stable measurement targets + resolver foundation."""

from __future__ import annotations

from uuid import uuid4

import pytest

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
)
from takeoff.services.measurement_resolver import (
    MeasurementResolverService,
    inventory_from_aggregate_row,
    resolve_measurement,
)
from takeoff.services.measurement_target import (
    build_measurement_target_key,
    measurement_target_from_aggregate_row,
)
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_prep_row_review import build_row_key


def test_measurement_target_stable_when_measurement_source_changes():
    """Target key excludes measurement type and IFC source."""
    tid = uuid4()
    key_a = build_measurement_target_key(
        grain="type",
        ifc_class="IfcBeam",
        element_type_id=tid,
        type_name="Rib Beam",
    )
    key_b = build_measurement_target_key(
        grain="type",
        ifc_class="IfcBeam",
        element_type_id=tid,
        type_name="Rib Beam",
    )
    assert key_a == key_b
    assert "NetVolume" not in key_a
    assert "volume" not in key_a
    assert key_a.startswith("mt1|type|IfcBeam|id:")


def test_measurement_target_includes_ifc_class_for_same_type_name():
    """Identical type names under different classes must not collide."""
    a = build_measurement_target_key(
        grain="type",
        ifc_class="IfcBeam",
        type_name="Generic",
    )
    b = build_measurement_target_key(
        grain="type",
        ifc_class="IfcColumn",
        type_name="Generic",
    )
    assert a != b
    assert "|IfcBeam|" in a
    assert "|IfcColumn|" in b


def test_measurement_target_prefers_stable_type_id_over_name():
    """element_type_id wins over type name when both are present."""
    tid = uuid4()
    key = build_measurement_target_key(
        grain="type",
        ifc_class="IfcWall",
        element_type_id=tid,
        type_name="Wall A",
    )
    assert f"id:{tid}" in key
    assert "name:Wall A" not in key


def test_measurement_target_type_name_fallback_deterministic():
    """Name fallback normalizes whitespace and is stable."""
    a = build_measurement_target_key(
        grain="type",
        ifc_class="IfcSlab",
        type_name="  Floor:THK  200  ",
    )
    b = build_measurement_target_key(
        grain="type",
        ifc_class="IfcSlab",
        type_name="Floor:THK 200",
    )
    assert a == b
    assert a.endswith("|name:Floor:THK 200")


def test_legacy_row_key_still_includes_basis_unchanged():
    """Legacy row_key contract is preserved (includes quantity_basis)."""
    legacy = build_row_key(
        grain="type",
        ifc_class="IfcBeam",
        type_name="Rib",
        quantity_basis="NetVolume",
    )
    assert legacy == "v1|type|IfcBeam|Rib|NetVolume"
    mt = build_measurement_target_key(
        grain="type",
        ifc_class="IfcBeam",
        type_name="Rib",
    )
    assert mt != legacy
    assert mt.startswith("mt1|")


def test_count_auto_resolves_from_element_count():
    """Count uses synthetic element_count and preserves numeric zero."""
    result = resolve_measurement(
        measurement_type="count",
        inventory={"element_count": 0},
    )
    assert result.status == "resolved"
    assert result.selected_source == "element_count"
    assert result.total == 0
    assert result.model_unit_family == "count"


def test_length_one_source_resolves():
    """Single Length source auto-resolves."""
    result = resolve_measurement(
        measurement_type="length",
        inventory={"element_count": 3, "Length": 1200.0},
    )
    assert result.status == "resolved"
    assert result.selected_source == "Length"
    assert result.total == 1200.0


def test_area_one_source_resolves():
    """Single NetArea auto-resolves; GrossArea absent."""
    result = resolve_measurement(
        measurement_type="area",
        inventory={"element_count": 2, "NetArea": 1630.06},
    )
    assert result.status == "resolved"
    assert result.selected_source == "NetArea"
    assert result.total == 1630.06
    assert result.compatible_sources == ("NetArea",)


def test_area_multi_source_choice_required():
    """NetArea and GrossArea remain distinct → choice_required."""
    result = resolve_measurement(
        measurement_type="area",
        inventory={"element_count": 1, "NetArea": 10.0, "GrossArea": 12.0},
    )
    assert result.status == "choice_required"
    assert result.selected_source is None
    assert result.total is None
    assert result.compatible_sources == ("NetArea", "GrossArea")


def test_volume_multi_source_choice_required():
    """NetVolume and GrossVolume → choice_required before explicit source."""
    result = resolve_measurement(
        measurement_type="volume",
        inventory={"element_count": 10, "NetVolume": 960.29, "GrossVolume": 472.24},
    )
    assert result.status == "choice_required"
    assert result.compatible_sources == ("NetVolume", "GrossVolume")
    assert result.total is None


def test_explicit_net_volume_selection():
    """Explicit NetVolume resolves to that total only."""
    result = resolve_measurement(
        measurement_type="volume",
        inventory={"element_count": 10, "NetVolume": 960.29, "GrossVolume": 472.24},
        selected_source="NetVolume",
    )
    assert result.status == "resolved"
    assert result.selected_source == "NetVolume"
    assert result.total == 960.29


def test_explicit_gross_volume_selection():
    """Explicit GrossVolume resolves to that total only."""
    result = resolve_measurement(
        measurement_type="volume",
        inventory={"element_count": 10, "NetVolume": 960.29, "GrossVolume": 472.24},
        selected_source="GrossVolume",
    )
    assert result.status == "resolved"
    assert result.selected_source == "GrossVolume"
    assert result.total == 472.24


def test_incompatible_source_rejected():
    """Length is not a volume source — no silent fallback."""
    result = resolve_measurement(
        measurement_type="volume",
        inventory={"element_count": 1, "NetVolume": 1.0, "Length": 9.0},
        selected_source="Length",
    )
    assert result.status == "unavailable"
    assert result.selected_source is None
    assert result.total is None


def test_side_area_not_area_compatible():
    """NetSideArea must not auto-qualify as Area without an explicit rule."""
    result = resolve_measurement(
        measurement_type="area",
        inventory={"element_count": 1, "NetSideArea": 99.0},
    )
    assert result.status == "unavailable"
    assert result.compatible_sources == ()
    assert result.total is None


def test_missing_source_unavailable_not_zero():
    """Unavailable totals are None, never invented zero."""
    result = resolve_measurement(
        measurement_type="volume",
        inventory={"element_count": 4},
    )
    assert result.status == "unavailable"
    assert result.total is None


def test_numeric_zero_preserved_for_present_measure():
    """Present volume of 0.0 stays 0, not None."""
    result = resolve_measurement(
        measurement_type="volume",
        inventory={"element_count": 1, "NetVolume": 0.0},
    )
    assert result.status == "resolved"
    assert result.total == 0
    assert result.total is not None


def test_missing_remains_none_in_inventory_adapter():
    """inventory_from_aggregate_row keeps missing as None and 0 as 0."""
    row = {
        "element_count": 2,
        "measure_inventory": {
            "NetVolume": 0.0,
            "GrossVolume": None,
            "NetArea": None,
            "GrossArea": None,
            "Length": None,
        },
    }
    inv = inventory_from_aggregate_row(row)
    assert inv["NetVolume"] == 0.0
    assert "GrossVolume" not in inv or inv.get("GrossVolume") is None
    zero = resolve_measurement(measurement_type="volume", inventory=inv)
    assert zero.status == "resolved"
    assert zero.total == 0


def test_same_ifc_class_rows_independent_choices():
    """Two type rows under IfcBeam can resolve different sources without key collision."""
    t1 = uuid4()
    t2 = uuid4()
    key1 = build_measurement_target_key(
        grain="type", ifc_class="IfcBeam", element_type_id=t1, type_name="Rib"
    )
    key2 = build_measurement_target_key(
        grain="type", ifc_class="IfcBeam", element_type_id=t2, type_name="BU"
    )
    assert key1 != key2
    r1 = MeasurementResolverService().resolve(
        measurement_type="volume",
        inventory={"element_count": 1, "NetVolume": 960.29, "GrossVolume": 472.24},
        selected_source="NetVolume",
        measurement_target_key=key1,
    )
    r2 = MeasurementResolverService().resolve(
        measurement_type="volume",
        inventory={"element_count": 1, "NetVolume": 5.43, "GrossVolume": 5.39},
        selected_source="GrossVolume",
        measurement_target_key=key2,
    )
    assert r1["measurement_target_key"] != r2["measurement_target_key"]
    assert r1["selected_source"] == "NetVolume"
    assert r1["total"] == 960.29
    assert r2["selected_source"] == "GrossVolume"
    assert r2["total"] == 5.39


@pytest.mark.django_db
def test_aggregate_exposes_element_type_id_and_inventory_zero_truth():
    """ModelQuantities adds element_type_id + measure_inventory with 0≠missing."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    et = IFCElementTypeFactory(ifc_file=ifc, name="ZeroVol", ifc_type="IfcBeamType")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-ZV",
        element_type=et,
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcStair",
        global_id="GID-ST",
        element_type=IFCElementTypeFactory(
            ifc_file=ifc, name="StairX", ifc_type="IfcStairType", global_id="TYPE-ST"
        ),
        properties={},
    )

    payload = ModelQuantitiesService(project).build()
    beam = next(r for r in payload["by_type"] if r["type_name"] == "ZeroVol")
    assert beam["element_type_id"] == str(et.pk)
    assert beam["measure_inventory"]["NetVolume"] == 0.0
    # Legacy display may still collapse zero → None; inventory must not.
    assert beam["measure_inventory"]["GrossVolume"] is None

    target = measurement_target_from_aggregate_row(beam, grain="type")
    assert f"id:{et.pk}" in target

    resolved = resolve_measurement(
        measurement_type="volume",
        inventory=inventory_from_aggregate_row(beam),
    )
    assert resolved.status == "resolved"
    assert resolved.total == 0

    stair = next(r for r in payload["by_type"] if r["type_name"] == "StairX")
    missing = resolve_measurement(
        measurement_type="volume",
        inventory=inventory_from_aggregate_row(stair),
    )
    assert missing.status == "unavailable"
    assert missing.total is None


@pytest.mark.django_db
def test_same_type_name_different_class_distinct_targets_in_aggregates():
    """Aggregate type buckets keyed by class+name; targets include class."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    et_beam = IFCElementTypeFactory(
        ifc_file=ifc, name="SharedName", ifc_type="IfcBeamType", global_id="TB"
    )
    et_col = IFCElementTypeFactory(
        ifc_file=ifc, name="SharedName", ifc_type="IfcColumnType", global_id="TC"
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B",
        element_type=et_beam,
        properties={"Qto_BeamBaseQuantities.NetVolume": 1.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcColumn",
        global_id="GID-C",
        element_type=et_col,
        properties={"Qto_ColumnBaseQuantities.NetVolume": 2.0},
    )
    payload = ModelQuantitiesService(project).build()
    rows = [r for r in payload["by_type"] if r["type_name"] == "SharedName"]
    assert len(rows) == 2
    keys = {measurement_target_from_aggregate_row(r, grain="type") for r in rows}
    assert len(keys) == 2
