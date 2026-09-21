# takeoff/tests/test_qto_perf15b_post_no_rebuild.py
"""QTO-PERF-15B — POST handlers must not full-rebuild before redirect."""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_editable_table import QuantityEditableTableService
from takeoff.services.quantity_measurement_settings import apply_class_settings
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _project_with_column():
    project = ProjectFactory()
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={"VOLUMEUNIT": "m³", "AREAUNIT": "m²", "LENGTHUNIT": "mm"},
        file_hash="perf15b-hash-1",
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcColumn",
        global_id="COL-P15B-1",
        properties={"Qto_ColumnBaseQuantities.NetVolume": 2.5},
    )
    return project, ifc


def _extract_apply_scope_token(html: str) -> str:
    m = re.search(
        r'data-testid="qty-measurement-class-form"[^>]*>.*?'
        r'name="apply_scope_token" value="([^"]*)"',
        html,
        re.S,
    )
    assert m, "apply_scope_token missing from Measurement settings form"
    return m.group(1)


@pytest.mark.django_db
def test_open_post_does_not_call_full_rebuild(client):
    """Open POST restores session and redirects without build_qty_prep_session_ui."""
    project, ifc = _project_with_column()
    user = project.owner
    session: dict = {}
    svc = QuantityEditableTableService(project, user)
    table = svc.save_new(
        name="PERF15B Open",
        session=session,
        query={"table_layout": "v2", "semantic_classes": "IfcColumn"},
    )["result"]
    assert table.ifc_file_id == ifc.pk

    client.force_login(user)
    with patch(
        "takeoff.services.quantity_editable_table.build_qty_prep_session_ui"
    ) as banned_capture:
        banned_capture.side_effect = AssertionError("Open must not rebuild via editable_table")
        with patch(
            "takeoff.views.build_qty_prep_session_ui",
            side_effect=AssertionError("Open POST must not call build_qty_prep_session_ui"),
        ):
            with patch(
                "takeoff.services.quantity_editable_table.build_qty_prep_session_ui",
                side_effect=AssertionError("Open restore must not rebuild"),
            ):
                resp = client.post(
                    reverse("takeoff:qty_editable_table_open", kwargs={"pk": project.pk}),
                    {"table_id": str(table.pk)},
                    HTTP_HX_REQUEST="true",
                )
    assert resp.status_code == 204
    assert resp.get("HX-Redirect")
    assert "build_qty_prep_session_ui" not in (resp.content.decode("utf-8", errors="ignore"))


@pytest.mark.django_db
def test_restore_into_session_zero_rebuilds_then_get_rematches():
    """Service Open path: zero rebuilds; first GET rematches assignments."""
    project, ifc = _project_with_column()
    user = project.owner
    session: dict = {}
    runtime = build_qty_prep_session_ui(project=project, user=user, session=session, query={})
    row = next(
        r
        for r in (runtime["qty_prep"].get("prep_rows_export") or runtime["qty_prep"]["prep_rows"])
        if isinstance(r, dict) and r.get("measurement_target_key")
    )
    from takeoff.services.quantity_prep_row_mapping import QuantityPrepRowMappingService

    QuantityPrepRowMappingService(project, user, session).apply_values(
        row_key=str(row["row_key"]),
        values={"classification_code": "EL-DEMO-COLUMN"},
        eligible_keys={"classification_code"},
        known_row_keys={str(row["row_key"])},
    )
    svc = QuantityEditableTableService(project, user)
    table = svc.save_new(name="PERF15B Rematch", session=session, query={})["result"]

    fresh: dict = {}
    with patch(
        "takeoff.services.quantity_editable_table.build_qty_prep_session_ui",
        side_effect=AssertionError("restore_into_session must not rebuild"),
    ):
        out = svc.restore_into_session(table=table, session=fresh)
    assert out["error"] is None
    assert out["restore_report"].get("deferred") is True

    after = build_qty_prep_session_ui(
        project=project, user=user, session=fresh, query=out["query"], ifc_file=ifc
    )
    matched = [
        r
        for r in (after["qty_prep"].get("prep_rows_export") or after["qty_prep"]["prep_rows"] or [])
        if isinstance(r, dict) and r.get("classification_code") == "EL-DEMO-COLUMN"
    ]
    assert matched
    assert after["qty_prep"].get("editable_restore_report") is not None


