# fived/tests/test_content_hash_contract_hash1.py
"""HASH-1: deterministic content-hash contract for frozen snapshots."""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest
from django.contrib.sessions.backends.db import SessionStore

from environments.tests.factories import ProjectFactory
from fived.models import FiveDModelRow, FiveDModelVersion
from fived.services.content_hash_contract import (
    CONTENT_HASH_CONTRACT_V2,
    CURRENT_CONTENT_HASH_CONTRACT,
    ContentHashStatus,
    assess_version_content_hash,
    canonicalize_json_value,
    compute_content_hash,
    row_fingerprint_from_mapping,
    sort_row_fingerprints,
    verify_version_content_hash,
)
from fived.services.snapshot_service import FiveDPrepSnapshotService, _content_hash
from fived.tests.factories import FiveDModelRowFactory, FiveDModelVersionFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

QUERY = {
    "basis_IfcWall": "NetVolume",
    "source_classification_code": "manual_field",
}


def _session() -> SessionStore:
    store = SessionStore()
    store.create()
    return store


def _project_with_ifc():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="hash1.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-HASH1-W",
        properties={"Qto_WallBaseQuantities.NetVolume": 3.0},
    )
    return project


def _fingerprint(**overrides: object) -> dict:
    base = {
        "source_row_key": "k1",
        "ifc_class": "IfcWall",
        "type_name": "W",
        "quantity_basis": "NetVolume",
        "total_quantity": 1.5,
        "classification_code": "",
        "package_mapping": "",
        "work_package": "",
        "classification_origin": "empty",
        "package_mapping_origin": "empty",
        "work_package_origin": "empty",
        "session_review_status": "",
    }
    base.update(overrides)
    return row_fingerprint_from_mapping(base)


@pytest.mark.django_db
def test_create_and_verify_share_canonical_builder():
    """Fresh freeze verifies immediately from persisted DB rows."""
    project = _project_with_ifc()
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=_session(),
        query=QUERY,
        model_name="HASH1 Shared Builder",
        version_label="shared-v1",
    )
    assert out["error"] is None
    version = FiveDModelVersion.objects.get(pk=out["result"]["version"].pk)
    assert version.content_hash_contract_version == CURRENT_CONTENT_HASH_CONTRACT
    assert verify_version_content_hash(version) is True
    assessment = assess_version_content_hash(version)
    assert assessment.status == ContentHashStatus.VERIFIED


@pytest.mark.django_db
def test_shuffled_persisted_row_retrieval_same_hash():
    """DB retrieval order must not change the digest."""
    version = FiveDModelVersionFactory(
        content_hash_contract_version=CONTENT_HASH_CONTRACT_V2,
        settings_snapshot={"k": "v"},
        semantic_source_readiness_snapshot={
            "contract_version": "sem4a_readiness_v1",
            "fields": [{"key": "a", "message": "m"}],
            "helper": "",
            "future_bridge_note": "",
        },
    )
    FiveDModelRowFactory(version=version, source_row_key="z-row", total_quantity=3.0)
    FiveDModelRowFactory(version=version, source_row_key="a-row", total_quantity=1.0)
    FiveDModelRowFactory(version=version, source_row_key="m-row", total_quantity=2.0)
    version.row_count = 3
    digest = compute_content_hash(
        settings=version.settings_snapshot,
        row_fingerprints=[
            row_fingerprint_from_mapping(
                {
                    "source_row_key": r.source_row_key,
                    "ifc_class": r.ifc_class,
                    "type_name": r.type_name,
                    "quantity_basis": r.quantity_basis,
                    "total_quantity": r.total_quantity,
                    "classification_code": r.classification_code,
                    "package_mapping": r.package_mapping,
                    "work_package": r.work_package,
                    "classification_origin": r.classification_origin,
                    "package_mapping_origin": r.package_mapping_origin,
                    "work_package_origin": r.work_package_origin,
                    "session_review_status": r.session_review_status,
                }
            )
            for r in version.rows.order_by("?")
        ],
        semantic_source_readiness=version.semantic_source_readiness_snapshot,
    )
    version.content_hash = digest
    version.save(update_fields=["content_hash", "row_count", "content_hash_contract_version"])
    version.refresh_from_db()
    assert verify_version_content_hash(version) is True
    # Explicit reverse PK order still verifies.
    assert (
        compute_content_hash(
            settings=version.settings_snapshot,
            row_fingerprints=[
                row_fingerprint_from_mapping(
                    {
                        "source_row_key": r.source_row_key,
                        "ifc_class": r.ifc_class,
                        "type_name": r.type_name,
                        "quantity_basis": r.quantity_basis,
                        "total_quantity": r.total_quantity,
                        "classification_code": r.classification_code,
                        "package_mapping": r.package_mapping,
                        "work_package": r.work_package,
                        "classification_origin": r.classification_origin,
                        "package_mapping_origin": r.package_mapping_origin,
                        "work_package_origin": r.work_package_origin,
                        "session_review_status": r.session_review_status,
                    }
                )
                for r in version.rows.order_by("-pk")
            ],
            semantic_source_readiness=version.semantic_source_readiness_snapshot,
        )
        == version.content_hash
    )


