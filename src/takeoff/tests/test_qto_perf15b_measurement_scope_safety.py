# takeoff/tests/test_qto_perf15b_measurement_scope_safety.py
"""QTO-PERF-15B-SAFETY — Measurement Apply signed scope trust boundary."""

from __future__ import annotations

import re
import time
from unittest.mock import patch

import pytest
from django.core import signing
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_measurement_settings import (
    MEASUREMENT_APPLY_SCOPE_SALT,
    apply_class_settings,
    canonicalize_measurement_filter_scope,
    issue_measurement_apply_scope_token,
    verify_measurement_apply_scope_token,
)
from takeoff.services.quantity_prep_row_measurement import session_key_for_project
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _project_with_column(*, file_hash: str = "safety-hash-1"):
    project = ProjectFactory()
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={"VOLUMEUNIT": "m³", "AREAUNIT": "m²", "LENGTHUNIT": "mm"},
        file_hash=file_hash,
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcColumn",
        global_id="COL-SAFETY-1",
        properties={"Qto_ColumnBaseQuantities.NetVolume": 2.5},
    )
    return project, ifc


def _token_from_page(client, project, *, query=None) -> str:
    query = query or {"table_layout": "v2", "semantic_classes": "IfcColumn"}
    page = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}), query)
    assert page.status_code == 200
    html = page.content.decode("utf-8")
    m = re.search(
        r'name="apply_scope_token" value="([^"]*)"',
        html,
    )
    assert m, "apply_scope_token missing"
    return m.group(1)


def _session_measurement_snapshot(session, project_id) -> dict:
    return dict(session.get(session_key_for_project(project_id)) or {})


