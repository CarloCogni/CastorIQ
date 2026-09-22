# takeoff/tests/test_demo_polish17c_presentation.py
"""DEMO-POLISH-17C — Quantities presentation/toolbar cleanup."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


def _project_with_beams(n: int = 3):
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="demo17c.ifc")
    for i in range(n):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcBeam",
            global_id=f"B{i}",
            properties={"Qto_BeamBaseQuantities.NetVolume": 1.0 + i},
        )
    return project


def _manual_params() -> dict:
    return {
        "include_classification": "1",
        "include_package": "1",
        "include_work_package": "1",
    }


@pytest.mark.django_db
def test_demo17c_toolbar_hierarchy_zero_selection(client):
    """Table / file groups visible; selection actions hidden; More holds version/export."""
    project = _project_with_beams()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()

    assert "IFC Quantity Preparation" in html
    assert 'data-testid="qty-toolbar-table-actions"' in html
    assert 'data-testid="qty-hierarchy-expand-classes"' in html
    assert 'data-testid="qty-columns-open"' in html
    assert 'data-testid="qty-measurement-settings-open"' in html

    sel = html.split('data-testid="qty-toolbar-selection-actions"', 1)[1][:1800]
    assert "d-none" in sel or "hidden" in sel
    assert "Apply measurement" in sel
    assert "Assign metadata" in sel
    assert "btn-primary" not in sel

    file_grp = html.split('data-testid="qty-toolbar-file-actions"', 1)[1][:2000]
    assert 'data-testid="qty-save-table-open"' in file_grp
    save_btn = file_grp.split('data-testid="qty-save-table-open"', 1)[0][-160:]
    assert "btn-primary" in save_btn
    assert 'data-testid="qty-open-table-open"' in file_grp
    assert 'data-testid="qty-toolbar-more"' in file_grp
    assert 'data-testid="qty-save-version-open"' in file_grp
    assert 'data-testid="qty-prep-export"' in file_grp
    # Version/export live under More, not as equal-weight primary buttons.
    assert "dropdown-item" in file_grp
    assert "dropdown-toggle" in file_grp


@pytest.mark.django_db
def test_demo17c_zone_notice_under_advanced_not_primary_filter(client):
    """Zone unavailable truth lives under Advanced; not persistent above the table."""
    project = _project_with_beams()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()

    assert 'data-testid="qty-zone-unavailable"' in html
    zone_idx = html.find('data-testid="qty-zone-unavailable"')
    adv_idx = html.find('data-testid="quantities-optional-estimate"')
    filter_idx = html.find('data-testid="qty-table-filter-bar"')
    assert adv_idx >= 0 and zone_idx > adv_idx
    # Persistent zone helper is not in the primary filter bar block.
    filter_block = html[filter_idx:adv_idx] if filter_idx >= 0 else ""
    assert 'data-testid="qty-zone-unavailable"' not in filter_block
    assert 'data-testid="qty-zone-unavailable-contextual"' in html


@pytest.mark.django_db
def test_demo17c_measurement_modal_default_vs_how_units(client):
    """Default modal shows short helper; caveats under How units work disclosure."""
    project = _project_with_beams()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()
    modal = html.split('data-testid="qty-measurement-settings-modal"', 1)[1]
    assert "Settings are per IFC class" in modal
    assert 'data-testid="qty-measurement-how-units-work"' in modal
    how = modal.split('data-testid="qty-measurement-how-units-work"', 1)[1][:2500]
    assert 'data-testid="qty-measurement-settings-gap"' in how
    assert 'data-testid="qty-measurement-settings-precedence"' in how
    assert 'data-testid="qty-measurement-class-apply"' in modal
    assert 'data-testid="qty-measurement-class-name"' in modal
    assert 'data-testid="qty-measurement-class-source-unit"' in modal
    assert 'data-testid="qty-measurement-class-output-unit"' in modal or (
        'data-testid="qty-measurement-class-output-unit-locked"' in modal
    )


@pytest.mark.django_db
def test_demo17c_boundary_copy_rejects_cost_claims(client):
    """Advanced boundary copy remains non-commercial / non-cost."""
    project = _project_with_beams()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()
    boundary = html.split('data-testid="quantities-boundary-copy"', 1)[1][:500]
    assert "Not BOQ" in boundary or "not BOQ" in boundary
    assert "not cost" in boundary.lower()
    assert "rates/pricing" in boundary.lower() or "not rates" in boundary.lower()
    assert "Quantity → Cost → Schedule" in boundary or "Quantity &rarr; Cost" in boundary
