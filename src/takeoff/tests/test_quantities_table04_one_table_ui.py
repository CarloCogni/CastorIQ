# takeoff/tests/test_quantities_table04_one_table_ui.py
"""R5D-QTO-TABLE-04 — minimal one-table Quantities product UI."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


def _manual_params() -> dict[str, str]:
    return {
        "source_classification_code": "manual_field",
        "source_package_boq_mapping": "manual_field",
        "source_work_package": "manual_field",
    }


def _project_with_beams(n: int = 5):
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="table04.ifc")
    for i in range(n):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcBeam",
            global_id=f"GID-T04-{i}",
            name=f"BeamType-{i % 2}",
            properties={
                "Qto_BeamBaseQuantities.NetVolume": 1.0 + i,
                "Qto_BeamBaseQuantities.GrossVolume": 2.0 + i,
                "Qto_BeamBaseQuantities.Length": 1000.0 * (i + 1),
            },
        )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-T04-WALL",
        name="WallType-A",
        properties={"Qto_WallBaseQuantities.NetVolume": 3.0},
    )
    return project


@pytest.mark.django_db
def test_table04_default_opens_one_table_without_workflow_rail(client):
    """Primary screen is the quantity table; six-stage rail is gone."""
    project = _project_with_beams()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()

    assert 'data-testid="quantities-workspace-title"' in html
    assert ">Quantities<" in html or "Quantities\n" in html
    assert "table04.ifc" in html
    assert 'data-testid="qty-c5d-workflow-rail"' not in html
    assert "1</span> Discover" not in html and ">1</span> Discover" not in html
    assert "Filters &amp; evidence columns" not in html
    primary = html.split('data-testid="qty-prep-primary-toolbar"', 1)[1][:3500]
    assert "evidence columns" not in primary.lower()
    assert 'data-testid="qty-prep-primary-toolbar"' in html
    assert 'data-testid="qty-columns-open"' in html
    assert 'data-testid="qty-table-filter-bar"' in html
    assert 'data-testid="qty-filter-focus"' not in html
    assert 'data-testid="qty-batch-set-measurement"' in html
    assert 'data-testid="qty-batch-map-selected"' in html
    assert "Assign values" in html
    assert 'data-testid="qty-units-toolbar-link"' in html
    assert 'data-testid="qty-measurement-settings-open"' in html
    assert "Measurement settings" in html
    assert 'data-testid="qty-save-version-open"' in html
    assert "Save version" in html
    assert 'data-testid="qty-prep-export"' in html
    assert "Export table" in html
    assert "Export session" not in html
    assert "Map selected visible rows" not in html
    assert (
        'data-testid="qty-prep-col-ifc_class"' in html
        or 'data-testid="qty-prep-col-ifc-class"' in html
    )
    assert (
        'data-testid="qty-prep-col-name"' in html or 'data-testid="qty-prep-col-type-name"' in html
    )
    # Default REVIEW-08 layout omits measurement until added as optional column.
    # Legacy sticky layouts may still include it when opened with older query state.


@pytest.mark.django_db
def test_table04_no_filter_count_omits_filtered_and_clear(client):
    """No-filter hierarchy footnote shows element counts without filtered chip."""
    project = _project_with_beams(4)
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()
    footnote = html.split('data-testid="qty-prep-row-count-footnote"', 1)[1][:280]
    assert "filtered from" not in footnote.lower()
    assert re.search(r"\d+\s+elements?", footnote)
    assert "Class → Type → Instance" in footnote
    assert 'data-testid="qty-semantic-clear-filter"' not in html
    assert 'data-testid="qty-discover-active-filter"' not in html
    value_snip = html.split('data-testid="qty-semantic-value"', 1)[1][:120]
    assert "disabled" in value_snip
    apply_snip = html.split('data-testid="qty-semantic-apply-filter"', 1)[1][:120]
    assert "disabled" in apply_snip


@pytest.mark.django_db
def test_table04_active_filter_shows_of_total_chip_and_clear(client):
    """Active filter keeps unrestricted baseline + Clear; Category/Family excluded."""
    project = _project_with_beams(6)
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(
        url,
        {
            **_manual_params(),
            "semantic_field": "ifc_class",
            "semantic_value": "IfcBeam",
        },
    ).content.decode()
    footnote = html.split('data-testid="qty-prep-row-count-footnote"', 1)[1][:320]
    assert re.search(r"\d+\s+elements?", footnote)
    # 6 beams + 1 wall on the fixture IFC.
    assert "filtered from 7 unrestricted elements" in footnote
    assert 'data-testid="qty-discover-active-filter"' in html
    chip = html.split('data-testid="qty-discover-active-filter"', 1)[1][:180]
    assert "IFC Class" in chip
    assert "IfcBeam" in chip
    assert 'data-testid="qty-semantic-clear-filter"' in html
    assert ">Clear<" in html or "Clear\n" in html
    # Field picker is hierarchical; Category/Family stay excluded from options.
    assert 'data-testid="qty-filter-field"' in html
    assert (
        "Category"
        not in html.split('data-testid="qty-filter-field"', 1)[1][:4000].split(
            "qty-semantic-op", 1
        )[0]
        or "Other.Category" not in html
    )
    picker = html.split('data-testid="qty-filter-field"', 1)[1][:5000]
    assert "Category" not in picker or "prop:Other.Category" not in picker
    assert "Family" not in picker or "prop:Other.Family" not in picker
    assert "Element properties" in picker or "Quantities" in picker or "IFC Class" in html


@pytest.mark.django_db
def test_table04_columns_modal_opens_ifc_property_selection(client):
    """Columns control targets modal with IFC property groups — not evidence wording."""
    project = _project_with_beams()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()
    assert 'data-bs-target="#qtyColumnsModal"' in html
    assert 'data-testid="qty-columns-modal"' in html
    assert "IFC fields" in html or "IFC properties" in html
    assert "Element properties" in html or "Quantities" in html or "Spatial" in html
    assert "Add evidence columns" not in html


@pytest.mark.django_db
def test_table04_selection_scoped_actions_disabled_at_zero(client):
    """Set measurement / Assign values start disabled; hierarchy selection scope copy."""
    project = _project_with_beams()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()
    measure = html.split('data-testid="qty-batch-set-measurement"', 1)[1][:200]
    assign = html.split('data-testid="qty-batch-map-selected"', 1)[1][:200]
    assert "disabled" in measure
    assert "disabled" in assign
    assert "Select visible rows" in html
    assert 'data-testid="qty-selection-scope-note"' in html
    scope = html.split('data-testid="qty-selection-scope-note"', 1)[1][:220]
    assert "descendant" in scope.lower() or "Current page only" in scope
    assert 'data-testid="qty-batch-toolbar-count"' in html
    assert "0 selected" in html
    assert 'data-testid="qty-batch-clear-selection"' in html
    clear = html.split('data-testid="qty-batch-clear-selection"', 1)[0][-80:]
    assert "d-none" in clear or 'class="btn btn-sm btn-outline-secondary d-none"' in html


@pytest.mark.django_db
def test_table04_assign_values_wording_and_scope(client):
    """Assign values modal states session scope and no IFC writeback."""
    project = _project_with_beams()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()
    modal = html.split('data-testid="qty-batch-mapping-modal"', 1)[1][:2500]
    assert "Assign values" in modal
    assert "Does not write back to the IFC" in modal
    assert "current working session" in modal.lower() or "working session" in modal
    assert "selected rows" in modal.lower()
    assert "Batch schema mapping" not in modal


@pytest.mark.django_db
def test_table04_units_and_save_version_controls(client):
    """Units and Save version open product modals; freeze CTA uses Save version."""
    project = _project_with_beams()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()
    assert 'data-testid="qty-units-modal"' in html
    assert 'data-testid="qty-measurement-settings-modal"' in html
    assert 'data-testid="qty-measurement-class-apply"' in html
    assert 'data-testid="qty-measurement-settings-precedence"' in html
    assert 'data-testid="qty-save-version-modal"' in html
    assert 'data-testid="qty-freeze-cta"' in html
    freeze = html.split('data-testid="qty-freeze-cta"', 1)[1][:120]
    assert "Save version" in freeze
    assert "Freeze updated 5D snapshot" not in html.split("Advanced", 1)[0]


@pytest.mark.django_db
def test_table04_export_remains_non_busy_download(client):
    """Export table keeps castor-download opt-out and ZIP attachment contract."""
    project = _project_with_beams()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()
    snip = html[
        max(0, html.find('data-testid="qty-prep-export"') - 280) : html.find(
            'data-testid="qty-prep-export"'
        )
        + 160
    ]
    assert 'data-castor-download="1"' in snip
    assert "Export table" in snip
    resp = client.get(reverse("takeoff:qty_prep_export", kwargs={"pk": project.pk}))
    assert resp.status_code == 200
    assert "attachment" in (resp.get("Content-Disposition") or "").lower()
