# takeoff/tests/test_ifc_semantic_fields_sem4a.py
"""SEM-4A — IFC classification reference filters on Quantities."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.services.classification_ref_index import (
    KEY_DISPLAY,
    KEY_IDENTIFICATION,
    KEY_SOURCE,
    KEY_SYSTEM,
)
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.ifc_semantic_fields import (
    enrich_prep_rows_with_entity_semantics,
    filter_prep_rows_by_semantic,
    parse_sem_cols,
)
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_preparation_ui import build_preparation_ui


def _project_with_classref():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    for i, gid in enumerate(("B1", "B2", "B3")):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcBeam",
            global_id=gid,
            properties={
                "Qto_BeamBaseQuantities.NetVolume": 2.0 + i,
                "Identity Data.OmniClass Title": "Beams",
                "Identity Data.Assembly Code": "B.10",
                "Other.Category": "Structural Framing",
                "Other.Family": "Family",
                KEY_SYSTEM: "Uniformat",
                KEY_IDENTIFICATION: "B10",
                KEY_DISPLAY: "Uniformat / B10",
                KEY_SOURCE: "IfcRelAssociatesClassification",
            },
        )
    return project


@pytest.mark.django_db
def test_parse_sem_cols_accepts_classref_keys():
    """classref:* keys are accepted beside prop and spatial keys."""
    keys = parse_sem_cols({"sem_cols": "classref:ifc,prop:Identity Data.OmniClass Title"})
    assert "classref:ifc" in keys
    assert "prop:Identity Data.OmniClass Title" in keys


@pytest.mark.django_db
def test_classref_field_appears_when_display_exists():
    """classref:ifc appears in discovery when ClassRef.Display is indexed."""
    project = _project_with_classref()
    meta = enrich_prep_rows_with_entity_semantics(
        project,
        [{"ifc_class": "IfcBeam", "type_name": ""}],
        selected_prop_columns=[],
    )
    keys = {d["key"] for d in meta["classref_available"]}
    assert "classref:ifc" in keys
    assert "ifc_classification_ref" not in meta["unavailable"]


@pytest.mark.django_db
def test_classref_unavailable_when_missing():
    """Unavailable helper appears when no ClassRef.Display exists."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B-NO",
        properties={"Qto_BeamBaseQuantities.NetVolume": 1.0},
    )
    meta = enrich_prep_rows_with_entity_semantics(
        project,
        [{"ifc_class": "IfcBeam", "type_name": ""}],
        selected_prop_columns=[],
    )
    assert meta["classref_available"] == []
    assert meta["unavailable"]["ifc_classification_ref"]["available"] is False


@pytest.mark.django_db
def test_filter_by_uniformat_b10():
    """Filtering by Uniformat / B10 works on classref:ifc column."""
    project = _project_with_classref()
    quantities = ModelQuantitiesService(project).build()
    rows = list(build_preparation_ui(quantities)["prep_rows"])
    col = "classref:ifc"
    enrich_prep_rows_with_entity_semantics(project, rows, selected_prop_columns=[col])
    filtered = filter_prep_rows_by_semantic(
        rows,
        field_key=col,
        value="Uniformat / B10",
        allowed_extra_keys=[col],
    )
    assert filtered
    assert all(r.get(col) == "Uniformat / B10" or r.get(col) == "Mixed values" for r in filtered)


@pytest.mark.django_db
def test_omniclass_filters_remain_independent():
    """OmniClass property filters still work beside ClassRef columns."""
    project = _project_with_classref()
    quantities = ModelQuantitiesService(project).build()
    rows = list(build_preparation_ui(quantities)["prep_rows"])
    cols = ["classref:ifc", "prop:Identity Data.OmniClass Title"]
    enrich_prep_rows_with_entity_semantics(project, rows, selected_prop_columns=cols)
    filtered = filter_prep_rows_by_semantic(
        rows,
        field_key="prop:Identity Data.OmniClass Title",
        value="Beams",
        allowed_extra_keys=cols,
    )
    assert filtered
    assert all(
        r.get("prop:Identity Data.OmniClass Title") in {"Beams", "Mixed values"} for r in filtered
    )


@pytest.mark.django_db
def test_no_zone_keys_created_on_enrich():
    """SEM-4A enrichment never invents Zone.* property keys on entities."""
    project = _project_with_classref()
    from ifc_processor.models import IFCEntity

    before = list(
        IFCEntity.objects.filter(ifc_file__project=project).values_list("pk", "properties")
    )
    enrich_prep_rows_with_entity_semantics(
        project,
        [{"ifc_class": "IfcBeam", "type_name": ""}],
        selected_prop_columns=["classref:ifc"],
    )
    after = list(
        IFCEntity.objects.filter(ifc_file__project=project).values_list("pk", "properties")
    )
    assert before == after
    for _, props in after:
        assert not any(str(k).startswith("Zone.") for k in (props or {}))


@pytest.mark.django_db
def test_quantities_ui_shows_classref_section(client):
    """Quantities page exposes Existing IFC classification section when indexed."""
    project = _project_with_classref()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(
        url,
        {
            "basis_IfcBeam": "NetVolume",
            "source_classification_code": "manual_field",
            "source_package_boq_mapping": "manual_field",
            "source_work_package": "manual_field",
            "sem_cols": "classref:ifc",
            "semantic_field": "classref:ifc",
            "semantic_value": "Uniformat / B10",
        },
    ).content.decode()
    assert 'data-testid="qty-existing-ifc-classification"' in html
    assert "IFC Classification Reference" in html
    assert 'data-testid="qty-existing-classification"' in html
    assert "Authoring classification properties" in html
    assert "Castor schema mapping" in html
    assert 'data-testid="qty-batch-map-selected"' in html
    assert "Uniformat / B10" in html
