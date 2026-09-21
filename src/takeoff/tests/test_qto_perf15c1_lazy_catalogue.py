# takeoff/tests/test_qto_perf15c1_lazy_catalogue.py
"""QTO-PERF-15C1 — defer IFC field catalogues off the initial Quantities GET."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
)
from takeoff.services.ifc_semantic_fields import (
    apply_semantic_filters_to_qty_prep,
    enrich_prep_rows_with_entity_semantics,
    label_for_stable_field_key,
)


def _project_with_props():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    et = IFCElementTypeFactory(ifc_file=ifc, name="Rib", ifc_type="IfcBeamType", global_id="ET1")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="G1",
        element_type=et,
        properties={
            "Identity Data.Keynote": "K-1",
            "Qto_BeamBaseQuantities.NetVolume": 2.5,
            "Other.Category": "Cat",
            "Other.Family": "Fam",
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="G2",
        element_type=et,
        properties={
            "Identity Data.Keynote": "K-2",
            "Qto_BeamBaseQuantities.NetVolume": 1.0,
        },
    )
    return project, ifc


@pytest.mark.django_db
def test_label_for_stable_field_key_without_scan():
    assert label_for_stable_field_key("ifc_class") == "IFC Class"
    assert label_for_stable_field_key("prop:Qto_BeamBaseQuantities.NetVolume") == "NetVolume"
    assert label_for_stable_field_key("spatial:storey") == "Level / Storey (spatial)"


@pytest.mark.django_db
def test_enrich_skips_catalogue_scan_when_idle():
    project, ifc = _project_with_props()
    rows = [{"ifc_class": "IfcBeam", "type_name": "Rib", "level": "class", "row_key": "r1"}]
    with patch(
        "takeoff.services.ifc_semantic_fields._scan_entities",
        wraps=__import__(
            "takeoff.services.ifc_semantic_fields", fromlist=["_scan_entities"]
        )._scan_entities,
    ) as scanned:
        meta = enrich_prep_rows_with_entity_semantics(
            project,
            rows,
            selected_prop_columns=[],
            ifc_file=ifc,
            discover_catalogue=False,
        )
    assert meta["entity_count_scanned"] == 0
    assert meta.get("catalogue_discovered") is False
    assert "zone" in meta["unavailable"]
    # Idle path must not invoke the entity iterator at all.
    assert scanned.call_count == 0


@pytest.mark.django_db
def test_enrich_still_scans_for_visible_property_columns():
    project, ifc = _project_with_props()
    col = "prop:Identity Data.Keynote"
    rows = [
        {
            "ifc_class": "IfcBeam",
            "type_name": "Rib",
            "level": "instance",
            "row_key": "i1",
            "global_id": "G1",
        }
    ]
    meta = enrich_prep_rows_with_entity_semantics(
        project,
        rows,
        selected_prop_columns=[col],
        ifc_file=ifc,
        discover_catalogue=False,
    )
    assert meta["entity_count_scanned"] > 0
    assert meta.get("catalogue_discovered") is False
    assert rows[0].get(col) in {"K-1", "K-2"} or rows[0].get("prop_columns")


@pytest.mark.django_db
def test_apply_defers_picker_hierarchy_and_keeps_active_label():
    project, ifc = _project_with_props()
    qty_prep = {
        "prep_rows": [
            {
                "ifc_class": "IfcBeam",
                "type_name": "Rib",
                "level": "class",
                "row_key": "r1",
            }
        ],
        "selected_classes": [],
        "available_classes": ["IfcBeam"],
        "baseline_row_count": 1,
    }
    panel = apply_semantic_filters_to_qty_prep(
        qty_prep,
        project=project,
        query={
            "semantic_field": "prop:Qto_BeamBaseQuantities.NetVolume",
            "semantic_op": "gt",
            "semantic_value": "0.5",
            "semantic_value_type": "numeric",
        },
        ifc_file=ifc,
        defer_field_catalogue=True,
    )
    assert panel["field_catalogue_lazy"] is True
    assert panel["picker_hierarchy"] == []
    assert panel["column_picker_hierarchy"] == []
    assert panel["active_field_label"] == "NetVolume"
    assert panel["active_value_type"] == "numeric"
    assert "zone" in panel["unavailable"]


@pytest.mark.django_db
def test_initial_qto_get_has_no_field_option_tree(client):
    project, _ifc = _project_with_props()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert 'data-testid="qty-column-field-option"' not in html
    assert 'data-testid="qty-filter-field-option"' not in html
    assert 'data-catalogue-url="' in html
    assert "/field-catalogue/" in html
    # Two shells, one catalogue endpoint — no duplicated tree markup.
    assert html.count('data-testid="qty-column-field"') == 1
    assert html.count('data-testid="qty-filter-field"') == 1
    assert "Loading IFC fields…" in html
    assert "Open to load indexed fields for this model." in html
    # Idle and loading chrome coexist in markup; JS keeps them exclusive at runtime.
    assert 'data-testid="qty-filter-field-loading"' in html
    assert 'data-testid="qty-column-field-loading"' in html
    assert 'data-testid="qty-filter-field-placeholder"' in html
    assert 'data-testid="qty-column-field-placeholder"' in html
    # Add Column shell must not inherit a selected_key from Filter context.
    assert 'id="qty-column-field"' in html
    col_idx = html.find('id="qty-column-field"')
    col_snip = html[col_idx : col_idx + 500]
    assert 'data-selected-key=""' in col_snip or "data-selected-key=''" in col_snip
    assert 'data-catalogue-mode="column"' in col_snip


@pytest.mark.django_db
def test_lazy_catalogue_endpoint_returns_grouped_options(client):
    project, _ifc = _project_with_props()
    client.force_login(project.owner)
    resp = client.get(
        reverse("takeoff:qty_field_catalogue", kwargs={"pk": project.pk}),
        {"mode": "filter", "picker_id": "qty-filter-field"},
    )
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'data-testid="qty-filter-field-option"' in html
    assert "Element properties" in html or "Quantities" in html
    assert "prop:Identity Data.Keynote" in html or "Keynote" in html


@pytest.mark.django_db
def test_lazy_catalogue_column_mode_marks_and_includes_table_fields(client):
    project, _ifc = _project_with_props()
    client.force_login(project.owner)
    resp = client.get(
        reverse("takeoff:qty_field_catalogue", kwargs={"pk": project.pk}),
        {
            "mode": "column",
            "picker_id": "qty-column-field",
            "mark_added": "ifc_class,name,status,actions",
        },
    )
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'data-testid="qty-column-field-option"' in html
    assert "Table fields" in html or "quantity" in html.lower()


@pytest.mark.django_db
def test_lazy_catalogue_rejects_foreign_ifc(client):
    project, _ifc = _project_with_props()
    other = ProjectFactory()
    foreign = IFCFileFactory(project=other, status="completed")
    client.force_login(project.owner)
    resp = client.get(
        reverse("takeoff:qty_field_catalogue", kwargs={"pk": project.pk}),
        {"mode": "filter", "ifc_file_id": str(foreign.pk)},
    )
    assert resp.status_code == 403


@pytest.mark.django_db
def test_lazy_catalogue_requires_login(client):
    project, _ifc = _project_with_props()
    resp = client.get(
        reverse("takeoff:qty_field_catalogue", kwargs={"pk": project.pk}),
        {"mode": "filter"},
    )
    assert resp.status_code in {302, 401, 403}


@pytest.mark.django_db
def test_lazy_catalogue_rejects_other_project_user(client):
    project, _ifc = _project_with_props()
    stranger = UserFactory()
    client.force_login(stranger)
    resp = client.get(
        reverse("takeoff:qty_field_catalogue", kwargs={"pk": project.pk}),
        {"mode": "filter"},
    )
    assert resp.status_code in {302, 403, 404}


@pytest.mark.django_db
def test_saved_semantic_column_heading_without_eager_tree(client):
    project, _ifc = _project_with_props()
    client.force_login(project.owner)
    col = "prop:Identity Data.Keynote"
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "table_layout": "v2",
            "col_order": f"ifc_class,name,{col},status,actions",
            "sem_cols": col,
        },
    ).content.decode()
    assert "Keynote" in html
    assert 'data-testid="qty-column-field-option"' not in html