def test_python_sort_independent_of_input_order():
    """Canonical sort is stable in-process regardless of list order."""
    rows = [_fingerprint(source_row_key="b"), _fingerprint(source_row_key="a")]
    d1 = compute_content_hash(settings={}, row_fingerprints=rows)
    d2 = compute_content_hash(settings={}, row_fingerprints=list(reversed(rows)))
    assert d1 == d2
    assert [r["source_row_key"] for r in sort_row_fingerprints(rows)] == ["a", "b"]


def test_canonical_serialization_normalizes_types():
    """UUID/Decimal/float/null/bool normalize consistently for hashing."""
    from uuid import UUID

    assert canonicalize_json_value(UUID("4195bf94-daf4-429d-abec-2c5568929f4d")) == (
        "4195bf94-daf4-429d-abec-2c5568929f4d"
    )
    assert canonicalize_json_value(Decimal("1.50")) == "1.50"
    assert canonicalize_json_value(0.14) == "0.14"
    assert canonicalize_json_value(None) is None
    assert canonicalize_json_value(True) is True
    assert canonicalize_json_value({"b": 1, "a": [2, None]}) == {"b": 1, "a": [2, None]}


@pytest.mark.django_db
def test_database_reload_preserves_verification():
    """Reload after save must still verify under v2."""
    project = _project_with_ifc()
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=_session(),
        query=QUERY,
        model_name="HASH1 Reload",
        version_label="reload-v1",
    )
    pk = out["result"]["version"].pk
    reloaded = FiveDModelVersion.objects.get(pk=pk)
    assert verify_version_content_hash(reloaded) is True


@pytest.mark.django_db
def test_readiness_tamper_fails_verification():
    """Mutating frozen readiness under v2 fails integrity."""
    project = _project_with_ifc()
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=_session(),
        query=QUERY,
        model_name="HASH1 Ready Tamper",
        version_label="ready-tamper",
    )
    version = FiveDModelVersion.objects.get(pk=out["result"]["version"].pk)
    assert version.semantic_source_readiness_snapshot
    tampered = copy.deepcopy(version.semantic_source_readiness_snapshot)
    tampered["helper"] = "tampered-helper"
    FiveDModelVersion.objects.filter(pk=version.pk).update(
        semantic_source_readiness_snapshot=tampered
    )
    version.refresh_from_db()
    assessment = assess_version_content_hash(version)
    assert assessment.status == ContentHashStatus.FAILED_INTEGRITY
    assert verify_version_content_hash(version) is False


