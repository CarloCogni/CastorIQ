# takeoff/tests/test_quantities_slice5e.py
"""Quantities Slice 5e-1 — true preparation model export (CSV+JSON ZIP)."""

from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_prep_config import QuantityPrepConfigService
from takeoff.services.quantity_prep_export import (
    CONTRACT_VERSION_V1,
    EXPORT_KIND,
    neutralize_csv_cell,
)
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.urls import urlpatterns


def _project_with_ifc():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="prep-export.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-5E-W",
        properties={"Qto_WallBaseQuantities.NetVolume": 3.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-5E-B",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.5},
    )
    return project


def _open_zip(response) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(response.content))


@pytest.mark.django_db
def test_prep_export_route_returns_zip_with_three_members(client):
    project = _project_with_ifc()
    client.force_login(project.owner)
    url = reverse("takeoff:qty_prep_export", kwargs={"pk": project.pk})
    resp = client.get(
        url,
        {"basis_IfcWall": "NetVolume", "source_classification_code": "manual_field"},
    )
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/zip"
    assert 'filename="qty_prep_' in resp["Content-Disposition"]
    assert "qto_" not in resp["Content-Disposition"]
    assert "boq_" not in resp["Content-Disposition"]

    with _open_zip(resp) as zf:
        names = set(zf.namelist())
        assert names == {"BOUNDARY.txt", "preparation_export.json", "rows.csv"}
        boundary = zf.read("BOUNDARY.txt").decode("utf-8")
        doc = json.loads(zf.read("preparation_export.json").decode("utf-8"))
        csv_text = zf.read("rows.csv").decode("utf-8")

    assert doc["contract_version"] == CONTRACT_VERSION_V1
    assert doc["export_kind"] == EXPORT_KIND
    for phrase in (
        "Not BOQ",
        "Not QS-certified takeoff",
        "Not a cost estimate",
        "Not IFC writeback",
        "Not a Modify proposal",
        "session-only",
    ):
        assert phrase in boundary

    blob = json.dumps(doc).lower()
    for bad in (
        "unit_cost",
        "total_cost",
        "estimated_cost",
        "boq ready",
        "qs approved",
        "5d ready",
    ):
        assert bad not in blob

    assert "row_key" in csv_text
    assert "unit_cost" not in csv_text.lower()


@pytest.mark.django_db
def test_export_reflects_basis_schema_source_and_sessions(client):
    project = _project_with_ifc()
    client.force_login(project.owner)
    params = {
        "basis_IfcWall": "NetVolume",
        "field_zone": "0",
        "field_type_name": "1",
        "field_classification_code": "1",
        "field_package_boq_mapping": "0",
        "source_classification_code": "manual_field",
        "source_work_package": "not_mapped",
        # REVIEW-08: mapping fields export only when present in col_order.
        "table_layout": "v2",
        "col_order": "ifc_class,name,type_name,classification_code,status,actions",
    }
    assert client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}), params).status_code == 200
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=client.session,
        query=params,
    )
    # HIERARCHY-09: export/freeze use instance keys from prep_rows_export.
    export_rows = runtime["qty_prep"].get("prep_rows_export") or []
    assert export_rows, "expected instance export rows"
    row_key = export_rows[0]["row_key"]
    rq = "&".join(f"{k}={v}" for k, v in params.items())

    client.post(
        reverse("takeoff:qty_prep_row_review", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": row_key,
            "return_query": rq,
            "review_status": "reviewing",
            "note": "5e review note",
        },
        follow=True,
    )
    client.post(
        reverse("takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": row_key,
            "return_query": rq,
            "classification_code": "CL-5E-EXPORT",
        },
        follow=True,
    )

    resp = client.get(reverse("takeoff:qty_prep_export", kwargs={"pk": project.pk}), params)
    assert resp.status_code == 200
    with _open_zip(resp) as zf:
        doc = json.loads(zf.read("preparation_export.json").decode("utf-8"))
        csv_text = zf.read("rows.csv").decode("utf-8")

    assert doc["settings"]["basis_rules"].get("IfcWall") == "NetVolume"
    assert doc["settings"]["schema_includes"].get("package_boq_mapping") is False
    assert doc["settings"]["schema_includes"].get("classification_code") is True
    assert doc["settings"]["source_mappings"]["classification_code"] == "manual_field"

    exported = next(r for r in doc["rows"] if r["row_key"] == row_key)
    assert exported["session_review_status"] == "reviewing"
    assert "5e review note" in exported["session_review_note"]
    assert exported["classification_code"] == "CL-5E-EXPORT"
    assert exported["classification_code_origin"] == "manual_session"
    assert "package_boq_mapping" not in exported

    headers = csv.DictReader(io.StringIO(csv_text)).fieldnames or []
    assert "classification_code" in headers
    assert "classification_code_origin" in headers
    assert "package_boq_mapping" not in headers
    assert "CL-5E-EXPORT" in csv_text


