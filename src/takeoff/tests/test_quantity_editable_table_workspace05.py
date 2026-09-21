# takeoff/tests/test_quantity_editable_table_workspace05.py
"""R5D-QTO-WORKSPACE-05 — durable save/resume of editable quantity tables."""

from __future__ import annotations

import copy

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from fived.models import FiveDModelVersion
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
)
from takeoff.models import QuantityEditableTable
from takeoff.services.ifc_semantic_fields import prop_column_key
from takeoff.services.quantity_editable_table import (
    COLUMNS_UI_EXCLUDED_SOURCE_PROPS,
    CONTRACT_VERSION,
    QuantityEditableTableService,
    SourceMismatchError,
    get_active_editable_binding,
    is_editable_table_dirty,
)
from takeoff.services.quantity_output_units import QuantityOutputUnitsService
from takeoff.services.quantity_prep_row_mapping import QuantityPrepRowMappingService
from takeoff.services.quantity_prep_row_measurement import QuantityPrepRowMeasurementService
from takeoff.services.quantity_prep_row_review import QuantityPrepRowReviewService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_unit_conversion import FAMILY_VOLUME


def _project_with_beam(*, file_hash: str = "a" * 64):
    project = ProjectFactory()
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        name="workspace05.ifc",
        file_hash=file_hash,
    )
    et = IFCElementTypeFactory(
        ifc_file=ifc, name="BeamType-A", ifc_type="IfcBeamType", global_id="ET-W05"
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-W05-1",
        name="BeamType-A",
        element_type=et,
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 12.5,
            "Qto_BeamBaseQuantities.GrossVolume": 13.0,
            "Qto_BeamBaseQuantities.Length": 4000.0,
            "Identity Data.Type Name": "BeamType-A",
            "Pset_ManufacturerTypeInformation.Manufacturer": "Acme",
            "Other.Category": "Structural Framing",
            "Other.Family": "Concrete Beam",
        },
    )
    return project, ifc


def _first_prep_row(project, user, session, query=None):
    runtime = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query=query or {},
    )
    rows = runtime["qty_prep"].get("prep_rows") or []
    assert rows, "expected prep rows"
    return runtime, rows[0]


@pytest.mark.django_db
def test_save_new_and_update_same_table_no_duplicate(client):
    """First save creates one row; second save updates revision without duplicate."""
    project, _ifc = _project_with_beam()
    client.force_login(project.owner)
    url = reverse("takeoff:qty_editable_table_save", kwargs={"pk": project.pk})
    r1 = client.post(url, {"name": "W05 Structural beams"})
    assert r1.status_code in {302, 204}
    assert QuantityEditableTable.objects.filter(project=project).count() == 1
    table = QuantityEditableTable.objects.get(project=project)
    assert table.revision == 1
    assert table.contract_version == CONTRACT_VERSION

    r2 = client.post(
        url,
        {
            "name": "W05 Structural beams",
            "table_id": str(table.pk),
            "revision": str(table.revision),
        },
    )
    assert r2.status_code in {302, 204}
    assert QuantityEditableTable.objects.filter(project=project).count() == 1
    table.refresh_from_db()
    assert table.revision == 2


