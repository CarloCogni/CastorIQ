# takeoff/tests/test_quantities_slice5b.py
"""Quantities Slice 5b — session-only manual mapping values for manual_field."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_prep_config import CONTRACT_VERSION_V1, QuantityPrepConfigService
from takeoff.services.quantity_prep_row_mapping import (
    VALUE_MAX_LENGTH,
    QuantityPrepRowMappingService,
    apply_session_mapping_values_to_ui,
    sanitize_mapping_value,
)
from takeoff.services.quantity_preparation_ui import build_preparation_ui


def _pilot_like_project():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W-5B",
        properties={
            "Qto_WallBaseQuantities.NetVolume": 2.0,
            "Qto_WallBaseQuantities.NetArea": 12.5,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B-5B",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.4},
    )
    return project


def _ui_manual_classification(project):
    return build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings={
            "classification_code": "manual_field",
            "package_boq_mapping": "future_modify_handoff",
            "work_package": "not_mapped",
        },
    )


@pytest.mark.django_db
def test_manual_inputs_only_for_manual_field(client):
    """TABLE-04B: included mapping fields are assignable regardless of Advanced intent."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "source_classification_code": "manual_field",
            "source_package_boq_mapping": "future_modify_handoff",
            "source_work_package": "not_mapped",
        },
    ).content.decode()
    assert 'data-testid="qty-batch-map-selected"' in html
    assert "Assign metadata" in html or "Assign values" in html
    # Drawer / batch targets follow inclusion for all three fields.
    assert 'data-testid="qty-batch-field-classification_code"' in html
    assert 'data-testid="qty-batch-field-package_boq_mapping"' in html
    assert 'data-testid="qty-batch-field-work_package"' in html


