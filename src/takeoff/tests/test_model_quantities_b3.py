# takeoff/tests/test_model_quantities_b3.py
"""Package B3 — Model Quantities page (read-only aggregates, honest wording)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)
from takeoff.services.ifc_qto_flags import entity_has_ifc_quantity
from takeoff.services.model_quantities import ModelQuantitiesService


@pytest.mark.django_db
def test_id_only_qto_does_not_count_as_availability():
    """Qto_*.id alone is not Has IFC Qto."""
    assert entity_has_ifc_quantity({"Qto_BeamBaseQuantities.id": 12345}) is False
    assert entity_has_ifc_quantity({"Qto_BeamBaseQuantities": {"id": 99}}) is False
    assert (
        entity_has_ifc_quantity(
            {
                "Qto_BeamBaseQuantities.id": 1,
                "Qto_BeamBaseQuantities.NetVolume": 2.5,
            }
        )
        is True
    )


@pytest.mark.django_db
def test_aggregation_by_class_level_and_missing():
    """Named measure sums + missing counts; no properties/GIDs in payload."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    storey_ent = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBuildingStorey",
        name="L1",
        global_id="GID-STOREY",
        properties={},
    )
    storey = IFCSpatialElementFactory(
        ifc_file=ifc, entity=storey_ent, spatial_type="building_storey"
    )
    et = IFCElementTypeFactory(ifc_file=ifc, name="WallType-A", ifc_type="IfcWallType")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        name="W1",
        global_id="GID-W1",
        spatial_container=storey,
        element_type=et,
        properties={
            "Qto_WallBaseQuantities.NetVolume": 3.0,
            "Qto_WallBaseQuantities.NetSideArea": 12.0,
            "Qto_WallBaseQuantities.Length": 4000.0,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        name="W2",
        global_id="GID-W2",
        spatial_container=storey,
        element_type=et,
        properties={"Qto_WallBaseQuantities.id": 77},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcSlab",
        name="S1",
        global_id="GID-S1",
        spatial_container=None,
        properties={"Qto_SlabBaseQuantities": {"NetArea": 20.0}},
    )

    payload = ModelQuantitiesService(project).build()
    rd = payload["readiness"]
    assert rd["total_entities"] == 4  # storey + 2 walls + slab
    assert rd["entities_with_quantity"] == 2
    assert rd["missing_quantity_count"] == 2
    assert rd["classification_coverage"] == "unavailable"

    walls = next(r for r in payload["by_ifc_class"] if r["ifc_class"] == "IfcWall")
    assert walls["element_count"] == 2
    assert walls["has_ifc_qto"] == 1
    assert walls["missing_qto"] == 1
    assert walls["net_volume"] == 3.0
    assert walls["net_side_area"] == 12.0
    assert walls["length"] == 4000.0

    levels = {r["level_label"]: r for r in payload["by_level"]}
    assert levels["L1"]["has_ifc_qto"] == 1
    assert levels["Unassigned"]["missing_qto"] >= 1

    missing_classes = {
        r["ifc_class"]: r["missing_qto"] for r in payload["missing_quantities"]["by_ifc_class"]
    }
    assert missing_classes.get("IfcWall") == 1

    blob = str(payload)
    assert "GID-W1" not in blob
    assert "Qto_WallBaseQuantities" not in blob
    assert "items_json" not in blob
    assert "unit_cost" not in blob
    assert "total_cost" not in blob
    # Caveat may say "model properties" — forbid property payloads / dumps.
    assert "'properties':" not in blob
    assert '"properties"' not in blob


@pytest.mark.django_db
def test_by_type_capped_when_populated():
    """By Type appears when element_type is populated; capped at 50."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    for i in range(3):
        et = IFCElementTypeFactory(
            ifc_file=ifc, name=f"Type-{i}", ifc_type="IfcWallType", global_id=f"TYPE-{i}"
        )
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcWall",
            global_id=f"GID-W-{i}",
            element_type=et,
            properties={"Qto_WallBaseQuantities.NetVolume": 1.0 + i},
        )

    payload = ModelQuantitiesService(project).build()
    assert payload["by_type_shown"] is True
    assert len(payload["by_type"]) == 3
    assert all("net_volume" in r for r in payload["by_type"])


@pytest.mark.django_db
def test_quantities_page_sections_and_honesty(client):
    """First paint: readiness/class/level/missing; classification unavailable; no cost KPIs."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcColumn",
        global_id="GID-C1",
        properties={"Qto_ColumnBaseQuantities.NetVolume": 1.5},
    )
    client.force_login(project.owner)

    response = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="quantities-page"' in html
    assert 'data-testid="quantity-readiness"' in html
    assert 'data-testid="quantity-by-ifc-class"' in html
    assert 'data-testid="quantity-by-level"' in html
    assert 'data-testid="missing-quantities"' in html
    assert 'data-testid="qty-classification-unavailable"' in html
    assert "Unavailable" in html
    assert "Length (model units)" in html
    assert "model units" in html.lower()
    assert "Inspect IFC Elements" in html
    assert "has_qto=no" in html
    assert "GID-C1" not in html
    assert "items_json" not in html
    assert "Qto_ColumnBaseQuantities" not in html

    # Cost dashboard not primary
    assert "Optional unit-cost estimate" not in html
    assert "Estimated total by level" not in html
    assert "qto-level-canvas" not in html
    assert "total_cost_estimate" not in html

    # Scope honesty check to the Quantities root (ignore unrelated shell chrome).
    m = re.search(
        r'data-testid="quantities-page"(.*?)data-testid="quantities-not-claims"',
        html,
        re.DOTALL,
    )
    assert m, "quantities page root / not-claims marker missing"
    body = m.group(1)
    # Strip explicit negation caveats, then forbid remaining claim phrases.
    cleaned = re.sub(
        r"(?i)\bnot\b[^.<]{0,200}?(?:BOQ|QS valuation|ERP|invoice|procurement|"
        r"payment|company actual cost|commercial 5D)[^.<]{0,120}",
        "",
        body,
    )
    cleaned = (
        cleaned.replace("Not BOQ", "")
        .replace("not BOQ / not commercial cost", "")
        .replace("must not be treated as commercial cost control", "")
    )
    for phrase in (
        "Bill of Quantities",
        "Commercial 5D",
        "Procurement ledger",
        "Invoice actuals",
        "Optional unit-cost estimate",
        "Estimated total by level",
        "qto-level-canvas",
        "total_cost_estimate",
    ):
        assert phrase not in cleaned, phrase

    assert len(response.content) < 200_000


@pytest.mark.django_db
def test_hub_quantities_still_points_to_qto_route(client):
    project = ProjectFactory()
    client.force_login(project.owner)
    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    ).content.decode()
    assert 'data-testid="hub-quantities"' in html
    assert reverse("takeoff:qto", kwargs={"pk": project.pk}) in html
