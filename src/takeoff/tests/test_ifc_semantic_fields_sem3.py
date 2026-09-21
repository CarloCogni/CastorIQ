# takeoff/tests/test_ifc_semantic_fields_sem3.py
"""IFC-SEM-3 — Level/Storey spatial + classification-like discovery/filters."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)
from takeoff.services.ifc_semantic_fields import (
    SPATIAL_STOREY_KEY,
    enrich_prep_rows_with_entity_semantics,
    filter_prep_rows_by_semantic,
    parse_sem_cols,
)
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_preparation_ui import build_preparation_ui


def _project_with_structure():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    storey_ent = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBuildingStorey",
        global_id="STOREY-L07",
        name="L07",
        properties={},
    )
    storey = IFCSpatialElementFactory(
        ifc_file=ifc,
        entity=storey_ent,
        spatial_type="building_storey",
    )
    for i, (gid, level, omni) in enumerate(
        [
            ("B1", "L07", "Beams"),
            ("B2", "L07", "Beams"),
            ("B3", "L08", "Columns"),
        ]
    ):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcBeam" if omni == "Beams" else "IfcColumn",
            global_id=gid,
            spatial_container=storey,
            properties={
                "Qto_BeamBaseQuantities.NetVolume"
                if omni == "Beams"
                else "Qto_ColumnBaseQuantities.NetVolume": 2.0 + i,
                "Identity Data.Project Level": level,
                "Identity Data.OmniClass Title": omni,
                "Other.Category": "Structural Framing",
                "Other.Family": "Family",
            },
        )
    return project


@pytest.mark.django_db
def test_parse_sem_cols_accepts_spatial_keys():
    """SEM-3 spatial keys are accepted beside prop keys."""
    keys = parse_sem_cols({"sem_cols": f"{SPATIAL_STOREY_KEY},prop:Other.Category"})
    assert SPATIAL_STOREY_KEY in keys
    assert "prop:Other.Category" in keys


@pytest.mark.django_db
def test_discover_storey_and_project_level_and_unavailable_zone():
    """Structure discovery finds storey/project level; zone/classref unavailable."""
    project = _project_with_structure()
    rows = [{"ifc_class": "IfcBeam", "type_name": ""}]
    meta = enrich_prep_rows_with_entity_semantics(project, rows, selected_prop_columns=[])
    structure_keys = {d["key"] for d in meta["structure_columns_available"]}
    assert SPATIAL_STOREY_KEY in structure_keys
    assert "prop:Identity Data.Project Level" in structure_keys
    classlike_keys = {d["key"] for d in meta["classification_like_available"]}
    assert "prop:Identity Data.OmniClass Title" in classlike_keys
    assert meta["unavailable"]["zone"]["available"] is False
    assert meta["unavailable"]["ifc_classification_ref"]["available"] is False
    assert not any(d["key"] == "struct:zone" for d in meta["structure_columns_available"])
    zone_msg = meta["unavailable"]["zone"]["message"]
    assert "select it as the Zone source during preparation" in zone_msg
    assert "Ask" not in zone_msg
    assert "Modify" not in zone_msg
    assert "writeback" not in zone_msg.lower()


@pytest.mark.django_db
def test_enrich_project_level_column_and_filter():
    """Project Level column aggregates and filters prep rows."""
    project = _project_with_structure()
    quantities = ModelQuantitiesService(project).build()
    rows = list(build_preparation_ui(quantities)["prep_rows"])
    col = "prop:Identity Data.Project Level"
    enrich_prep_rows_with_entity_semantics(project, rows, selected_prop_columns=[col])
    filtered = filter_prep_rows_by_semantic(
        rows,
        field_key=col,
        value="L07",
        allowed_extra_keys=[col],
    )
    assert filtered
    assert all(r.get(col) == "L07" or r.get(col) == "Mixed values" for r in filtered)


@pytest.mark.django_db
def test_quantities_page_shows_structure_and_unavailable_helpers(client):
    """Quantities UI keeps zone truth; structure catalogue loads lazily (PERF-15C1)."""
    project = _project_with_structure()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(url, {"basis_IfcBeam": "NetVolume"}).content.decode()
    assert 'data-testid="qty-zone-unavailable"' in html
    assert 'data-catalogue-url="' in html
    assert "/field-catalogue/" in html
    assert 'data-testid="qty-table-filter-bar"' in html or 'data-testid="qty-filter-field"' in html

    cat = client.get(
        reverse("takeoff:qty_field_catalogue", kwargs={"pk": project.pk}),
        {"mode": "filter"},
    )
    assert cat.status_code == 200
    cat_html = cat.content.decode()
    assert "Level / Storey" in cat_html or "Project Level" in cat_html
    assert "Spatial" in cat_html or "Element properties" in cat_html


@pytest.mark.django_db
def test_structure_filter_keeps_sem2_and_batch_toolbar(client):
    """Adding Project Level column keeps Add-column path and batch toolbar."""
    project = _project_with_structure()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(
        url,
        {
            "basis_IfcBeam": "NetVolume",
            "basis_IfcColumn": "NetVolume",
            "source_classification_code": "manual_field",
            "source_package_boq_mapping": "manual_field",
            "source_work_package": "manual_field",
            "sem_cols": "prop:Identity Data.Project Level",
            "semantic_field": "prop:Identity Data.Project Level",
            "semantic_value": "L07",
        },
    ).content.decode()
    assert 'data-testid="qty-columns-modal"' in html or 'data-testid="qty-column-field"' in html
    assert 'data-testid="qty-batch-map-selected"' in html
    assert "L07" in html or "Project Level" in html


@pytest.mark.django_db
def test_discover_sem3_does_not_write_db():
    """Discovery path is read-only (no entity updates)."""
    project = _project_with_structure()
    from ifc_processor.models import IFCEntity

    before = list(
        IFCEntity.objects.filter(ifc_file__project=project).values_list("pk", "properties")
    )
    enrich_prep_rows_with_entity_semantics(
        project,
        [{"ifc_class": "IfcBeam", "type_name": ""}],
        selected_prop_columns=[SPATIAL_STOREY_KEY],
    )
    after = list(
        IFCEntity.objects.filter(ifc_file__project=project).values_list("pk", "properties")
    )
    assert before == after


@pytest.mark.django_db
def test_session_ui_includes_structure_panel_keys():
    """Runtime keeps zone unavailable; selected structure column restores without catalogue."""
    project = _project_with_structure()
    ui = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session={},
        query={
            "basis_IfcBeam": "NetVolume",
            "sem_cols": "prop:Identity Data.Project Level",
        },
    )
    sem = (ui.get("qty_prep") or {}).get("semantic_filters") or {}
    assert sem.get("field_catalogue_lazy") is True
    assert sem.get("unavailable", {}).get("zone", {}).get("available") is False
    meta = sem.get("selected_prop_column_meta") or []
    assert any(
        str(d.get("key")) == "prop:Identity Data.Project Level"
        or "Project Level" in str(d.get("label") or "")
        for d in meta
    )
