# takeoff/tests/test_ifc_semantic_fields_sem1.py
"""IFC-SEM-1 — semantic field discovery, enrichment, and prep filters."""

from __future__ import annotations

import pytest

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.ifc_semantic_fields import (
    discover_semantic_fields,
    enrich_prep_rows_with_entity_semantics,
    filter_prep_rows_by_semantic,
    sort_prep_rows_by_semantic,
)
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_preparation_ui import build_preparation_ui


def _project_with_semantics():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W1",
        properties={
            "Qto_WallBaseQuantities.NetVolume": 2.0,
            "Other.Category": "Walls",
            "Other.Family": "Basic Wall",
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W2",
        properties={
            "Qto_WallBaseQuantities.NetVolume": 3.0,
            "Other.Category": "Walls",
            "Other.Family": "Basic Wall",
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B1",
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 0.5,
            "Other.Category": "Structural Framing",
            "Other.Family": "SI-Beam",
        },
    )
    return project


@pytest.mark.django_db
def test_discover_returns_prep_native_fields():
    """Discovery always includes prep-native IFC class when rows exist."""
    project = _project_with_semantics()
    quantities = ModelQuantitiesService(project).build()
    qty_prep = build_preparation_ui(quantities)
    rows = qty_prep["prep_rows"]
    enrichment = enrich_prep_rows_with_entity_semantics(project, rows)
    discovered = discover_semantic_fields(project, rows, enrichment_meta=enrichment)
    keys = {f["key"] for f in discovered["fields"]}
    assert "ifc_class" in keys
    assert "quantity_basis" in keys
    assert all(len(f.get("sample_values") or []) <= 20 for f in discovered["fields"])
    # SEM-2: indexed Other.* / Pset keys surface as available property columns.
    assert discovered["property_sets_available_on_prep"] is True
    assert any(
        str(c.get("key", "")).startswith("prop:")
        for c in (discovered.get("property_columns_available") or [])
    )


@pytest.mark.django_db
def test_enrichment_attaches_category_family_without_inventing_psets():
    """Category/Family majority values attach; arbitrary psets are not invented."""
    project = _project_with_semantics()
    quantities = ModelQuantitiesService(project).build()
    rows = list(build_preparation_ui(quantities)["prep_rows"])
    meta = enrich_prep_rows_with_entity_semantics(project, rows)
    assert meta["enriched"] is True
    cats = {str(r.get("semantic_category") or "") for r in rows}
    assert "Walls" in cats or "Structural Framing" in cats
    discovered = discover_semantic_fields(project, rows, enrichment_meta=meta)
    keys = {f["key"] for f in discovered["fields"]}
    assert "semantic_category" in keys
    assert "pset:Fake.Prop" not in keys


@pytest.mark.django_db
def test_filter_by_ifc_class_and_category():
    """Semantic equality filter narrows prep rows."""
    project = _project_with_semantics()
    quantities = ModelQuantitiesService(project).build()
    rows = list(build_preparation_ui(quantities)["prep_rows"])
    enrich_prep_rows_with_entity_semantics(project, rows)
    walls = filter_prep_rows_by_semantic(rows, field_key="ifc_class", value="IfcWall")
    assert walls
    assert all(r["ifc_class"] == "IfcWall" for r in walls)
    if any(r.get("semantic_category") == "Walls" for r in rows):
        by_cat = filter_prep_rows_by_semantic(rows, field_key="semantic_category", value="Walls")
        assert by_cat
        assert all(r.get("semantic_category") == "Walls" for r in by_cat)


@pytest.mark.django_db
def test_sort_by_element_count():
    """Element count sort is stable and descending when requested."""
    rows = [
        {"ifc_class": "A", "element_count": 2},
        {"ifc_class": "B", "element_count": 9},
        {"ifc_class": "C", "element_count": 1},
    ]
    desc = sort_prep_rows_by_semantic(rows, sort_key="element_count", direction="desc")
    assert [r["element_count"] for r in desc] == [9, 2, 1]


@pytest.mark.django_db
def test_empty_project_safe():
    """Empty project returns empty discovery without raising."""
    project = ProjectFactory()
    discovered = discover_semantic_fields(project, [])
    assert discovered["fields"] == [] or discovered["prep_row_total"] == 0
    meta = enrich_prep_rows_with_entity_semantics(project, [])
    assert meta["enriched"] is False


@pytest.mark.django_db
def test_runtime_applies_semantic_filter_query(client):
    """build_qty_prep_session_ui honors semantic_field/value query params."""
    project = _project_with_semantics()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session={},
        query={
            "semantic_field": "ifc_class",
            "semantic_value": "IfcWall",
            "source_classification_code": "manual_field",
        },
    )
    qty_prep = runtime["qty_prep"]
    assert qty_prep["semantic_filter_active"] is True
    assert qty_prep["prep_rows"]
    assert all(r["ifc_class"] == "IfcWall" for r in qty_prep["prep_rows"])
    panel = qty_prep["semantic_filters"]
    assert panel["filter_active"] is True
    assert panel["showing_count"] <= panel["total_count"]


@pytest.mark.django_db
def test_no_db_writes_from_discovery():
    """Discovery/enrichment only issues reads (no INSERT/UPDATE)."""
    project = _project_with_semantics()
    quantities = ModelQuantitiesService(project).build()
    rows = list(build_preparation_ui(quantities)["prep_rows"])
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as ctx:
        enrich_prep_rows_with_entity_semantics(project, rows)
        discover_semantic_fields(project, rows)
    for q in ctx.captured_queries:
        sql = q["sql"].lstrip().upper()
        assert not sql.startswith("INSERT")
        assert not sql.startswith("UPDATE")
        assert not sql.startswith("DELETE")


@pytest.mark.django_db
def test_quantities_page_shows_semantic_filters_panel(client):
    """Quantities HTML includes IFC semantic filters panel."""
    project = _project_with_semantics()
    client.force_login(project.owner)
    from django.urls import reverse

    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    response = client.get(
        url,
        {
            "source_classification_code": "manual_field",
            "source_package_boq_mapping": "manual_field",
            "source_work_package": "manual_field",
        },
    )
    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert 'data-testid="qty-semantic-filters"' in html
    assert "IFC semantic filters" in html
    assert "BOQ-ready" not in html