@pytest.mark.django_db
def test_full_state_round_trip_columns_filter_measure_units_assign_review():
    """Capture and restore supported working state via measurement_target_key."""
    project, ifc = _project_with_beam()
    user = project.owner
    session: dict = {}
    query = {
        "sem_cols": prop_column_key("Pset_ManufacturerTypeInformation.Manufacturer"),
        "semantic_field": "ifc_class",
        "semantic_value": "IfcBeam",
    }
    runtime, row = _first_prep_row(project, user, session, query)
    rk = str(row["row_key"])
    mt = str(row["measurement_target_key"])

    QuantityPrepRowMappingService(project, user, session).apply_values(
        row_key=rk,
        values={"classification_code": "EL-DEMO-BEAM"},
        eligible_keys={"classification_code"},
        known_row_keys={rk},
    )
    QuantityPrepRowReviewService(project, user, session).apply_review(
        row_key=rk,
        review_status="needs_review",
        note="Check volume family",
        known_row_keys={rk},
    )
    # Prefer GrossVolume when available
    QuantityPrepRowMeasurementService(project, user, session).apply_choice(
        measurement_target_key=mt,
        measurement_type="Volume",
        selected_source="GrossVolume",
        known_target_keys={mt},
    )
    QuantityOutputUnitsService(project, user, session).apply_output_units({FAMILY_VOLUME: "cm3"})

    svc = QuantityEditableTableService(project, user)
    out = svc.save_new(name="W05 Round Trip", session=session, query=query)
    assert out["error"] is None
    table = out["result"]
    assert table.ifc_file_id == ifc.pk
    assert table.ifc_file_hash == ifc.file_hash
    state = table.state
    assert state["query"]["sem_cols"]
    assert "Manufacturer" in state["query"]["sem_cols"]
    assert (
        state["assignments"]["by_measurement_target"][mt]["classification_code"] == "EL-DEMO-BEAM"
    )
    assert state["reviews"]["by_measurement_target"][mt]["note"] == "Check volume family"
    assert mt in state["measurements"]["choices"]

    session2: dict = {}
    restored = svc.restore_into_session(table=table, session=session2)
    assert restored["error"] is None
    runtime2 = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session2,
        query=restored["query"],
        ifc_file=ifc,
    )
    row2 = next(
        r
        for r in (runtime2["qty_prep"].get("prep_rows") or [])
        if r.get("measurement_target_key") == mt
    )
    assert row2.get("classification_code") == "EL-DEMO-BEAM"
    assert row2.get("session_review_status") == "needs_review" or "Needs review" in str(
        row2.get("review_status_display") or ""
    )
    assert (
        str(row2.get("selected_source") or "") == "GrossVolume"
        or str(row2.get("ifc_quantity_source") or "") == "GrossVolume"
    )
    units = QuantityOutputUnitsService(project, user, session2).get_output_units()
    assert units.get(FAMILY_VOLUME) == "cm3"
    # No double conversion: model_total retained
    assert row2.get("model_total") is not None


@pytest.mark.django_db
def test_assignments_survive_measurement_change_via_mt_key():
    """Assignments keyed by measurement_target_key survive basis/measurement edits."""
    project, ifc = _project_with_beam()
    user = project.owner
    session: dict = {}
    _, row = _first_prep_row(project, user, session)
    rk = str(row["row_key"])
    mt = str(row["measurement_target_key"])
    QuantityPrepRowMappingService(project, user, session).apply_values(
        row_key=rk,
        values={"classification_code": "PKG-STABLE"},
        eligible_keys={"classification_code"},
        known_row_keys={rk},
    )
    svc = QuantityEditableTableService(project, user)
    table = svc.save_new(name="W05 MT Stable", session=session, query={})["result"]
    # Change measurement after save
    QuantityPrepRowMeasurementService(project, user, session).apply_choice(
        measurement_target_key=mt,
        measurement_type="Volume",
        selected_source="NetVolume",
        known_target_keys={mt},
    )
    table = svc.save_update(
        table_id=table.pk,
        expected_revision=table.revision,
        session=session,
        query={},
    )["result"]
    session_b: dict = {}
    restored = svc.restore_into_session(table=table, session=session_b)
    assert restored["error"] is None
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session_b, query={}, ifc_file=ifc
    )
    row_b = next(
        r
        for r in (runtime["qty_prep"].get("prep_rows") or [])
        if r.get("measurement_target_key") == mt
    )
    assert row_b.get("classification_code") == "PKG-STABLE"


@pytest.mark.django_db
def test_logout_new_session_restores_saved_table(client):
    """Saved table reopens after a new authenticated session."""
    project, _ifc = _project_with_beam()
    client.force_login(project.owner)
    session = client.session
    _, row = _first_prep_row(project, project.owner, session)
    rk = str(row["row_key"])
    QuantityPrepRowReviewService(project, project.owner, session).apply_review(
        row_key=rk,
        review_status="reviewed_for_preparation",
        note="ok",
        known_row_keys={rk},
    )
    session.save()
    save_url = reverse("takeoff:qty_editable_table_save", kwargs={"pk": project.pk})
    assert client.post(save_url, {"name": "W05 Logout Restore"}).status_code in {302, 204}
    table = QuantityEditableTable.objects.get(project=project)

    client.logout()
    client.force_login(project.owner)
    open_url = reverse("takeoff:qty_editable_table_open", kwargs={"pk": project.pk})
    resp = client.post(open_url, {"table_id": str(table.pk)})
    assert resp.status_code in {302, 204}
    binding = get_active_editable_binding(client.session, project.pk)
    assert binding is not None
    assert binding["id"] == str(table.pk)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert "W05 Logout Restore" in html
    assert 'data-testid="qty-editable-status"' in html
    assert "Saved" in html


