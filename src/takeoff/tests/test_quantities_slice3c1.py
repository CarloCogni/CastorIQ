# takeoff/tests/test_quantities_slice3c1.py
"""Quantities Slice 3c-1 — schema include/exclude via GET field_* params."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_preparation_ui import (
    build_preparation_ui,
    default_schema_includes,
    parse_schema_includes_from_query,
)


def _pilot_like_project():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W-3C1",
        properties={
            "Qto_WallBaseQuantities.NetVolume": 2.0,
            "Qto_WallBaseQuantities.NetArea": 12.5,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B-3C1",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.4},
    )
    return project


@pytest.mark.django_db
def test_default_schema_includes_match_baseline():
    """No field_* params: classification/package/work/type included; zone/level excluded."""
    includes = default_schema_includes()
    assert includes["classification_code"] is True
    assert includes["package_boq_mapping"] is True
    assert includes["work_package"] is True
    assert includes["type_name"] is True
    assert includes["zone"] is False
    assert includes["level_storey"] is False
    assert includes["ifc_class"] is True
    assert includes["review_status"] is True

    project = _pilot_like_project()
    ui = build_preparation_ui(ModelQuantitiesService(project).build())
    assert ui["show"]["classification_code"] is True
    assert ui["show"]["zone"] is False
    assert ui["unresolved_register"]["missing_classification"] >= 1
    assert ui["unresolved_register"]["missing_package_boq_mapping"] >= 1


@pytest.mark.django_db
def test_locked_core_fields_cannot_be_excluded_via_query():
    """URL attempts to exclude locked fields are ignored."""
    includes = parse_schema_includes_from_query(
        {
            "field_ifc_class": "0",
            "field_quantity_basis": "0",
            "field_total_quantity": "0",
            "field_review_status": "0",
            "field_classification_code": "0",
        }
    )
    assert includes["ifc_class"] is True
    assert includes["quantity_basis"] is True
    assert includes["total_quantity"] is True
    assert includes["review_status"] is True
    assert includes["classification_code"] is False


@pytest.mark.django_db
def test_exclude_classification_hides_column_and_register_count(client):
    """Excluding classification removes column and missing classification count."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(
        url,
        {"field_classification_code": "0"},
    ).content.decode()

    assert (
        "Classification Code"
        not in html.split('data-testid="qty-prep-table"', 1)[1].split("</table>", 1)[0]
    )
    assert 'data-testid="qty-reg-missing-classification"' not in html
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        schema_includes=parse_schema_includes_from_query({"field_classification_code": "0"}),
    )
    assert ui["unresolved_register"]["missing_classification"] == 0
    assert ui["show"]["classification_code"] is False
    viz_labels = [i["label"] for i in ui["visual_summary"]["unresolved_by_field"]]
    assert "Classification" not in viz_labels
    mapping = next(c for c in ui["preparation_insights"] if c["id"] == "mapping_gaps")
    assert mapping["count"] == (
        ui["unresolved_register"]["missing_package_boq_mapping"]
        + ui["unresolved_register"]["missing_work_package"]
    )
    assert "Classification" not in mapping["body"]


@pytest.mark.django_db
def test_exclude_package_and_work_package(client):
    """Excluding package/work package removes register cards and counts."""
    project = _pilot_like_project()
    includes = parse_schema_includes_from_query(
        {
            "field_package_boq_mapping": "0",
            "field_work_package": "0",
        }
    )
    ui = build_preparation_ui(ModelQuantitiesService(project).build(), schema_includes=includes)
    assert ui["unresolved_register"]["missing_package_boq_mapping"] == 0
    assert ui["unresolved_register"]["missing_work_package"] == 0
    assert ui["unresolved_register"]["missing_classification"] >= 1

    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"field_package_boq_mapping": "0", "field_work_package": "0"},
    ).content.decode()
    assert 'data-testid="qty-reg-missing-package"' not in html
    assert 'data-testid="qty-reg-missing-work-package"' not in html
    prep = html.split('data-testid="qty-prep-table"', 1)[1].split("</table>", 1)[0]
    assert "Package / BOQ Mapping" not in prep
    assert "Work Package" not in prep


@pytest.mark.django_db
def test_basis_params_compose_with_schema_params(client):
    """basis_* and field_* compose; Wall NetArea still works when classification excluded."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "basis_IfcWall": "NetArea",
            "field_classification_code": "0",
        },
    ).content.decode()
    assert "basis_IfcWall=NetArea" in html or "NetArea" in html
    wall_attr = html.index('data-qty-ifc-class="IfcWall"')
    wall_tr = html[html.rfind("<tr", 0, wall_attr) : html.index("</tr>", wall_attr)]
    assert 'data-qty-basis-unresolved="0"' in wall_tr
    assert "NetArea" in wall_tr
    assert 'data-testid="qty-reg-missing-classification"' not in html


@pytest.mark.django_db
def test_schema_builder_controls_and_source_mapping_interactive(client):
    """Editable includes appear; locked fields locked; source selects for 3c-2a fields."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert 'data-testid="qty-schema-include-classification_code"' in html
    assert 'data-testid="qty-schema-locked-ifc_class"' in html
    assert 'data-testid="qty-schema-session-note"' in html
    assert (
        "session-only until saved" in html.lower()
        or "preparation configuration draft" in html.lower()
    )
    assert "configuration is session-only and not saved" not in html.lower()
    assert 'data-testid="qty-source-select-classification_code"' in html
    assert 'data-testid="qty-source-select-package_boq_mapping"' in html
    assert 'data-testid="qty-source-select-work_package"' in html
    assert 'data-testid="qty-source-select-level_storey"' not in html
    assert 'data-testid="qty-source-locked-ifc_class"' in html
    assert 'data-testid="qty-prep-config-form"' in html
    assert "Source Mapping remains read-only" not in html
    page = html.split('data-testid="quantities-page"', 1)[1].split(
        'data-testid="quantities-not-claims"', 1
    )[0]
    for phrase in (
        "With IFC Qto",
        "Quantity Coverage",
        "recommended basis",
        "Castor recommends",
        "Save configuration",
    ):
        assert phrase not in page, phrase
    assert "auto-selected" not in page.lower()
    handoff = html.split('data-testid="qty-send-unresolved-to-modify"', 1)[0]
    assert "disabled" in handoff[handoff.rfind("<button") :]


@pytest.mark.django_db
def test_invalid_schema_param_ignored():
    """Invalid field values do not crash; defaults apply for that key."""
    includes = parse_schema_includes_from_query({"field_classification_code": "maybe"})
    assert includes["classification_code"] is True
    includes2 = parse_schema_includes_from_query({"field_not_a_real_field": "0"})
    assert "not_a_real_field" not in includes2