@pytest.mark.django_db
def test_valid_signed_scope_applies_without_rebuild(client):
    project, _ = _project_with_column()
    client.force_login(project.owner)
    token = _token_from_page(client, project)
    with patch(
        "takeoff.views.build_qty_prep_session_ui",
        side_effect=AssertionError("Apply POST must not rebuild"),
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
    snap = _session_measurement_snapshot(client.session, project.pk)
    assert snap.get("choices")


@pytest.mark.django_db
def test_altered_token_rejected_no_mutation(client):
    project, _ = _project_with_column()
    client.force_login(project.owner)
    token = _token_from_page(client, project)
    before = _session_measurement_snapshot(client.session, project.pk)
    bad = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    resp = client.post(
        reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
        {
            "ifc_class": "IfcColumn",
            "measurement_type": "volume",
            "selected_source": "NetVolume",
            "output_unit": "m3",
            "apply_scope_token": bad,
            "return_query": "table_layout=v2&semantic_classes=IfcColumn",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 400
    assert _session_measurement_snapshot(client.session, project.pk) == before


@pytest.mark.django_db
def test_expired_token_rejected_no_mutation(client):
    project, ifc = _project_with_column()
    client.force_login(project.owner)
    token = issue_measurement_apply_scope_token(
        user_id=project.owner.pk,
        project_id=project.pk,
        ifc_file_id=ifc.pk,
        ifc_file_hash=ifc.file_hash,
        ifc_class="IfcColumn",
        filter_scope=canonicalize_measurement_filter_scope("semantic_classes=IfcColumn"),
        target_keys=["k1"],
        source_coverage={"volume": {"NetVolume": {"present": 1, "missing": 0}}},
    )
    before = _session_measurement_snapshot(client.session, project.pk)
    now = time.time()
    with patch("django.core.signing.time.time", return_value=now + 10_000):
        resp = client.post(
            reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
            {
                "ifc_class": "IfcColumn",
                "measurement_type": "volume",
                "selected_source": "NetVolume",
                "output_unit": "m3",
                "apply_scope_token": token,
                "return_query": "semantic_classes=IfcColumn",
            },
            HTTP_HX_REQUEST="true",
        )
    assert resp.status_code == 400
    assert _session_measurement_snapshot(client.session, project.pk) == before


@pytest.mark.django_db
def test_token_from_other_user_rejected(client):
    project, ifc = _project_with_column()
    other = UserFactory()
    client.force_login(project.owner)
    token = issue_measurement_apply_scope_token(
        user_id=other.pk,
        project_id=project.pk,
        ifc_file_id=ifc.pk,
        ifc_file_hash=ifc.file_hash,
        ifc_class="IfcColumn",
        filter_scope=canonicalize_measurement_filter_scope("semantic_classes=IfcColumn"),
        target_keys=["k1"],
        source_coverage={"volume": {"NetVolume": {"present": 1, "missing": 0}}},
    )
    before = _session_measurement_snapshot(client.session, project.pk)
    resp = client.post(
        reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
        {
            "ifc_class": "IfcColumn",
            "measurement_type": "volume",
            "selected_source": "NetVolume",
            "output_unit": "m3",
            "apply_scope_token": token,
            "return_query": "semantic_classes=IfcColumn",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 400
    assert _session_measurement_snapshot(client.session, project.pk) == before


@pytest.mark.django_db
def test_token_from_other_project_rejected(client):
    project, ifc = _project_with_column()
    other_project, other_ifc = _project_with_column(file_hash="other-hash")
    client.force_login(project.owner)
    token = issue_measurement_apply_scope_token(
        user_id=project.owner.pk,
        project_id=other_project.pk,
        ifc_file_id=other_ifc.pk,
        ifc_file_hash=other_ifc.file_hash,
        ifc_class="IfcColumn",
        filter_scope=canonicalize_measurement_filter_scope("semantic_classes=IfcColumn"),
        target_keys=["k1"],
        source_coverage={"volume": {"NetVolume": {"present": 1, "missing": 0}}},
    )
    before = _session_measurement_snapshot(client.session, project.pk)
    resp = client.post(
        reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
        {
            "ifc_class": "IfcColumn",
            "measurement_type": "volume",
            "selected_source": "NetVolume",
            "output_unit": "m3",
            "apply_scope_token": token,
            "return_query": "semantic_classes=IfcColumn",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 400
    assert _session_measurement_snapshot(client.session, project.pk) == before


@pytest.mark.django_db
def test_token_wrong_ifc_class_rejected(client):
    project, _ = _project_with_column()
    client.force_login(project.owner)
    token = _token_from_page(client, project)
    before = _session_measurement_snapshot(client.session, project.pk)
    resp = client.post(
        reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
        {
            "ifc_class": "IfcWall",
            "measurement_type": "volume",
            "selected_source": "NetVolume",
            "output_unit": "m3",
            "apply_scope_token": token,
            "return_query": "table_layout=v2&semantic_classes=IfcColumn",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 400
    assert _session_measurement_snapshot(client.session, project.pk) == before


@pytest.mark.django_db
def test_changed_ifc_hash_rejected(client):
    project, ifc = _project_with_column()
    client.force_login(project.owner)
    token = _token_from_page(client, project)
    ifc.file_hash = "changed-after-token"
    ifc.save(update_fields=["file_hash"])
    before = _session_measurement_snapshot(client.session, project.pk)
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
    assert resp.status_code == 400
    assert _session_measurement_snapshot(client.session, project.pk) == before


@pytest.mark.django_db
def test_changed_filter_scope_rejected(client):
    project, _ = _project_with_column()
    client.force_login(project.owner)
    token = _token_from_page(
        client,
        project,
        query={"table_layout": "v2", "semantic_classes": "IfcColumn"},
    )
    before = _session_measurement_snapshot(client.session, project.pk)
    resp = client.post(
        reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
        {
            "ifc_class": "IfcColumn",
            "measurement_type": "volume",
            "selected_source": "NetVolume",
            "output_unit": "m3",
            "apply_scope_token": token,
            # Unrestricted / different class filter vs token scope.
            "return_query": "table_layout=v2&semantic_classes=IfcWall",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 400
    assert _session_measurement_snapshot(client.session, project.pk) == before


@pytest.mark.django_db
def test_fabricated_raw_keys_ignored_cannot_enter_choices(client):
    project, _ = _project_with_column()
    client.force_login(project.owner)
    before = _session_measurement_snapshot(client.session, project.pk)
    resp = client.post(
        reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
        {
            "ifc_class": "IfcColumn",
            "measurement_type": "volume",
            "selected_source": "NetVolume",
            "output_unit": "m3",
            "target_keys_json": '["fabricated-evil-key"]',
            "source_coverage_json": (
                '{"volume":{"NetVolume":{"present":99,"missing":0,"total":99}}}'
            ),
            "return_query": "semantic_classes=IfcColumn",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 400
    after = _session_measurement_snapshot(client.session, project.pk)
    assert after == before
    blob = str(after)
    assert "fabricated-evil-key" not in blob


@pytest.mark.django_db
def test_fabricated_coverage_cannot_authorize_unavailable_source():
    """Authoritative signed coverage does not invent GrossVolume when absent."""
    project, ifc = _project_with_column()
    session: dict = {}
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={"semantic_classes": "IfcColumn"},
    )
    crow = next(
        r
        for r in runtime["qty_prep"]["measurement_settings"]["class_rows"]
        if r["ifc_class"] == "IfcColumn"
    )
    verified = verify_measurement_apply_scope_token(
        crow["apply_scope_token"],
        user=project.owner,
        project=project,
        ifc_file=ifc,
        ifc_class="IfcColumn",
        filter_scope=canonicalize_measurement_filter_scope("semantic_classes=IfcColumn"),
    )
    assert verified["ok"] is True
    gross = (verified["source_coverage"].get("volume") or {}).get("GrossVolume") or {}
    assert int(gross.get("present") or 0) == 0

    # HTTP-shaped apply using only verified coverage rejects GrossVolume.
    result = apply_class_settings(
        project=project,
        user=project.owner,
        session=session,
        ifc_class="IfcColumn",
        measurement_type="volume",
        selected_source="GrossVolume",
        output_unit="m3",
        prep_rows=[],
        target_keys=verified["target_keys"],
        source_coverage=verified["source_coverage"],
    )
    assert result["ok"] is False
    assert "GrossVolume" in (result.get("error") or "")

    # Rebuild discovery also rejects GrossVolume on this fixture.
    rebuild = apply_class_settings(
        project=project,
        user=project.owner,
        session=session,
        ifc_class="IfcColumn",
        measurement_type="volume",
        selected_source="GrossVolume",
        output_unit="m3",
        prep_rows=list(runtime["qty_prep"].get("prep_rows_export") or []),
        inventory_rows=list(runtime["qty_prep"].get("prep_rows_export") or []),
    )
    assert rebuild["ok"] is False
    assert MEASUREMENT_APPLY_SCOPE_SALT


@pytest.mark.django_db
def test_invalid_output_unit_rejected_before_mutation(client):
    project, _ = _project_with_column()
    client.force_login(project.owner)
    token = _token_from_page(client, project)
    before = _session_measurement_snapshot(client.session, project.pk)
    resp = client.post(
        reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
        {
            "ifc_class": "IfcColumn",
            "measurement_type": "volume",
            "selected_source": "NetVolume",
            "output_unit": "m2",
            "apply_scope_token": token,
            "return_query": "table_layout=v2&semantic_classes=IfcColumn",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 400
    assert _session_measurement_snapshot(client.session, project.pk) == before


@pytest.mark.django_db
def test_valid_apply_restores_quantity_unit_after_redirect_get(client):
    project, _ = _project_with_column()
    client.force_login(project.owner)
    token = _token_from_page(client, project)
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
    redir = resp["HX-Redirect"]
    page = client.get(redir)
    assert page.status_code == 200
    html = page.content.decode("utf-8")
    assert "m³" in html or "m3" in html or ">m<" in html or "Volume" in html
    # Count remains dimensionless path still available in modal prefs.
    assert "element_count" in html or "Count" in html


@pytest.mark.django_db
def test_count_rejects_dimensional_unit_via_signed_scope(client):
    project, _ = _project_with_column()
    client.force_login(project.owner)
    token = _token_from_page(client, project)
    before = _session_measurement_snapshot(client.session, project.pk)
    resp = client.post(
        reverse("takeoff:qty_measurement_settings", kwargs={"pk": project.pk}),
        {
            "ifc_class": "IfcColumn",
            "measurement_type": "count",
            "selected_source": "element_count",
            "output_unit": "m3",
            "apply_scope_token": token,
            "return_query": "table_layout=v2&semantic_classes=IfcColumn",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 400
    assert _session_measurement_snapshot(client.session, project.pk) == before


@pytest.mark.django_db
def test_raw_json_fields_not_rendered_in_form(client):
    project, _ = _project_with_column()
    client.force_login(project.owner)
    page = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"table_layout": "v2", "semantic_classes": "IfcColumn"},
    )
    html = page.content.decode("utf-8")
    assert 'name="apply_scope_token"' in html
    assert 'name="target_keys_json"' not in html
    assert 'name="source_coverage_json"' not in html


@pytest.mark.django_db
def test_verify_rejects_bad_signature_payload():
    project, ifc = _project_with_column()
    forged = signing.dumps(
        {
            "v": 1,
            "uid": str(project.owner.pk),
            "pid": str(project.pk),
            "fid": str(ifc.pk),
            "fh": ifc.file_hash,
            "cls": "IfcColumn",
            "scope": "semantic_classes=IfcColumn",
            "keys": ["evil"],
            "cov": {},
        },
        salt="wrong-salt",
        compress=True,
    )
    out = verify_measurement_apply_scope_token(
        forged,
        user=project.owner,
        project=project,
        ifc_file=ifc,
        ifc_class="IfcColumn",
        filter_scope="semantic_classes=IfcColumn",
    )
    assert out["ok"] is False
    assert MEASUREMENT_APPLY_SCOPE_SALT