@pytest.mark.django_db
def test_frozen_row_tamper_fails_verification():
    """Mutating a covered frozen row field fails integrity."""
    project = _project_with_ifc()
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=_session(),
        query=QUERY,
        model_name="HASH1 Row Tamper",
        version_label="row-tamper",
    )
    version = FiveDModelVersion.objects.get(pk=out["result"]["version"].pk)
    row = version.rows.first()
    assert row is not None
    FiveDModelRow.objects.filter(pk=row.pk).update(total_quantity=(row.total_quantity or 0) + 99)
    version.refresh_from_db()
    assert assess_version_content_hash(version).status == ContentHashStatus.FAILED_INTEGRITY


@pytest.mark.django_db
def test_legacy_contract_routing_unverifiable_with_rows():
    """Pilot-shaped legacy rows without contract version are UNVERIFIABLE LEGACY."""
    version = FiveDModelVersionFactory(
        content_hash="a" * 64,
        content_hash_contract_version="",
        settings_snapshot={"contract_version": "fived-snapshot-f2-v1"},
        semantic_source_readiness_snapshot=None,
        row_count=2,
    )
    FiveDModelRowFactory(version=version, source_row_key="v1|type|IfcBeam|A|NetVolume")
    FiveDModelRowFactory(version=version, source_row_key="v1|type|IfcWall|B|NetVolume")
    assessment = assess_version_content_hash(version)
    assert assessment.status == ContentHashStatus.UNVERIFIABLE_LEGACY
    assert "prep_rows" in assessment.reason
    assert verify_version_content_hash(version) is False


@pytest.mark.django_db
def test_legacy_empty_row_snapshot_can_verify():
    """Order-independent legacy empty snapshots remain VERIFIED."""
    legacy = FiveDModelVersionFactory(
        version_label="legacy-empty-hash",
        semantic_source_readiness_snapshot=None,
        content_hash_contract_version="",
        settings_snapshot={"contract_version": "fived-snapshot-f2-v1"},
        content_hash="",
        row_count=0,
    )
    # Pre-HASH-1 formula path via shared builder without readiness.
    legacy.content_hash = compute_content_hash(
        settings=legacy.settings_snapshot,
        row_fingerprints=[],
        include_readiness=False,
    )
    legacy.save(update_fields=["content_hash"])
    assessment = assess_version_content_hash(legacy)
    assert assessment.status == ContentHashStatus.VERIFIED


@pytest.mark.django_db
def test_explicit_failed_integrity_when_v2_mismatch():
    """Known v2 contract with wrong stored hash is FAILED INTEGRITY."""
    version = FiveDModelVersionFactory(
        content_hash_contract_version=CONTENT_HASH_CONTRACT_V2,
        content_hash="0" * 64,
        settings_snapshot={"a": 1},
        semantic_source_readiness_snapshot=None,
        row_count=0,
    )
    assessment = assess_version_content_hash(version)
    assert assessment.status == ContentHashStatus.FAILED_INTEGRITY
    assert assessment.expected_hash is not None
    assert assessment.expected_hash != version.content_hash


@pytest.mark.django_db
def test_snapshot_service_content_hash_helper_matches_verify():
    """snapshot_service._content_hash aligns with verify after persist."""
    project = _project_with_ifc()
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=_session(),
        query=QUERY,
        model_name="HASH1 Helper Align",
        version_label="helper-align",
    )
    version = out["result"]["version"]
    assert version.content_hash == out["result"]["content_hash"]
    assert len(version.content_hash) == 64
    assert verify_version_content_hash(FiveDModelVersion.objects.get(pk=version.pk))


@pytest.mark.django_db
def test_readiness_aware_current_contract_differs_without_readiness():
    """Including readiness changes the v2 digest."""
    settings = {"x": 1}
    rows = [_fingerprint()]
    ready = {"contract_version": "sem4a_readiness_v1", "fields": [{"k": 1}]}
    with_r = _content_hash(settings, rows, semantic_source_readiness=ready)
    without = _content_hash(settings, rows, semantic_source_readiness=None)
    assert with_r != without