@pytest.mark.django_db
def test_source_mismatch_refuses_silent_rebind():
    """Changed IFC hash blocks restore without applying assignments."""
    project, ifc = _project_with_beam(file_hash="b" * 64)
    user = project.owner
    session: dict = {}
    svc = QuantityEditableTableService(project, user)
    table = svc.save_new(name="W05 Hash Pin", session=session, query={})["result"]
    ifc.file_hash = "c" * 64
    ifc.save(update_fields=["file_hash"])
    with pytest.raises(SourceMismatchError):
        from takeoff.services.quantity_editable_table import verify_source_identity

        verify_source_identity(
            project=project, ifc_file_id=ifc.pk, expected_hash=table.ifc_file_hash
        )
    out = svc.restore_into_session(table=table, session={})
    assert out["result"] is None
    assert "changed" in (out["error"] or "").lower() or "missing" in (out["error"] or "").lower()


@pytest.mark.django_db
def test_stale_revision_conflict_rejects_overwrite(client):
    """Stale revision returns conflict and leaves stored state intact."""
    project, _ifc = _project_with_beam()
    client.force_login(project.owner)
    svc = QuantityEditableTableService(project, project.owner)
    session = client.session
    table = svc.save_new(name="W05 Conflict", session=session, query={})["result"]
    original_state = copy.deepcopy(table.state)
    # Concurrent update bumps revision
    table.state = {**table.state, "captured_at": "concurrent"}
    table.revision = 2
    table.save(update_fields=["state", "revision", "updated_at"])

    url = reverse("takeoff:qty_editable_table_save", kwargs={"pk": project.pk})
    resp = client.post(
        url,
        {
            "name": "W05 Conflict",
            "table_id": str(table.pk),
            "revision": "1",
        },
    )
    assert resp.status_code == 409
    table.refresh_from_db()
    assert table.revision == 2
    assert table.state.get("captured_at") == "concurrent"
    # Prior payload from revision-2 write remains (not overwritten by stale client)
    assert table.state != original_state or table.revision == 2


@pytest.mark.django_db
def test_invalid_payload_leaves_previous_state_intact():
    """Failed validate on update must not replace a valid stored table."""
    from unittest.mock import patch

    from takeoff.services.quantity_editable_table import InvalidPayloadError

    project, _ifc = _project_with_beam()
    user = project.owner
    session: dict = {}
    svc = QuantityEditableTableService(project, user)
    table = svc.save_new(name="W05 Intact", session=session, query={})["result"]
    good = copy.deepcopy(table.state)
    with patch.object(svc, "validate_state", side_effect=InvalidPayloadError("bad payload")):
        out = svc.save_update(
            table_id=table.pk,
            expected_revision=table.revision,
            session=session,
            query={},
        )
    assert out["result"] is None
    assert out["error"]
    table.refresh_from_db()
    assert table.state == good
    assert table.revision == 1


@pytest.mark.django_db
def test_cross_project_access_denied(client):
    """Opening/saving another project's table id is rejected."""
    project_a, _ = _project_with_beam(file_hash="d" * 64)
    project_b, _ = _project_with_beam(file_hash="e" * 64)
    client.force_login(project_a.owner)
    table_b = QuantityEditableTableService(project_b, project_b.owner).save_new(
        name="Other Project Table", session={}, query={}
    )["result"]
    open_url = reverse("takeoff:qty_editable_table_open", kwargs={"pk": project_a.pk})
    resp = client.post(open_url, {"table_id": str(table_b.pk)})
    assert resp.status_code == 404
    save_url = reverse("takeoff:qty_editable_table_save", kwargs={"pk": project_a.pk})
    resp2 = client.post(
        save_url,
        {
            "name": "Hijack",
            "table_id": str(table_b.pk),
            "revision": "1",
        },
    )
    assert resp2.status_code == 400
    assert not QuantityEditableTable.objects.filter(project=project_a).exists()
    table_b.refresh_from_db()
    assert table_b.name == "Other Project Table"


