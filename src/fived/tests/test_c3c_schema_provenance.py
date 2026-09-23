# fived/tests/test_c3c_schema_provenance.py
"""C3c — F2 quantity_provenance.mapping + F3 manual_session_schema_node bucket."""

from __future__ import annotations

from typing import Any

import pytest

from fived.models import FiveDModelRow
from fived.services.completeness_service import FiveDCompletenessService
from fived.services.snapshot_service import FiveDPrepSnapshotService
from fived.tests.factories import FiveDModelRowFactory, FiveDModelVersionFactory
from takeoff.services.quantity_prep_row_mapping import (
    ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
    QuantityPrepRowMappingService,
)
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.tests.test_quantities_slice5b import _pilot_like_project

QUERY = {
    "source_classification_code": "manual_field",
    "source_package_boq_mapping": "manual_field",
    "source_work_package": "manual_field",
    "basis_IfcWall": "NetVolume",
}

SETTINGS_ALL_INCLUDED = {
    "schema_includes": {
        "classification_code": True,
        "package_boq_mapping": True,
        "work_package": True,
    }
}


def _session() -> dict:
    return {}


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


def _walk_keys(obj, *, acc: set[str] | None = None) -> set[str]:
    keys: set[str] = acc if acc is not None else set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k))
            _walk_keys(v, acc=keys)
    elif isinstance(obj, list):
        for item in obj:
            _walk_keys(item, acc=keys)
    return keys


@pytest.mark.django_db
def test_f2_schema_backed_row_copies_provenance_mapping():
    """Schema-backed prep values land in codes + quantity_provenance.mapping."""
    project = _pilot_like_project()
    session = _session()
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=QUERY
    )
    target = _export_instance_row(runtime["qty_prep"], ifc_class="IfcWall", global_id="GID-W-5B")
    row_key = target["row_key"]
    QuantityPrepRowMappingService(project, project.owner, session).apply_values(
        row_key=row_key,
        values={
            "classification_code": {
                "value": "EL-DEMO-WALL",
                "label": "Wall elements",
                "schema_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "schema_key": "nbkch-demo-elements",
                "node_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "origin": ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
                "source_intent": "manual_field",
            },
            "package_boq_mapping": {
                "value": "PKG-DEMO-STRUCTURE",
                "label": "Structural works",
                "schema_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "schema_key": "nbkch-demo-packages",
                "node_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                "origin": ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
                "source_intent": "manual_field",
            },
            "work_package": {
                "value": "WP-DEMO-BASEMENT-Z1",
                "label": "Basement Zone 1 works",
                "schema_id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                "schema_key": "nbkch-demo-work-packages",
                "node_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                "origin": ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
                "source_intent": "manual_field",
            },
        },
        eligible_keys={
            "classification_code",
            "package_boq_mapping",
            "work_package",
        },
        known_row_keys={row_key},
    )
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="C3c Schema Model",
        version_label="c3c-1",
    )
    assert out["error"] is None
    row = out["result"]["version"].rows.get(source_row_key=row_key)
    assert row.classification_code == "EL-DEMO-WALL"
    assert row.classification_origin == ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    assert row.package_mapping == "PKG-DEMO-STRUCTURE"
    assert row.package_mapping_origin == ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    assert row.work_package == "WP-DEMO-BASEMENT-Z1"
    assert row.work_package_origin == ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    prov = row.quantity_provenance or {}
    mapping = prov.get("mapping") or {}
    assert mapping["classification"]["schema_key"] == "nbkch-demo-elements"
    assert mapping["classification"]["node_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert mapping["classification"]["label"] == "Wall elements"
    assert mapping["classification"]["origin"] == ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    assert mapping["package_mapping"]["schema_key"] == "nbkch-demo-packages"
    assert mapping["work_package"]["node_id"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"
    assert "unit_rate" not in _walk_keys(prov)
    assert "extended_cost" not in _walk_keys(prov)
    assert not hasattr(row, "package_boq_mapping")


@pytest.mark.django_db
def test_f2_free_text_no_false_schema_meta():
    """Free-text snapshot keeps manual_session and omits schema ids in mapping JSON."""
    project = _pilot_like_project()
    session = _session()
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=QUERY
    )
    target = _export_instance_row(runtime["qty_prep"], ifc_class="IfcWall", global_id="GID-W-5B")
    row_key = target["row_key"]
    QuantityPrepRowMappingService(project, project.owner, session).apply_values(
        row_key=row_key,
        values={"classification_code": "CL-FREE-C3C"},
        eligible_keys={"classification_code"},
        known_row_keys={row_key},
    )
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="C3c Free Text",
        version_label="c3c-free",
    )
    row = out["result"]["version"].rows.get(source_row_key=row_key)
    assert row.classification_code == "CL-FREE-C3C"
    assert row.classification_origin == "manual_session"
    mapping = (row.quantity_provenance or {}).get("mapping") or {}
    class_meta = mapping.get("classification") or {}
    assert class_meta.get("origin") == "manual_session"
    assert "schema_id" not in class_meta
    assert "node_id" not in class_meta


@pytest.mark.django_db
def test_f3_schema_node_counted_separately_and_filled_not_weak():
    """manual_session_schema_node is its own bucket; filled but not weak_filled."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    FiveDModelRowFactory(
        version=version,
        source_row_key="schema-node",
        quantity_basis="NetVolume",
        basis_unresolved=False,
        missing_quantity_source=False,
        classification_code="EL-DEMO-WALL",
        missing_classification=False,
        classification_origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        classification_source_intent="manual_field",
        package_mapping="PKG-DEMO-STRUCTURE",
        missing_package_mapping=False,
        package_mapping_origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        package_mapping_source_intent="manual_field",
        work_package="WP-DEMO-BASEMENT-Z1",
        missing_work_package=False,
        work_package_origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        work_package_source_intent="manual_field",
        status=FiveDModelRow.Status.STRUCTURALLY_READY,
        quantity_provenance={
            "mapping": {
                "classification": {
                    "schema_key": "nbkch-demo-elements",
                    "origin": ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
                }
            }
        },
    )
    review = FiveDCompletenessService().build_version_review(version)
    assert review["provenance_counts"]["manual_session_schema_node"] == 3
    assert review["provenance_counts"]["manual_session"] == 0
    assert review["slot_coverage"]["classification"]["filled"] == 1
    assert review["slot_coverage"]["classification"]["weak_filled"] == 0
    assert review["missing_counts"]["classification"] == 0
    assert review["categories"]["no_open_structural_gaps"] == 1
    assert review["non_claims"]["schema_session_is_not_approved"] is True
    assert review["non_claims"]["manual_session_is_weak_provenance"] is True
    keys = set(_walk_keys(review))
    assert "official" not in keys
    assert "certified" not in keys
    assert "boq_ready" not in keys
    assert "5d_ready" not in keys
