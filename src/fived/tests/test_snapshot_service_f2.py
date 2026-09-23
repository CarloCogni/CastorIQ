# fived/tests/test_snapshot_service_f2.py
"""5D-F2 FiveDPrepSnapshotService tests.

Snapshots Quantities prep runtime into immutable versions.
Must not use QTOCache, create ModificationProposal, or write IFC.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.sessions.backends.db import SessionStore

from environments.tests.factories import ProjectFactory
from fived.models import FiveDDataModel, FiveDModelRow, FiveDModelVersion
from fived.services.snapshot_service import FiveDPrepSnapshotService
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_prep_row_mapping import QuantityPrepRowMappingService
from takeoff.services.quantity_prep_row_review import QuantityPrepRowReviewService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _project_with_ifc():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="fived-snap.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-F2-W",
        properties={"Qto_WallBaseQuantities.NetVolume": 3.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-F2-B",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.5},
    )
    return project


def _session() -> SessionStore:
    store = SessionStore()
    store.create()
    return store


QUERY = {
    "basis_IfcWall": "NetVolume",
    "basis_IfcBeam": "NetVolume",
    "field_type_name": "1",
    "field_classification_code": "1",
    "field_package_boq_mapping": "1",
    "field_work_package": "1",
    "source_classification_code": "manual_field",
    "source_package_boq_mapping": "manual_field",
    "source_work_package": "manual_field",
}


def _export_instance_row(
    qty_prep: dict[str, Any], *, ifc_class: str, global_id: str
) -> dict[str, Any]:
    """Return the frozen instance export row for one IFC class + GlobalId."""
    gid_token = f"gid:{global_id}"
    matches = [
        row
        for row in (qty_prep.get("prep_rows_export") or [])
        if isinstance(row, dict)
        and str(row.get("level") or "") == "instance"
        and str(row.get("ifc_class") or "") == ifc_class
        and (
            str(row.get("global_id") or "") == global_id
            or gid_token in str(row.get("row_key") or "")
        )
    ]
    assert matches, f"missing instance export row for {ifc_class} {global_id}"
    row = matches[0]
    assert str(row.get("level") or "") == "instance"
    assert str(row.get("row_key") or "").startswith("v1|instance|")
    return row


@pytest.mark.django_db
def test_snapshot_creates_model_version_rows_from_runtime():
    """Snapshot service creates data_model/version/rows from Quantities prep runtime."""
    project = _project_with_ifc()
    session = _session()
    svc = FiveDPrepSnapshotService(project, project.owner)
    out = svc.create_snapshot(
        session=session,
        query=QUERY,
        model_name="F2 Snapshot Model",
        version_label="v1",
        notes="foundation test",
    )
    assert out["error"] is None
    result = out["result"]
    assert result["data_model"].name == "F2 Snapshot Model"
    assert result["version"].version_label == "v1"
    assert result["version"].status == FiveDModelVersion.Status.FROZEN
    assert result["rows_created"] == result["version"].row_count
    assert result["version"].rows.count() == result["rows_created"]
    assert result["rows_created"] >= 1
    assert result["content_hash"]
    assert len(result["content_hash"]) == 64


@pytest.mark.django_db
def test_settings_and_annotations_copied():
    """settings_snapshot and session_annotations_snapshot are copied."""
    project = _project_with_ifc()
    session = _session()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query=QUERY,
    )
    target = _export_instance_row(runtime["qty_prep"], ifc_class="IfcBeam", global_id="GID-F2-B")
    row_key = target["row_key"]
    QuantityPrepRowReviewService(project, project.owner, session).apply_review(
        row_key=row_key,
        review_status="reviewing",
        note="f2 review",
        known_row_keys={row_key},
    )
    QuantityPrepRowMappingService(project, project.owner, session).apply_values(
        row_key=row_key,
        values={
            "classification_code": "CL-F2",
            "package_boq_mapping": "PKG-F2",
            "work_package": "WP-F2",
        },
        eligible_keys={"classification_code", "package_boq_mapping", "work_package"},
        known_row_keys={row_key},
    )

    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="Annotated Model",
        version_label="ann-1",
    )
    assert out["error"] is None
    version = out["result"]["version"]
    settings = version.settings_snapshot
    assert settings["basis_rules"].get("IfcWall") == "NetVolume"
    assert settings["source_mappings"]["classification_code"] == "manual_field"
    assert "basis_rules" in settings
    assert "schema_includes" in settings

    annotations = version.session_annotations_snapshot
    assert row_key in annotations["row_reviews"]
    assert annotations["row_reviews"][row_key]["review_status"] == "reviewing"
    assert row_key in annotations["manual_mappings"]
    assert annotations["manual_mappings"][row_key]["classification_code"] == "CL-F2"

    row = version.rows.get(source_row_key=row_key)
    assert row.classification_code == "CL-F2"
    assert row.classification_origin == "manual_session"
    assert row.package_mapping == "PKG-F2"
    assert row.package_mapping_origin == "manual_session"
    assert row.work_package == "WP-F2"
    assert row.manual_mapping_applied is True
    assert "package_boq_mapping" not in {f.name for f in FiveDModelRow._meta.get_fields()}


@pytest.mark.django_db
def test_package_boq_mapping_stored_as_package_mapping():
    """Prep package_boq_mapping is stored as package_mapping (not BOQ output)."""
    project = _project_with_ifc()
    session = _session()
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=QUERY
    )
    target = _export_instance_row(runtime["qty_prep"], ifc_class="IfcBeam", global_id="GID-F2-B")
    row_key = target["row_key"]
    QuantityPrepRowMappingService(project, project.owner, session).apply_values(
        row_key=row_key,
        values={"package_boq_mapping": "PKG-ONLY"},
        eligible_keys={"package_boq_mapping"},
        known_row_keys={row_key},
    )
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="Package Map Model",
        version_label="pkg-1",
    )
    row = out["result"]["version"].rows.get(source_row_key=row_key)
    assert row.package_mapping == "PKG-ONLY"
    assert not hasattr(row, "package_boq_mapping")


@pytest.mark.django_db
def test_new_version_not_mutate_existing():
    """Service creates a new version rather than mutating an existing version."""
    project = _project_with_ifc()
    session = _session()
    svc = FiveDPrepSnapshotService(project, project.owner)
    first = svc.create_snapshot(
        session=session, query=QUERY, model_name="Shared", version_label="v1"
    )
    assert first["error"] is None
    model = first["result"]["data_model"]
    v1 = first["result"]["version"]
    hash1 = v1.content_hash
    count1 = v1.row_count

    second = svc.create_snapshot(
        session=session,
        query=QUERY,
        model_name="Shared",
        version_label="v2",
        data_model=model,
    )
    assert second["error"] is None
    v2 = second["result"]["version"]
    v1.refresh_from_db()
    assert v1.pk != v2.pk
    assert v1.content_hash == hash1
    assert v1.row_count == count1
    assert model.versions.count() == 2


@pytest.mark.django_db
def test_duplicate_version_label_errors():
    """Creating the same version_label twice returns an error."""
    project = _project_with_ifc()
    session = _session()
    svc = FiveDPrepSnapshotService(project, project.owner)
    first = svc.create_snapshot(
        session=session, query=QUERY, model_name="Dup", version_label="same"
    )
    model = first["result"]["data_model"]
    second = svc.create_snapshot(
        session=session,
        query=QUERY,
        model_name="Dup",
        version_label="same",
        data_model=model,
    )
    assert second["result"] is None
    assert "version_label" in (second["error"] or "")


@pytest.mark.django_db
def test_service_does_not_use_qto_cache():
    """Snapshot path must not touch QTOCache."""
    project = _project_with_ifc()
    session = _session()
    with patch("takeoff.models.QTOCache") as mock_cache:
        out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
            session=session,
            query=QUERY,
            model_name="No Cache",
            version_label="v1",
        )
        assert out["error"] is None
        mock_cache.objects.assert_not_called()
        mock_cache.assert_not_called()


@pytest.mark.django_db
def test_no_modification_proposal_or_writeback_side_effects():
    """Snapshot must not create ModificationProposal rows."""
    from writeback.models import ModificationProposal

    project = _project_with_ifc()
    session = _session()
    before = ModificationProposal.objects.count()
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="No Writeback",
        version_label="v1",
    )
    assert out["error"] is None
    assert ModificationProposal.objects.count() == before
    assert FiveDDataModel.objects.filter(project=project).exists()


@pytest.mark.django_db
def test_boundary_snapshot_on_created_version():
    """Created version stores Stage 1 boundary flags."""
    project = _project_with_ifc()
    session = _session()
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="Boundary",
        version_label="v1",
    )
    boundary = out["result"]["version"].boundary_snapshot
    assert boundary["not_boq"] is True
    assert boundary["not_cost_estimate"] is True
    assert boundary["not_evm"] is True
    assert boundary["no_rates"] is True
    assert boundary["snapshot_stage"] == "stage_1_preparation_snapshot"
