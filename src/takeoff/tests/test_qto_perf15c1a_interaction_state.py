# takeoff/tests/test_qto_perf15c1a_interaction_state.py
"""QTO-PERF-15C1A — lazy picker loading chrome + independent Filter/Add selection."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
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
            "Analytical Properties.Absorptance": 0.4,
            "Qto_BeamBaseQuantities.NetVolume": 2.5,
        },
    )
    return project, ifc


@pytest.mark.django_db
def test_shell_loading_copy_and_exclusive_chrome_markup(client):
    project, _ifc = _project_with_props()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert "Loading IFC fields…" in html
    assert "Open to load indexed fields for this model." in html
    assert 'data-testid="qty-filter-field-error"' in html
    assert 'data-testid="qty-column-field-error"' in html
    # Add starts disabled in markup.
    m = re.search(
        r'id="qty-property-add-submit"[^>]*>',
        html,
        flags=re.DOTALL,
    )
    assert m is not None
    assert "disabled" in m.group(0)


@pytest.mark.django_db
def test_column_shell_selected_key_empty_even_with_active_filter(client):
    project, _ifc = _project_with_props()
    client.force_login(project.owner)
    field = "prop:Analytical Properties.Absorptance"
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "table_layout": "v2",
            "semantic_field": field,
            "semantic_op": "eq",
            "semantic_value": "0.4",
            "semantic_value_type": "numeric",
        },
    ).content.decode()
    # Filter shell may carry the active key for restore.
    filt = html[html.find('id="qty-filter-field"') : html.find('id="qty-filter-field"') + 600]
    assert field in filt or "data-selected-key=" in filt
    # Column shell must stay empty — independent of Filter.
    col = html[html.find('id="qty-column-field"') : html.find('id="qty-column-field"') + 600]
    assert 'data-selected-key=""' in col
    assert 'data-catalogue-mode="column"' in col
    assert 'data-catalogue-mode="filter"' in filt


@pytest.mark.django_db
def test_filter_and_column_catalogue_are_mode_specific(client):
    """Report: Filter and Add use separate mode= requests, not one shared payload."""
    project, _ifc = _project_with_props()
    client.force_login(project.owner)
    url = reverse("takeoff:qty_field_catalogue", kwargs={"pk": project.pk})
    filt = client.get(url, {"mode": "filter", "picker_id": "qty-filter-field"})
    col = client.get(
        url,
        {
            "mode": "column",
            "picker_id": "qty-column-field",
            "mark_added": "ifc_class,name,status,actions",
        },
    )
    assert filt.status_code == 200 and col.status_code == 200
    fhtml, chtml = filt.content.decode(), col.content.decode()
    assert 'data-testid="qty-filter-field-option"' in fhtml
    assert 'data-testid="qty-column-field-option"' in chtml
    assert 'data-testid="qty-filter-field-option"' not in chtml
    assert 'data-testid="qty-column-field-option"' not in fhtml
    # Column catalogue includes table fields; filter does not.
    assert "Table fields" in chtml or "quantity" in chtml.lower()


@pytest.mark.django_db
def test_column_catalogue_never_preselects_filter_field(client):
    project, _ifc = _project_with_props()
    client.force_login(project.owner)
    field = "prop:Analytical Properties.Absorptance"
    resp = client.get(
        reverse("takeoff:qty_field_catalogue", kwargs={"pk": project.pk}),
        {
            "mode": "column",
            "picker_id": "qty-column-field",
            "selected_key": field,
            "mark_added": "ifc_class,name,status,actions",
        },
    )
    html = resp.content.decode()
    assert 'data-testid="qty-column-field-option"' in html
    assert field in html
    # Server ignores selected_key for column mode — no aria-pressed inheritance.
    assert html.count('aria-pressed="true"') == 0
    assert 'aria-pressed="false"' in html
