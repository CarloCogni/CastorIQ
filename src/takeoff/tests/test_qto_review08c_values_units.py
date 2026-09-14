# takeoff/tests/test_qto_review08c_values_units.py
"""QTO-REVIEW-08C — exact field values + class-scoped output units."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
)
from takeoff.services.quantity_field_values import list_entity_distinct_values
from takeoff.services.quantity_measurement_settings import apply_class_settings
from takeoff.services.quantity_output_units import QuantityOutputUnitsService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _project_multi_class():
    project = ProjectFactory()
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={"LENGTHUNIT": "mm", "AREAUNIT": "m²", "VOLUMEUNIT": "m³"},
    )
    et_b = IFCElementTypeFactory(
        ifc_file=ifc, name="BeamType", ifc_type="IfcBeamType", global_id="ETB"
    )
    et_c = IFCElementTypeFactory(
        ifc_file=ifc, name="ColType", ifc_type="IfcColumnType", global_id="ETC"
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B1",
        element_type=et_b,
        properties={
            "Identity Data.4D status": "Planned",
            "Identity Data.Keynote": "K-B",
            "Qto_BeamBaseQuantities.NetVolume": 2.0,
            "Type.Identity Data.Keynote": "TK-B",
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B2",
        element_type=et_b,
        properties={
            "Identity Data.4D status": "Built",
            "Qto_BeamBaseQuantities.NetVolume": 1.0,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcColumn",
        global_id="C1",
        element_type=et_c,
        properties={
            "Identity Data.4D status": "ColumnOnly",
            "Qto_ColumnBaseQuantities.NetVolume": 3.0,
        },
    )
    return project


@pytest.mark.django_db
def test_entity_distinct_values_exclude_multiple_values_sentinel():
    """Entity suggestions are pre-aggregation distincts, not mixed-cell labels."""
    project = _project_multi_class()
    out = list_entity_distinct_values(
        project=project,
        field_key="prop:Identity Data.4D status",
        selected_classes=["IfcBeam"],
    )
    assert out["error"] is None
    assert set(out["values"]) == {"Built", "Planned"}
    assert "Multiple values" not in out["values"]
    assert "ColumnOnly" not in out["values"]


@pytest.mark.django_db
def test_entity_distinct_values_same_name_different_keys():
    project = _project_multi_class()
    elem = list_entity_distinct_values(
        project=project,
        field_key="prop:Identity Data.Keynote",
    )
    typ = list_entity_distinct_values(
        project=project,
        field_key="prop:Type.Identity Data.Keynote",
    )
    assert "K-B" in elem["values"]
    assert "TK-B" in typ["values"]
    assert elem["values"] != typ["values"]


@pytest.mark.django_db
def test_field_values_endpoint_returns_json(client):
    project = _project_multi_class()
    client.force_login(project.owner)
    url = reverse("takeoff:qty_field_values", kwargs={"pk": project.pk})
    resp = client.get(
        url,
        {
            "field": "prop:Identity Data.4D status",
            "semantic_classes": "IfcBeam",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["values"]) == {"Built", "Planned"}
    assert data["source"] == "entity_index"


@pytest.mark.django_db
def test_class_output_unit_does_not_change_other_class(client):
    """IfcBeam volume unit override leaves IfcColumn on its prior unit."""
    project = _project_multi_class()
    client.force_login(project.owner)
    session = client.session
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,status,actions",
            "semantic_classes": "IfcBeam,IfcColumn",
        },
    )
    rows = list((runtime["qty_prep"] or {}).get("prep_rows") or [])
    before = {
        str(r.get("ifc_class")): {
            "total": r.get("total"),
            "unit": r.get("output_unit") or r.get("unit_basis"),
        }
        for r in rows
        if r.get("ifc_class") in {"IfcBeam", "IfcColumn"}
    }
    units = QuantityOutputUnitsService(project, project.owner, session)
    # Prefer a non-identity unit if available; otherwise skip conversion assert.
    choices = ["cm3", "mm3", "m3", "ft3"]
    model = units.effective_output_units().get("volume") or "m3"
    target = next((c for c in choices if c != model), None)
    if not target:
        pytest.skip("no alternate volume unit available")

    result = apply_class_settings(
        project=project,
        user=project.owner,
        session=session,
        ifc_class="IfcBeam",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit=target,
        prep_rows=rows,
    )
    assert result.get("ok"), result
    session.save()

    runtime2 = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={
            "table_layout": "v2",
            "col_order": "ifc_class,name,status,actions",
            "semantic_classes": "IfcBeam,IfcColumn",
        },
    )
    after_rows = list((runtime2["qty_prep"] or {}).get("prep_rows") or [])
    after = {
        str(r.get("ifc_class")): {
            "total": r.get("total"),
            "unit": r.get("output_unit") or r.get("unit_basis"),
        }
        for r in after_rows
        if r.get("ifc_class") in {"IfcBeam", "IfcColumn"}
    }
    assert units.get_class_units().get("IfcBeam", {}).get("volume") == target
    assert "IfcColumn" not in units.get_class_units()
    # Column unit unchanged vs before (same token).
    if before.get("IfcColumn") and after.get("IfcColumn"):
        assert after["IfcColumn"]["unit"] == before["IfcColumn"]["unit"]
    if before.get("IfcBeam") and after.get("IfcBeam") and before["IfcBeam"]["unit"] != target:
        assert after["IfcBeam"]["unit"] == target


@pytest.mark.django_db
def test_filter_bar_compact_dropdown_and_values_url(client):
    project = _project_multi_class()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"table_layout": "v2", "col_order": "ifc_class,name,status,actions"},
    ).content.decode()
    assert 'data-testid="qty-filter-field-toggle"' in html
    assert 'data-testid="qty-filter-field-menu"' in html
    assert "/field-values/" in html
    assert 'id="qty-field-samples-json"' not in html
