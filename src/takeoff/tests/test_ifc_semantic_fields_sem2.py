# takeoff/tests/test_ifc_semantic_fields_sem2.py
"""IFC-SEM-2 — property column discovery, aggregation, and filtering."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.ifc_semantic_fields import (
    MIXED_VALUES_LABEL,
    aggregate_property_values,
    discover_indexed_property_columns,
    enrich_prep_rows_with_entity_semantics,
    filter_prep_rows_by_semantic,
    parse_sem_cols,
    prop_column_key,
)
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_preparation_ui import build_preparation_ui


def _project_with_props():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    for i, (gid, typ, fam, cat) in enumerate(
        [
            ("W1", "Basic Wall", "Basic Wall", "Walls"),
            ("W2", "Basic Wall", "Basic Wall", "Walls"),
            ("B1", "W12x26", "SI-Beam", "Structural Framing"),
        ]
    ):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcWall" if gid.startswith("W") else "IfcBeam",
            global_id=gid,
            properties={
                "Qto_WallBaseQuantities.NetVolume"
                if gid.startswith("W")
                else "Qto_BeamBaseQuantities.NetVolume": 1.0 + i,
                "Other.Category": cat,
                "Other.Family": fam,
                "Other.Type": typ,
                "Other.id": f"noise-{i}",
            },
        )
    # Extra walls to push coverage thresholds if needed in small fixtures —
    # discovery min is 50 on pilot; for unit tests call aggregation directly.
    return project


@pytest.mark.django_db
def test_aggregate_property_values_single_mixed_missing():
    """Aggregation returns single, Mixed values, or missing display."""
    assert aggregate_property_values(["Walls", "Walls"])["status"] == "single"
    mixed = aggregate_property_values(["Walls", "Floors"])
    assert mixed["status"] == "mixed"
    assert mixed["display"] == MIXED_VALUES_LABEL
    assert aggregate_property_values(["", None])["status"] == "missing"


@pytest.mark.django_db
def test_parse_sem_cols_validates_and_caps():
    """Invalid/noisy keys ignored; valid prop keys capped."""
    keys = parse_sem_cols(
        {"sem_cols": "prop:Other.Type,prop:Other.id,not-a-prop,prop:Other.Family,prop:Other.Type"}
    )
    assert "prop:Other.Type" in keys
    assert "prop:Other.Family" in keys
    assert "prop:Other.id" not in keys
    assert "not-a-prop" not in keys


@pytest.mark.django_db
def test_enrich_selected_property_column_and_filter():
    """Selected Other.Type column aggregates onto prep rows and filters."""
    project = _project_with_props()
    quantities = ModelQuantitiesService(project).build()
    rows = list(build_preparation_ui(quantities)["prep_rows"])
    col = prop_column_key("Other.Type")
    meta = enrich_prep_rows_with_entity_semantics(project, rows, selected_prop_columns=[col])
    assert col in (meta.get("selected_prop_columns") or [])
    assert any(_str_val_row(r, col) for r in rows)
    # Filter by a concrete type present on walls
    matched = filter_prep_rows_by_semantic(
        rows, field_key=col, value="Basic Wall", allowed_extra_keys=[col]
    )
    assert matched
    assert all(r.get(col) == "Basic Wall" for r in matched)


def _str_val_row(row, key: str) -> str:
    from takeoff.services.ifc_semantic_fields import _str_val

    return _str_val(row.get(key))


@pytest.mark.django_db
def test_discover_excludes_noisy_id_keys():
    """Discovery skips id/guid noise but keeps every other indexed key.

    DYNAMIC-07: the catalogue is source-backed and complete, so ``Qto_*``
    quantity fields are included (grouped as Quantity sets with numeric
    typing) rather than filtered out as a curated Identity-only list.
    """
    project = _project_with_props()
    cols = discover_indexed_property_columns(project=project)
    by_source = {str(c["source_property"]): c for c in cols}

    assert cols, "tiny fixtures must still surface discovered columns"
    assert all(str(c["key"]).startswith("prop:") for c in cols)
    assert all(not str(c["source_property"]).lower().endswith(".id") for c in cols)
    assert all("guid" not in str(c["source_property"]).lower() for c in cols)
    assert "Other.id" not in by_source

    # Non-noisy authoring keys survive discovery.
    assert "Other.Type" in by_source
    assert "Other.Family" in by_source

    # Qto_* is catalogued, not excluded.
    qto = [c for c in cols if str(c["source_property"]).startswith("Qto_")]
    assert qto
    assert all(c["group"] == "Quantity sets" for c in qto)
    assert all(c["value_type"] == "numeric" for c in qto)


@pytest.mark.django_db
def test_runtime_sem_cols_and_page_panel(client):
    """Runtime honors sem_cols; Quantities page shows property columns panel."""
    project = _project_with_props()
    # Seed enough entities so discovery can surface Other.Type if thresholds allow;
    # still assert panel + selected column wiring via enrich path.
    col = prop_column_key("Other.Type")
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session={},
        query={
            "sem_cols": col,
            "semantic_field": col,
            "semantic_value": "Basic Wall",
            "source_classification_code": "manual_field",
        },
    )
    qty_prep = runtime["qty_prep"]
    panel = qty_prep["semantic_filters"]
    assert col in (panel.get("selected_prop_columns") or panel.get("sem_cols_param", ""))
    client.force_login(project.owner)
    response = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "sem_cols": col,
            "source_classification_code": "manual_field",
            "source_package_boq_mapping": "manual_field",
            "source_work_package": "manual_field",
        },
    )
    assert response.status_code == 200
    html = response.content.decode("utf-8")
    # TABLE-04: property columns are managed from the Add column modal, and a
    # selected sem_col is rendered as a real table column.
    assert 'data-testid="qty-columns-open"' in html
    assert 'data-testid="qty-columns-modal"' in html
    assert 'data-testid="qty-manage-columns"' in html
    assert f'data-testid="qty-prep-col-{col}"' in html
    assert "BOQ-ready" not in html