@pytest.mark.django_db
def test_no_manual_input_for_future_modify_or_not_mapped(client):
    """TABLE-04B: future_modify / not_mapped no longer hide Assign values."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "source_classification_code": "future_modify_handoff",
            "source_package_boq_mapping": "not_mapped",
            "source_work_package": "future_modify_handoff",
        },
    ).content.decode()
    assert 'data-testid="qty-batch-map-selected"' in html
    assert 'data-testid="qty-batch-mapping-modal"' in html
    assert 'data-testid="qty-batch-mapping-unavailable"' not in html


@pytest.mark.django_db
def test_excluded_field_no_input(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "field_classification_code": "0",
            "source_classification_code": "manual_field",
            "source_package_boq_mapping": "manual_field",
        },
    ).content.decode()
    assert 'data-testid="qty-row-mapping-input-classification_code"' not in html
    assert 'data-testid="qty-row-mapping-input-package_boq_mapping"' in html
    assert 'data-testid="qty-prep-classification-cell"' not in html


@pytest.mark.django_db
def test_apply_classification_clears_missing_for_that_row_only(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    params = {
        "source_classification_code": "manual_field",
        "source_package_boq_mapping": "future_modify_handoff",
        "source_work_package": "not_mapped",
        "basis_IfcWall": "NetArea",
    }
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        basis_overrides={"IfcWall": "NetArea"},
        source_mappings={
            "classification_code": "manual_field",
            "package_boq_mapping": "future_modify_handoff",
            "work_package": "not_mapped",
        },
    )
    before = ui["unresolved_register"]["missing_classification"]
    assert before >= 1
    beam = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcBeam")
    wall = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcWall")
    assert beam["missing_classification"] is True
    assert wall["missing_classification"] is True

    resp = client.post(
        reverse("takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": beam["row_key"],
            "return_query": "&".join(f"{k}={v}" for k, v in params.items()),
            "classification_code": "CL-BEAM-1",
            "package_boq_mapping": "",
            "work_package": "",
        },
        follow=True,
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "CL-BEAM-1" in body
    assert 'data-testid="qty-manual-mapping-badge"' in body

    # Re-overlay from session to assert counts.
    session = client.session
    svc = QuantityPrepRowMappingService(project, project.owner, session)
    ui2 = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        basis_overrides={"IfcWall": "NetArea"},
        source_mappings={
            "classification_code": "manual_field",
            "package_boq_mapping": "future_modify_handoff",
            "work_package": "not_mapped",
        },
    )
    apply_session_mapping_values_to_ui(ui2, svc.get_annotations())
    assert ui2["unresolved_register"]["missing_classification"] == before - 1
    beam2 = next(r for r in ui2["prep_rows"] if r["ifc_class"] == "IfcBeam")
    wall2 = next(r for r in ui2["prep_rows"] if r["ifc_class"] == "IfcWall")
    assert beam2["missing_classification"] is False
    assert wall2["missing_classification"] is True
    assert (
        ui2["unresolved_register"]["missing_quantity_basis_rule"]
        == ui["unresolved_register"]["missing_quantity_basis_rule"]
    )
    assert (
        ui2["unresolved_register"]["missing_selected_quantity_source"]
        == ui["unresolved_register"]["missing_selected_quantity_source"]
    )


@pytest.mark.django_db
def test_package_and_work_clear_their_missing_flags():
    project = _pilot_like_project()
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings={
            "classification_code": "not_mapped",
            "package_boq_mapping": "manual_field",
            "work_package": "manual_field",
        },
    )
    row = ui["prep_rows"][0]
    before_pkg = ui["unresolved_register"]["missing_package_boq_mapping"]
    before_wp = ui["unresolved_register"]["missing_work_package"]
    apply_session_mapping_values_to_ui(
        ui,
        {
            row["row_key"]: {
                "package_boq_mapping": "PKG-1",
                "work_package": "WP-A",
            }
        },
    )
    row2 = next(r for r in ui["prep_rows"] if r["row_key"] == row["row_key"])
    assert row2["missing_package"] is False
    assert row2["missing_work_package"] is False
    assert ui["unresolved_register"]["missing_package_boq_mapping"] == before_pkg - 1
    assert ui["unresolved_register"]["missing_work_package"] == before_wp - 1
    assert any(c.get("id") == "manual_mapping_values" for c in ui["preparation_insights"])


@pytest.mark.django_db
def test_clear_mapping_restores_missing_and_independence_from_review(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    ui = _ui_manual_classification(project)
    row = ui["prep_rows"][0]
    mapping_url = reverse("takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk})
    review_url = reverse("takeoff:qty_prep_row_review", kwargs={"pk": project.pk})
    rq = "source_classification_code=manual_field"

    client.post(
        mapping_url,
        {
            "action": "apply",
            "row_key": row["row_key"],
            "return_query": rq,
            "classification_code": "CL-1",
        },
        follow=True,
    )
    client.post(
        review_url,
        {
            "action": "apply",
            "row_key": row["row_key"],
            "return_query": rq,
            "review_status": "reviewing",
            "note": "keep me",
        },
        follow=True,
    )
    # Clear mapping only.
    html = client.post(
        mapping_url,
        {"action": "clear", "row_key": row["row_key"], "return_query": rq},
        follow=True,
    ).content.decode()
    assert 'data-testid="qty-session-review-badge"' in html
    assert "Reviewing" in html
    # Mapping value gone for that row — badge may still exist if other rows have values;
    # ensure CL-1 not present.
    assert "CL-1" not in html

    # Re-apply mapping, clear review — mapping remains.
    client.post(
        mapping_url,
        {
            "action": "apply",
            "row_key": row["row_key"],
            "return_query": rq,
            "classification_code": "CL-2",
            "package_boq_mapping": "",
            "work_package": "",
        },
        follow=True,
    )
    html2 = client.post(
        review_url,
        {"action": "clear", "row_key": row["row_key"], "return_query": rq},
        follow=True,
    ).content.decode()
    assert "CL-2" in html2
    assert 'data-testid="qty-manual-mapping-badge"' in html2


@pytest.mark.django_db
def test_reject_non_manual_submission(client):
    """TABLE-04B: future_modify intent still accepts working Assign values overrides."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings={"classification_code": "future_modify_handoff"},
    )
    row = ui["prep_rows"][0]
    resp = client.post(
        reverse("takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": row["row_key"],
            "return_query": "source_classification_code=future_modify_handoff",
            "classification_code": "SHOULD-APPLY",
        },
    )
    assert resp.status_code in (204, 302)