@pytest.mark.django_db
def test_measurement_settings_post_does_not_rebuild(client):
    """Measurement Apply POST uses signed scope — no full rebuild."""
    project, _ifc = _project_with_column()
    client.force_login(project.owner)
    page = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"table_layout": "v2", "semantic_classes": "IfcColumn"},
    )
    assert page.status_code == 200
    html = page.content.decode("utf-8")
    assert 'name="apply_scope_token"' in html
    assert 'name="target_keys_json"' not in html
    assert 'name="source_coverage_json"' not in html
    token = _extract_apply_scope_token(html)
    assert token

    with patch(
        "takeoff.views.build_qty_prep_session_ui",
        side_effect=AssertionError("Measurement Apply POST must not rebuild"),
    ):
        resp = client.post(
            reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
            {
                "ifc_class": "IfcColumn",
                "measurement_type": "volume",
                "selected_source": "NetVolume",
                "output_unit": "m3",
                "apply_scope_token": token,
                "return_query": "table_layout=v2&semantic_classes=IfcColumn",
            },
            HTTP_HX_REQUEST="true",
        )
    assert resp.status_code == 204
    assert resp.get("HX-Redirect")


@pytest.mark.django_db
def test_measurement_settings_invalid_without_token(client):
    """Missing scope token yields validation error and no session mutation."""
    project, _ = _project_with_column()
    client.force_login(project.owner)
    before = dict(client.session.items())
    resp = client.post(
        reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
        {
            "ifc_class": "IfcColumn",
            "measurement_type": "volume",
            "selected_source": "NetVolume",
            "output_unit": "m3",
            "target_keys_json": '["fabricated-key"]',
            "source_coverage_json": '{"volume":{"NetVolume":{"present":1}}}',
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 400
    after = dict(client.session.items())
    assert after == before


@pytest.mark.django_db
def test_apply_class_settings_authoritative_keys_path():
    """Service accepts authoritative target_keys without prep_rows."""
    project, _ = _project_with_column()
    session: dict = {}
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query={}
    )
    crow = next(
        r
        for r in runtime["qty_prep"]["measurement_settings"]["class_rows"]
        if r.get("ifc_class") == "IfcColumn"
    )
    keys = list(crow.get("target_keys") or [])
    assert keys
    result = apply_class_settings(
        project=project,
        user=project.owner,
        session=session,
        ifc_class="IfcColumn",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="m3",
        prep_rows=[],
        target_keys=keys,
        source_coverage=crow.get("source_coverage") or {},
    )
    assert result["ok"] is True
    assert result["affected_targets"] >= 1


@pytest.mark.django_db
def test_measurement_settings_unauthorized():
    """Anonymous POST is rejected."""
    from django.test import Client

    project, _ = _project_with_column()
    anon = Client()
    resp = anon.post(
        reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
        {"ifc_class": "IfcColumn", "measurement_type": "volume", "selected_source": "NetVolume"},
    )
    assert resp.status_code in {302, 401, 403}


@pytest.mark.django_db
def test_save_stale_revision_still_409(client):
    """Stale editable-table revision still conflicts with no silent overwrite."""
    project, _ = _project_with_column()
    user = project.owner
    client.force_login(user)
    client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}))
    svc = QuantityEditableTableService(project, user)
    table = svc.save_new(name="PERF15B Stale", session=dict(client.session), query={})["result"]
    resp = client.post(
        reverse("takeoff:qty_editable_table_save", kwargs={"pk": project.pk}),
        {
            "table_id": str(table.pk),
            "revision": "999999",
            "name": "PERF15B Stale",
            "return_query": "",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 409