@pytest.mark.django_db
def test_category_family_excluded_from_columns_ui(client):
    """Category/Family stay out of Columns picker options."""
    project, _ifc = _project_with_beam()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    # Options in Columns modal should not list Category/Family labels as addable props
    # when present as Other.Category / Other.Family
    assert "Other.Category" not in html or "prop:Other.Category" not in html
    runtime = build_qty_prep_session_ui(project=project, user=project.owner, session={}, query={})
    available = runtime["qty_prep"]["semantic_filters"].get("property_columns_available") or []
    sources = {str(d.get("source_property") or "") for d in available}
    assert sources.isdisjoint(COLUMNS_UI_EXCLUDED_SOURCE_PROPS)
    labels = {str(d.get("label") or "").lower() for d in available}
    assert "category" not in labels
    assert "family" not in labels


@pytest.mark.django_db
def test_freeze_and_export_from_reopened_table(client):
    """Reopened editable table can freeze (Save version) and export."""
    project, _ifc = _project_with_beam()
    client.force_login(project.owner)
    session = client.session
    svc = QuantityEditableTableService(project, project.owner)
    table = svc.save_new(name="W05 Freeze Export", session=session, query={})["result"]
    session.save()
    client.post(
        reverse("takeoff:qty_editable_table_open", kwargs={"pk": project.pk}),
        {"table_id": str(table.pk)},
    )
    freeze = client.post(
        reverse("takeoff:qty_prep_freeze", kwargs={"pk": project.pk}),
        {"version_label": "W05-RO-Version"},
    )
    assert freeze.status_code in {302, 204}
    assert FiveDModelVersion.objects.filter(
        data_model__project_id=project.pk, version_label="W05-RO-Version"
    ).exists()
    export = client.get(reverse("takeoff:qty_prep_export", kwargs={"pk": project.pk}))
    assert export.status_code == 200
    assert export["Content-Type"] == "application/zip"


@pytest.mark.django_db
def test_ambiguous_unmatched_targets_not_silently_attached():
    """Ambiguous measurement targets are reported; values are not attached to wrong rows."""
    project, ifc = _project_with_beam()
    user = project.owner
    session: dict = {}
    svc = QuantityEditableTableService(project, user)
    table = svc.save_new(name="W05 Ambiguous", session=session, query={})["result"]
    # Inject assignment for unknown mt key
    state = copy.deepcopy(table.state)
    state["assignments"]["by_measurement_target"]["mt1|missing|target"] = {
        "classification_code": "SHOULD-NOT-APPLY"
    }
    table.state = state
    table.save(update_fields=["state", "updated_at"])
    session2: dict = {}
    out = svc.restore_into_session(table=table, session=session2)
    assert out["error"] is None
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session2, query={}, ifc_file=ifc
    )
    report = runtime["qty_prep"].get("editable_restore_report") or {}
    assert "mt1|missing|target" in (report.get("unmatched_assignment_targets") or [])
    for row in runtime["qty_prep"].get("prep_rows") or []:
        assert row.get("classification_code") != "SHOULD-NOT-APPLY"


@pytest.mark.django_db
def test_ui_exposes_save_open_and_status(client):
    """Toolbar exposes Save/Open with truthful Not saved default."""
    project, _ = _project_with_beam()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert 'data-testid="qty-save-table-open"' in html
    assert 'data-testid="qty-open-table-open"' in html
    assert 'data-testid="qty-save-table-modal"' in html
    assert 'data-testid="qty-open-table-modal"' in html
    assert "No saved tables yet" in html
    assert "Not saved" in html
    assert "Save table keeps your editable work" in html
    assert is_editable_table_dirty(client.session, project.pk) is False