@pytest.mark.django_db
def test_sanitize_and_length_limit():
    assert sanitize_mapping_value("<b>x</b>") == "x"
    long = "a" * (VALUE_MAX_LENGTH + 50)
    assert len(sanitize_mapping_value(long)) == VALUE_MAX_LENGTH


@pytest.mark.django_db
def test_stale_row_key_ignored():
    project = _pilot_like_project()
    ui = _ui_manual_classification(project)
    apply_session_mapping_values_to_ui(
        ui,
        {"v1|ifc_class|IfcGhost|-|NetVolume": {"classification_code": "STALE"}},
    )
    assert ui["session_mapping_value_count"] == 0
    assert ui["session_mapping_stale_count"] == 1
    assert all(r.get("classification_code") == "" for r in ui["prep_rows"])


@pytest.mark.django_db
def test_intent_change_ignores_stored_value():
    """TABLE-04B: working overrides survive source-intent changes (config not mutated)."""
    project = _pilot_like_project()
    ui_manual = _ui_manual_classification(project)
    row = ui_manual["prep_rows"][0]
    annotations = {row["row_key"]: {"classification_code": "CL-X"}}
    apply_session_mapping_values_to_ui(ui_manual, annotations)
    assert ui_manual["prep_rows"][0]["classification_code"] == "CL-X"

    ui_future = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings={"classification_code": "future_modify_handoff"},
    )
    apply_session_mapping_values_to_ui(ui_future, annotations)
    matched = [r for r in ui_future["prep_rows"] if r["row_key"] == row["row_key"]]
    assert matched
    assert matched[0]["classification_code"] == "CL-X"
    assert matched[0]["missing_classification"] is False
    assert ui_future["source_mapping_intents"]["classification_code"] == "future_modify_handoff"


@pytest.mark.django_db
def test_config_drafts_remain_settings_only(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    svc = QuantityPrepConfigService(project, project.owner)
    saved = svc.save_draft(
        name="5b settings",
        query={
            "source_classification_code": "manual_field",
            "basis_IfcWall": "NetArea",
        },
    )
    assert saved["error"] is None
    cfg = saved["result"]
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        basis_overrides={"IfcWall": "NetArea"},
        source_mappings={"classification_code": "manual_field"},
    )
    row = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcWall")
    session = client.session
    QuantityPrepRowMappingService(project, project.owner, session).apply_values(
        row_key=row["row_key"],
        values={"classification_code": "CL-DRAFT-TEST"},
        eligible_keys={"classification_code"},
        known_row_keys={row["row_key"]},
    )
    session.save()
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"prep_config": str(cfg.id)},
    ).content.decode()
    assert "Settings only" in html or "settings only" in html.lower()
    cfg.refresh_from_db()
    assert cfg.contract_version == CONTRACT_VERSION_V1
    blob = f"{cfg.basis_rules}{cfg.schema_fields}{cfg.source_mappings}"
    assert "CL-DRAFT-TEST" not in blob
    assert "prep_rows" not in cfg.basis_rules


@pytest.mark.django_db
def test_boundaries_no_quantity_override_or_forbidden(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"source_classification_code": "manual_field"},
    ).content.decode()
    assert 'name="total"' not in html
    assert 'data-testid="qty-export-reviewed"' not in html
    assert "Export reviewed preparation" not in html
    page = html.lower()
    for phrase in (
        "certified takeoff",
        "qs approved",
        "boq ready",
        "5d ready",
        "approve configuration",
    ):
        assert phrase not in page
    # Modify handoff control lives under Advanced; when present it stays disabled.
    if 'data-testid="qty-send-unresolved-to-modify"' in html:
        chunk = html.split('data-testid="qty-send-unresolved-to-modify"', 1)[0]
        assert "disabled" in chunk[chunk.rfind("<button") :]
    assert "Raw Indexed Quantity Inventory" in html or 'data-testid="quantities-optional-estimate"' in html
    from django.apps import apps

    names = {m.__name__ for m in apps.get_app_config("takeoff").get_models()}
    assert "QuantityPrepRowMapping" not in names