@pytest.mark.django_db
def test_export_excludes_ineligible_manual_mapping_and_includes_draft(client):
    project = _project_with_ifc()
    client.force_login(project.owner)
    saved = QuantityPrepConfigService(project, project.owner).save_draft(
        name="5e-export-draft",
        description="",
        query={
            "basis_IfcWall": "NetVolume",
            "field_classification_code": "1",
            "field_type_name": "1",
            "source_classification_code": "future_modify_handoff",
            "source_package_boq_mapping": "not_mapped",
            "source_work_package": "not_mapped",
            "table_layout": "v2",
            "col_order": "ifc_class,name,classification_code,status,actions",
        },
    )
    assert saved.get("error") is None
    cfg = saved["result"]

    params_manual = {
        "basis_IfcWall": "NetVolume",
        "source_classification_code": "manual_field",
        "table_layout": "v2",
        "col_order": "ifc_class,name,classification_code,status,actions",
    }
    client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}), params_manual)
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=client.session,
        query=params_manual,
    )
    export_rows = runtime["qty_prep"].get("prep_rows_export") or []
    assert export_rows, "expected instance export rows"
    row_key = export_rows[0]["row_key"]
    client.post(
        reverse("takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": row_key,
            "return_query": (
                "source_classification_code=manual_field"
                "&table_layout=v2"
                "&col_order=ifc_class,name,classification_code,status,actions"
            ),
            "classification_code": "SESSION-ASSIGN-VALUE",
        },
        follow=True,
    )

    resp = client.get(
        reverse("takeoff:qty_prep_export", kwargs={"pk": project.pk}),
        {"prep_config": str(cfg.pk)},
    )
    with _open_zip(resp) as zf:
        doc = json.loads(zf.read("preparation_export.json").decode("utf-8"))

    assert doc["settings"]["prep_config"] is not None
    assert doc["settings"]["prep_config"]["name"] == "5e-export-draft"
    assert doc["settings"]["source_mappings"]["classification_code"] == "future_modify_handoff"
    exported = next(r for r in doc["rows"] if r["row_key"] == row_key)
    # TABLE-04B: future_modify no longer blocks working Assign values on export rows;
    # provenance still reports the draft source intent.
    assert exported.get("classification_code_origin") == "deferred_modify"
    assert exported.get("classification_code_source_intent") == "future_modify_handoff"
    # Session overlay may be present; it does not rewrite settings.source_mappings.
    assert exported.get("classification_code") in ("", None, "SESSION-ASSIGN-VALUE")
    assert doc["settings"]["source_mappings"]["classification_code"] != "manual_field"


@pytest.mark.django_db
def test_csv_injection_protection_and_legacy_separation(client):
    assert neutralize_csv_cell("=1+1") == "'=1+1"
    assert neutralize_csv_cell("+cmd") == "'+cmd"
    assert neutralize_csv_cell("safe") == "safe"

    project = _project_with_ifc()
    client.force_login(project.owner)
    params = {
        "source_classification_code": "manual_field",
        "basis_IfcWall": "NetVolume",
        "table_layout": "v2",
        "col_order": "ifc_class,name,classification_code,status,actions",
    }
    client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}), params)
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=client.session,
        query=params,
    )
    export_rows = runtime["qty_prep"].get("prep_rows_export") or []
    assert export_rows
    row_key = export_rows[0]["row_key"]
    rq = (
        "source_classification_code=manual_field&basis_IfcWall=NetVolume"
        "&table_layout=v2&col_order=ifc_class,name,classification_code,status,actions"
    )
    client.post(
        reverse("takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": row_key,
            "return_query": rq,
            "classification_code": "=CMD",
        },
        follow=True,
    )

    resp = client.get(reverse("takeoff:qty_prep_export", kwargs={"pk": project.pk}), params)
    with _open_zip(resp) as zf:
        csv_text = zf.read("rows.csv").decode("utf-8")
        doc = json.loads(zf.read("preparation_export.json").decode("utf-8"))
    exported = next(r for r in doc["rows"] if r["row_key"] == row_key)
    assert exported["classification_code"] == "=CMD"
    assert "'=CMD" in csv_text

    names = {getattr(p, "name", None) for p in urlpatterns}
    assert "qty_prep_export" in names
    assert "qto_export" in names

    page = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}), params).content.decode()
    assert "Export table" in page
    assert 'data-testid="qty-prep-export"' in page
    assert "Export legacy QTO cache" in page
    assert "Export preparation data model" not in page
    # href appears before data-testid on the anchor.
    idx = page.find('data-testid="qty-prep-export"')
    prep_anchor = page[max(0, idx - 220) : idx + 80]
    assert "/prep-export/" in prep_anchor
    idx_l = page.find('data-testid="qty-advanced-export"')
    legacy_anchor = page[max(0, idx_l - 220) : idx_l + 80]
    assert "/export/" in legacy_anchor
    assert "/prep-export/" not in legacy_anchor

    from django.apps import apps

    assert "ModificationProposal" not in {
        m.__name__ for m in apps.get_app_config("takeoff").get_models()
    }
