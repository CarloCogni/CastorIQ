# takeoff/tests/test_qto_review08b_picker_values.py
"""QTO-REVIEW-08B — picker search, hierarchy, field-scoped value suggestions."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
)
from takeoff.services.quantity_field_catalogue import (
    FAMILY_ELEMENT,
    FAMILY_QTO,
    FAMILY_TYPE,
    build_picker_hierarchy,
    classify_field_family,
)


def _project():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    et = IFCElementTypeFactory(ifc_file=ifc, name="Rib", ifc_type="IfcBeamType", global_id="ET1")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="G1",
        element_type=et,
        properties={
            "Identity Data.4D status": "Planned",
            "Identity Data.Keynote": "K-1",
            "Type.Identity Data.Keynote": "TK-1",
            "Qto_BeamBaseQuantities.NetVolume": 2.5,
            "Other.Category": "should-hide",
            "Other.Family": "should-hide",
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="G2",
        element_type=et,
        properties={
            "Identity Data.4D status": "Built",
            "Qto_BeamBaseQuantities.NetVolume": 1.0,
        },
    )
    return project


@pytest.mark.django_db
def test_hierarchy_separates_element_type_qto():
    fields = [
        {
            "key": "prop:Identity Data.4D status",
            "label": "4D status",
            "source_property": "Identity Data.4D status",
            "group": "Identity Data",
        },
        {
            "key": "prop:Type.Identity Data.Keynote",
            "label": "Keynote",
            "source_property": "Type.Identity Data.Keynote",
            "group": "Identity Data",
        },
        {
            "key": "prop:Qto_BeamBaseQuantities.NetVolume",
            "label": "NetVolume",
            "source_property": "Qto_BeamBaseQuantities.NetVolume",
            "group": "Quantity sets",
        },
    ]
    assert classify_field_family(fields[0])[0] == FAMILY_ELEMENT
    assert classify_field_family(fields[1])[0] == FAMILY_TYPE
    assert classify_field_family(fields[2])[0] == FAMILY_QTO
    hier = build_picker_hierarchy(fields)
    families = [h["family"] for h in hier]
    assert FAMILY_ELEMENT in families
    assert FAMILY_TYPE in families
    assert FAMILY_QTO in families


@pytest.mark.django_db
def test_columns_modal_search_empty_state_and_no_pooled_datalist(client):
    """Add-column picker is a lazy shell on the page; search/options come from the catalogue.

    PERF-15C1: the page must not inline the whole field catalogue, so the
    page carries the shell + catalogue URL and the search / option / empty
    markers live in the fragment served by ``takeoff:qty_field_catalogue``.
    """
    project = _project()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    # Simulate refresh after adding a column.
    html = client.get(
        url,
        {
            "table_layout": "v2",
            "col_order": "ifc_class,name,prop:Identity Data.Keynote,status,actions",
        },
    ).content.decode()
    catalogue_url = reverse("takeoff:qty_field_catalogue", kwargs={"pk": project.pk})

    # Lazy shell only — no inlined option list on first paint.
    assert 'data-testid="qty-column-field"' in html
    assert 'data-testid="qty-column-field-placeholder"' in html
    assert 'data-testid="qty-column-field-loading"' in html
    assert 'data-catalogue-state="idle"' in html
    assert f'data-catalogue-url="{catalogue_url}"' in html
    assert 'data-no-matches-testid="qty-column-field-empty"' in html
    assert 'data-option-testid="qty-column-field-option"' in html
    assert 'data-testid="qty-column-field-option"' not in html
    assert 'data-testid="qty-filter-focus"' not in html
    # No pooled datalist of all fields
    assert "<datalist" not in html
    assert 'data-testid="qty-semantic-value-suggestions"' in html
    # REVIEW-08C: suggestions load from field-values endpoint, not pooled samples JSON
    assert "/field-values/" in html
    assert 'id="qty-field-samples-json"' not in html
    assert 'id="qty-field-meta-json"' in html
    m = re.search(
        r'<script type="application/json" id="qty-field-meta-json">(.*?)</script>',
        html,
        re.S,
    )
    assert m
    meta = json.loads(m.group(1))
    assert isinstance(meta, dict)
    # First paint bootstraps only the stable native fields; discovered IFC
    # property keys arrive with the lazy catalogue.
    assert "ifc_quantity_source" in meta
    status_key = "prop:Identity Data.4D status"
    assert status_key not in meta

    # The catalogue fragment carries the search box, options and hidden empty state.
    fragment = client.get(
        catalogue_url, {"mode": "column", "picker_id": "qty-column-field"}
    ).content.decode()
    lazy = re.search(
        r'<script type="application/json" data-qty-field-meta-lazy="1">(.*?)</script>',
        fragment,
        re.S,
    )
    assert lazy
    assert status_key in json.loads(lazy.group(1))
    assert 'data-testid="qty-column-field-search"' in fragment
    assert 'data-testid="qty-column-field-option"' in fragment
    assert "Element properties" in fragment or "Quantity sets" in fragment
    assert 'data-testid="qty-column-field-empty"' in fragment
    # Empty message starts hidden (class precedes data-testid)
    assert re.search(
        r'class="[^"]*\bd-none\b[^"]*"[^>]*data-testid="qty-column-field-empty"',
        fragment,
    )
    assert "<datalist" not in fragment


@pytest.mark.django_db
def test_filter_op_and_value_survive_page_render(client):
    """Active filter widgets must hydrate from server state (not clear on picker init)."""
    project = _project()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "table_layout": "v2",
            "col_order": "ifc_class,name,status,actions",
            "semantic_field": "prop:Qto_BeamBaseQuantities.NetVolume",
            "semantic_op": "gt",
            "semantic_value": "0.5",
            "semantic_value_type": "numeric",
            "semantic_classes": "IfcBeam",
        },
    ).content.decode()
    assert 'value="0.5"' in html or "value='0.5'" in html
    assert re.search(
        r'<option[^>]*value="gt"[^>]*selected',
        html,
    )
    assert 'id="qty-semantic-field"' in html
    assert "prop:Qto_BeamBaseQuantities.NetVolume" in html
    # Picker init must not notify onSelect for restored key (regression guard in JS)
    picker_js = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "takeoff"
        / "components"
        / "quantities_field_picker_js.html"
    ).read_text(encoding="utf-8")
    assert "notify: false" in picker_js or "notify !== false" in picker_js


@pytest.mark.django_db
def test_toolbar_groups_and_measurement_class_primary(client):
    project = _project()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"table_layout": "v2", "col_order": "ifc_class,name,status,actions"},
    ).content.decode()
    assert 'data-testid="qty-toolbar-table-actions"' in html
    assert 'data-testid="qty-toolbar-file-actions"' in html
    assert "Measurement settings" in html
    assert 'data-testid="qty-measurement-settings-precedence"' in html
    assert 'data-testid="qty-measurement-settings-units-block"' not in html
