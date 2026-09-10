# takeoff/tests/test_quantities_slice3c2a.py
"""Quantities Slice 3c-2a — source-type mapping for classification/package/work."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_preparation_ui import (
    build_preparation_ui,
    default_source_mapping_intents,
    parse_schema_includes_from_query,
    parse_source_mappings_from_query,
)


def _pilot_like_project():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W-3C2A",
        properties={
            "Qto_WallBaseQuantities.NetVolume": 2.0,
            "Qto_WallBaseQuantities.NetArea": 12.5,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B-3C2A",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.4},
    )
    return project


@pytest.mark.django_db
def test_default_source_intents_preserve_baseline_gaps():
    """No query: future_modify_handoff defaults; classification/package/work still gap."""
    intents = default_source_mapping_intents()
    assert intents["classification_code"] == "future_modify_handoff"
    assert intents["package_boq_mapping"] == "future_modify_handoff"
    assert intents["work_package"] == "future_modify_handoff"

    project = _pilot_like_project()
    ui = build_preparation_ui(ModelQuantitiesService(project).build())
    assert ui["source_mapping_intents"]["classification_code"] == "future_modify_handoff"
    assert ui["unresolved_register"]["missing_classification"] >= 1
    assert ui["unresolved_register"]["missing_package_boq_mapping"] >= 1
    assert ui["unresolved_register"]["missing_work_package"] >= 1
    assert all(r.get("classification_code") == "" for r in ui["prep_rows"])
    assert all(r.get("classification_hint") == "Deferred to Modify" for r in ui["prep_rows"])


@pytest.mark.django_db
def test_source_selects_only_for_editable_mapping_fields(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert 'data-testid="qty-source-select-classification_code"' in html
    assert 'data-testid="qty-source-select-package_boq_mapping"' in html
    assert 'data-testid="qty-source-select-work_package"' in html
    assert 'data-testid="qty-source-select-type_name"' not in html
    assert 'data-testid="qty-source-select-level_storey"' not in html
    assert 'data-testid="qty-source-select-zone"' not in html
    assert 'data-testid="qty-source-locked-ifc_class"' in html
    assert 'data-testid="qty-source-locked-quantity_basis"' in html
    assert (
        'type="text"'
        not in html.split('data-testid="quantities-source-mapping"', 1)[1].split("</section>", 1)[0]
    )
    assert 'data-testid="qty-property-picker"' not in html
    assert "Save configuration" not in html
    assert "Source Mapping remains read-only" not in html


@pytest.mark.django_db
def test_not_mapped_does_not_count_as_missing(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(url, {"source_classification_code": "not_mapped"}).content.decode()
    assert 'data-testid="qty-reg-missing-classification"' not in html
    assert "Not mapped by session config" in html
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings=parse_source_mappings_from_query(
            {"source_classification_code": "not_mapped"}
        ),
    )
    assert ui["unresolved_register"]["missing_classification"] == 0
    assert ui["unresolved_register"]["missing_package_boq_mapping"] >= 1
    viz = str(ui["visual_summary"])
    assert "Classification" not in viz or ui["visual_summary"]["unresolved_by_field"]
    class_labels = [i["label"] for i in ui["visual_summary"]["unresolved_by_field"]]
    assert "Classification" not in class_labels


@pytest.mark.django_db
def test_manual_field_counts_as_unresolved_without_inventing_value():
    project = _pilot_like_project()
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings=parse_source_mappings_from_query(
            {"source_classification_code": "manual_field"}
        ),
    )
    assert ui["unresolved_register"]["missing_classification"] >= 1
    assert all(r.get("classification_code") == "" for r in ui["prep_rows"])
    assert all(r.get("classification_hint") == "Manual value not entered" for r in ui["prep_rows"])


@pytest.mark.django_db
def test_future_modify_handoff_counts_as_unresolved():
    project = _pilot_like_project()
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings=parse_source_mappings_from_query(
            {"source_package_boq_mapping": "future_modify_handoff"}
        ),
    )
    assert ui["unresolved_register"]["missing_package_boq_mapping"] >= 1


@pytest.mark.django_db
def test_excluded_field_ignores_source_param():
    project = _pilot_like_project()
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        schema_includes=parse_schema_includes_from_query({"field_classification_code": "0"}),
        source_mappings=parse_source_mappings_from_query(
            {"source_classification_code": "manual_field"}
        ),
    )
    assert ui["show"]["classification_code"] is False
    assert ui["unresolved_register"]["missing_classification"] == 0


@pytest.mark.django_db
def test_invalid_and_locked_source_params_ignored():
    intents = parse_source_mappings_from_query(
        {
            "source_classification_code": "ifc_property",
            "source_package_boq_mapping": "maybe",
            "source_ifc_class": "not_mapped",
            "source_work_package": "manual_field",
        }
    )
    assert intents["classification_code"] == "future_modify_handoff"
    assert intents["package_boq_mapping"] == "future_modify_handoff"
    assert intents["work_package"] == "manual_field"
    assert "ifc_class" not in intents


@pytest.mark.django_db
def test_source_schema_basis_compose(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(
        url,
        {
            "source_classification_code": "not_mapped",
            "field_work_package": "0",
            "basis_IfcWall": "NetArea",
        },
    ).content.decode()
    assert 'data-testid="qty-reg-missing-classification"' not in html
    assert 'data-testid="qty-reg-missing-work-package"' not in html
    wall_attr = html.index('data-qty-ifc-class="IfcWall"')
    wall_tr = html[html.rfind("<tr", 0, wall_attr) : html.index("</tr>", wall_attr)]
    assert 'data-qty-basis-unresolved="0"' in wall_tr
    assert "NetArea" in wall_tr


@pytest.mark.django_db
def test_insights_and_boundaries(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"source_classification_code": "not_mapped"},
    ).content.decode()
    assert "Raw Indexed Quantity Inventory" in html
    assert "reference" in html.lower()
    assert (
        "disabled"
        in html.split('data-testid="qty-send-unresolved-to-modify"', 1)[0][
            html.split('data-testid="qty-send-unresolved-to-modify"', 1)[0].rfind("<button") :
        ]
    )
    page = html.split('data-testid="quantities-page"', 1)[1].split(
        'data-testid="quantities-not-claims"', 1
    )[0]
    for phrase in (
        "recommended basis",
        "Castor recommends",
        "Save configuration",
        "Quantity Coverage",
        "Qto Coverage",
        "Model Quantity Readiness",
    ):
        assert phrase not in page, phrase
    assert "auto-selected" not in page.lower()
