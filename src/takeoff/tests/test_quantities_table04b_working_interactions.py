# takeoff/tests/test_quantities_table04b_working_interactions.py
"""R5D-QTO-TABLE-04B — IFC columns + Assign values without Advanced."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classification.services.project_schema_seed import (
    seed_demo_project_classification_schemas,
)
from classification.services.quantity_mapping_selectors import get_selector_options_for_field
from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.ifc_semantic_fields import (
    MISSING_DISPLAY,
    MIXED_VALUES_LABEL,
    aggregate_property_values,
    prop_column_key,
)
from takeoff.services.quantity_prep_row_mapping import (
    QuantityPrepRowMappingService,
    eligible_mapping_fields,
)
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_unit_conversion import conversion_example_text


def _seed_demo_schemas(project) -> None:
    seed_demo_project_classification_schemas(project)


def _project_with_props():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="table04b.ifc")
    # Same type, mixed Material values → Mixed values
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-T04B-1",
        name="BeamType-A",
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 1.0,
            "Qto_BeamBaseQuantities.GrossVolume": 1.2,
            "Identity Data.Type Name": "BeamType-A",
            "Pset_ManufacturerTypeInformation.Manufacturer": "Acme",
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-T04B-2",
        name="BeamType-A",
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 2.0,
            "Qto_BeamBaseQuantities.GrossVolume": 2.2,
            "Identity Data.Type Name": "BeamType-A",
            "Pset_ManufacturerTypeInformation.Manufacturer": "BetaCo",
        },
    )
    # Uniform manufacturer on a second type
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-T04B-3",
        name="BeamType-B",
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 3.0,
            "Identity Data.Type Name": "BeamType-B",
            "Pset_ManufacturerTypeInformation.Manufacturer": "Acme",
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-T04B-W",
        name="WallType-A",
        properties={
            "Qto_WallBaseQuantities.NetVolume": 4.0,
            "Pset_ManufacturerTypeInformation.Manufacturer": "WallMfr",
        },
    )
    return project


@pytest.mark.django_db
def test_table04b_assign_values_available_without_manual_field_query(client):
    """Default configuration exposes Assign values without Advanced source params."""
    project = _project_with_props()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert 'data-testid="qty-batch-map-selected"' in html
    assert "Assign metadata" in html or "Assign values" in html
    assert 'data-testid="qty-batch-mapping-modal"' in html
    # Default URL must not require source_*=manual_field
    assert (
        "source_classification_code=manual_field"
        not in html.split('data-testid="qty-batch-mapping-form"', 1)[0]
    )


@pytest.mark.django_db
def test_table04b_eligible_fields_ignore_source_intent_but_require_include():
    """Assignable when included; source intent is not a gate and is not mutated."""
    fields = eligible_mapping_fields(
        show={
            "classification_code": True,
            "package_boq_mapping": True,
            "work_package": False,
        },
        source_intents={
            "classification_code": "future_modify_handoff",
            "package_boq_mapping": "not_mapped",
            "work_package": "manual_field",
        },
    )
    keys = {f["key"] for f in fields}
    assert keys == {"classification_code", "package_boq_mapping"}


@pytest.mark.django_db
def test_table04b_assign_preview_apply_without_advanced_and_no_intent_mutation(client):
    """Preview+Apply works on default intents; source intents stay unchanged."""
    project = _project_with_props()
    _seed_demo_schemas(project)
    client.force_login(project.owner)
    session = client.session
    # Include classification so overlay projects assigned values (hierarchy contract).
    query = {
        "table_layout": "v2",
        "col_order": "ifc_class,name,quantity,unit,status,actions,classification_code",
    }
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=query
    )
    intents_before = dict(runtime["qty_prep"].get("source_mapping_intents") or {})

    export = [
        r
        for r in (runtime["qty_prep"].get("prep_rows_export") or [])
        if r.get("level") == "instance" and r.get("ifc_class") == "IfcBeam"
    ]
    assert export
    # Prefer BeamType-B instance when present.
    target = next(
        (r for r in export if "BeamType-B" in str(r.get("type_name") or r.get("name") or "")),
        export[0],
    )
    control = next(
        r
        for r in (runtime["qty_prep"].get("prep_rows_export") or [])
        if r.get("ifc_class") == "IfcWall" and r.get("level") == "instance"
    )
    keys = [target["row_key"]]

    pack = get_selector_options_for_field(project, "classification_code")
    nodes = list(pack.get("nodes") or [])
    node = next(
        (n for n in nodes if "BEAM" in str(n.get("code") or "").upper()),
        nodes[0] if nodes else None,
    )
    if node is None:
        pytest.skip("No classification schema nodes in test DB")

    url = reverse("takeoff:qty_prep_row_mapping_batch", kwargs={"pk": project.pk})
    preview = client.post(
        url,
        {
            "action": "preview",
            "classification_code__node_id": node["node_id"],
            "row_keys": keys,
            "return_query": (
                "table_layout=v2&col_order=ifc_class,name,quantity,unit,status,actions,"
                "classification_code"
            ),
        },
    )
    assert preview.status_code == 200
    body = preview.content.decode()
    assert 'data-testid="qty-batch-mapping-preview-result"' in body
    assert str(node.get("code") or "") in body or str(node.get("label") or "") in body

    apply = client.post(
        url,
        {
            "action": "apply",
            "classification_code__node_id": node["node_id"],
            "row_keys": keys,
            "return_query": (
                "table_layout=v2&col_order=ifc_class,name,quantity,unit,status,actions,"
                "classification_code"
            ),
        },
    )
    assert apply.status_code in (204, 302)

    runtime2 = build_qty_prep_session_ui(
        project=project, user=project.owner, session=client.session, query=query
    )
    intents_after = dict(runtime2["qty_prep"].get("source_mapping_intents") or {})
    assert intents_after == intents_before

    by_key = {
        r["row_key"]: r
        for r in (runtime2["qty_prep"].get("prep_rows_export") or [])
        if r.get("row_key")
    }
    assigned = by_key[target["row_key"]]
    untouched = by_key[control["row_key"]]
    expected_code = str(node.get("code") or node.get("label") or "")
    assert assigned.get("classification_code")
    assert expected_code in str(assigned.get("classification_code") or "")
    assert assigned.get("manual_mapping") is True
    assert not untouched.get("classification_code")
    assert untouched.get("package_boq_mapping") == control.get("package_boq_mapping")


@pytest.mark.django_db
def test_table04b_unchanged_fields_and_preview_gate(client):
    """Empty targets leave fields unchanged; Apply stays gated by preview contract."""
    project = _project_with_props()
    _seed_demo_schemas(project)
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert 'data-qty-batch-apply-requires-preview="1"' in html
    apply_idx = html.find('data-testid="qty-batch-apply-btn"')
    assert "disabled" in html[apply_idx : apply_idx + 400]


@pytest.mark.django_db
def test_table04b_assignment_survives_measurement_change(client):
    """Session assignment stays keyed by stable row_key across measurement edits."""
    project = _project_with_props()
    client.force_login(project.owner)
    session = client.session
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query={}
    )
    row = next(r for r in runtime["qty_prep"]["prep_rows"] if r.get("ifc_class") == "IfcBeam")
    key = row["row_key"]
    target = row["measurement_target_key"]
    QuantityPrepRowMappingService(project, project.owner, session).apply_values(
        row_key=key,
        values={"classification_code": "EL-TEST-ASSIGN"},
        eligible_keys={"classification_code", "package_boq_mapping", "work_package"},
        known_row_keys={key},
    )
    session.save()

    # Change measurement via service
    from takeoff.services.quantity_prep_row_measurement import QuantityPrepRowMeasurementService

    QuantityPrepRowMeasurementService(project, project.owner, session).apply_choice(
        measurement_target_key=target,
        measurement_type="volume",
        selected_source="GrossVolume",
        known_target_keys={target},
    )
    session.save()

    runtime2 = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query={}
    )
    again = next(r for r in runtime2["qty_prep"]["prep_rows"] if r.get("row_key") == key)
    assert again.get("classification_code") == "EL-TEST-ASSIGN"
    assert again.get("ifc_quantity_source") == "GrossVolume"


@pytest.mark.django_db
def test_table04b_ifc_column_add_shows_aggregated_truth(client):
    """Added IFC property column shows single / Mixed values / — honestly."""
    assert aggregate_property_values(["Acme", "Acme"])["display"] == "Acme"
    assert aggregate_property_values(["Acme", "BetaCo"])["display"] == MIXED_VALUES_LABEL
    assert aggregate_property_values([])["display"] == MISSING_DISPLAY
    assert aggregate_property_values(["0", "0"])["display"] == "0"

    project = _project_with_props()
    client.force_login(project.owner)
    col = prop_column_key("Pset_ManufacturerTypeInformation.Manufacturer")
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"sem_cols": col, "prep_page_size": "50"},
    ).content.decode()
    assert f'data-testid="qty-prep-col-{col}"' in html or f'data-col-key="{col}"' in html
    assert "Manufacturer" in html
    # Mixed or uniform values appear as cells
    assert 'data-testid="qty-prep-prop-cell"' in html
    assert MIXED_VALUES_LABEL in html or "Acme" in html

    # Refresh/filter preserves column via URL
    filtered = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "sem_cols": col,
            "semantic_field": "ifc_class",
            "semantic_value": "IfcBeam",
            "prep_page": "1",
            "prep_page_size": "50",
        },
    ).content.decode()
    assert col in filtered or "Manufacturer" in filtered
    assert f'data-testid="qty-prep-col-{col}"' in filtered or f'data-col-key="{col}"' in filtered


@pytest.mark.django_db
def test_table04b_sem_cols_not_duplicated_after_filter(client):
    """Filter preserve path emits a single sem_cols (no duplicated key/value)."""
    project = _project_with_props()
    client.force_login(project.owner)
    col = prop_column_key("Pset_ManufacturerTypeInformation.Manufacturer")
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    # Simulate prior duplicate query params (pre-fix URL shape).
    html = client.get(
        url,
        [
            ("sem_cols", col),
            ("sem_cols", col),
            ("semantic_field", "ifc_class"),
            ("semantic_value", "IfcBeam"),
            ("prep_page_size", "50"),
        ],
    ).content.decode()
    assert f'data-testid="qty-prep-col-{col}"' in html or f'data-col-key="{col}"' in html
    # Filter form must not echo both GET copies + another hidden.
    form = html.split('data-testid="qty-semantic-filters-form"', 1)[1].split("</form>", 1)[0]
    hidden_sem = form.count('name="sem_cols"')
    assert hidden_sem == 1, f"expected one sem_cols hidden, got {hidden_sem}"
    assert col in form

    from django.http import QueryDict

    from takeoff.services.quantity_prep_pagination import build_pagination_query

    qd = QueryDict(mutable=True)
    qd.appendlist("sem_cols", col)
    qd.appendlist("sem_cols", col)
    qd["semantic_field"] = "ifc_class"
    built = build_pagination_query(qd, page=2, page_size=50)
    assert built.count("sem_cols=") == 1


@pytest.mark.django_db
def test_table04b_column_remove_clears_presentation_only(client):
    """Removing a column drops presentation; scanned property inventory remains."""
    project = _project_with_props()
    client.force_login(project.owner)
    col = prop_column_key("Pset_ManufacturerTypeInformation.Manufacturer")
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    with_col = client.get(url, {"sem_cols": col}).content.decode()
    assert f'data-testid="qty-prep-col-{col}"' in with_col or f'data-col-key="{col}"' in with_col
    assert "Manufacturer" in with_col
    without = client.get(url, {"sem_cols": ""}).content.decode()
    assert f'data-testid="qty-prep-col-{col}"' not in without
    assert f'data-col-key="{col}"' not in without
    # Columns modal still offers indexed properties via lazy catalogue (not deleted).
    assert (
        'data-testid="qty-column-field"' in without
        or 'data-catalogue-url="' in without
        or 'data-testid="qty-property-add"' in without
    )


def test_table04b_unit_example_follows_output_choice():
    """Units example tracks selected output; identical units → No conversion."""
    assert conversion_example_text(model_unit="mm", output_unit="mm") == "No conversion"
    assert "→" in conversion_example_text(model_unit="mm", output_unit="m")
    assert "m" in conversion_example_text(model_unit="mm", output_unit="m")
    assert conversion_example_text(model_unit="count", output_unit="count") == "unchanged"


@pytest.mark.django_db
def test_table04b_invalid_node_rejected(client):
    """Invalid schema node ids are rejected / ignored without inventing values."""
    project = _project_with_props()
    client.force_login(project.owner)
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=client.session, query={}
    )
    key = runtime["qty_prep"]["prep_rows"][0]["row_key"]
    url = reverse("takeoff:qty_prep_row_mapping_batch", kwargs={"pk": project.pk})
    resp = client.post(
        url,
        {
            "action": "preview",
            "classification_code__node_id": "00000000-0000-0000-0000-000000000000",
            "row_keys": [key],
        },
    )
    # Either 400 (no valid targets) or preview with no proposed classification
    if resp.status_code == 200:
        assert "classification" not in resp.content.decode().lower() or "—" in resp.content.decode()
    else:
        assert resp.status_code == 400
