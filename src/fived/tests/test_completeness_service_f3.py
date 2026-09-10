# fived/tests/test_completeness_service_f3.py
"""5D-F3 Completeness Review service tests.

Read-only review of frozen F2 rows — no Quantities rebuild, QTOCache, writeback,
rates/cost/BOQ/EVM, or readiness claims.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from fived.models import FiveDDataModel, FiveDModelRow, FiveDModelVersion
from fived.services import completeness_service as completeness_mod
from fived.services.completeness_service import (
    CONTRACT_VERSION_F3,
    FiveDCompletenessService,
)
from fived.tests.factories import (
    FiveDModelRowFactory,
    FiveDModelVersionFactory,
)

FORBIDDEN_TERMS = (
    "boq_ready",
    "qs_approved",
    "five_d_ready",
    "estimate_ready",
    "approved_cost",
    "certified",
    "earned_value",
    "payment_ready",
    "procurement_ready",
    "unit_rate",
    "extended_cost",
    "total_cost",
    "estimated_cost",
)

SETTINGS_ALL_INCLUDED = {
    "schema_includes": {
        "classification_code": True,
        "package_boq_mapping": True,
        "work_package": True,
        "type_name": True,
    },
    "source_mappings": {
        "classification_code": "manual_field",
        "package_boq_mapping": "not_mapped",
        "work_package": "not_mapped",
    },
    "basis_rules": {"IfcWall": "NetVolume"},
}


def _walk_keys(obj: object) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            out.extend(_walk_keys(v))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_walk_keys(item))
    return out


def _assert_no_forbidden(payload: dict) -> None:
    """Forbidden claim keys must not appear (substring match only on keys).

    Negation flags like ``not_qs_certified`` are allowed; do not substring-scan
    the full JSON blob for tokens such as ``certified``.
    """
    status_counts = payload.get("status_counts") or {}
    scrubbed = dict(payload)
    scrubbed["status_counts"] = {
        k: v for k, v in status_counts.items() if k != "structurally_ready"
    }
    keys = set(_walk_keys(scrubbed))
    for term in FORBIDDEN_TERMS:
        assert term not in keys
    assert "structurally_ready" not in keys


@pytest.mark.django_db
def test_empty_version_review():
    """Empty version returns zero counts, non_claims, no errors."""
    version = FiveDModelVersionFactory(
        settings_snapshot=SETTINGS_ALL_INCLUDED,
        row_count=0,
    )
    review = FiveDCompletenessService().build_version_review(version)
    assert review["contract_version"] == CONTRACT_VERSION_F3
    assert review["row_count"] == 0
    assert review["missing_counts"]["quantity_basis"] == 0
    assert review["categories"]["no_open_structural_gaps"] == 0
    assert review["issue_register"] == []
    assert review["non_claims"]["not_boq"] is True
    assert review["non_claims"]["not_5d_readiness"] is True
    assert review["generated_at"]
    _assert_no_forbidden(review)


@pytest.mark.django_db
def test_mixed_incomplete_rows_and_issue_register():
    """Missing quantity and included mapping slots are counted with issue labels."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    FiveDModelRowFactory(
        version=version,
        source_row_key="a-wall",
        ifc_class="IfcWall",
        type_name="W1",
        quantity_basis="",
        basis_unresolved=True,
        missing_quantity_source=True,
        classification_code="",
        missing_classification=True,
        package_mapping="",
        missing_package_mapping=True,
        work_package="",
        missing_work_package=True,
        status=FiveDModelRow.Status.INCOMPLETE,
    )
    FiveDModelRowFactory(
        version=version,
        source_row_key="b-beam",
        ifc_class="IfcBeam",
        type_name="B1",
        quantity_basis="NetVolume",
        basis_unresolved=False,
        missing_quantity_source=False,
        classification_code="CL-1",
        missing_classification=False,
        classification_origin="manual_session",
        classification_source_intent="manual_field",
        package_mapping="PKG",
        missing_package_mapping=False,
        work_package="WP",
        missing_work_package=False,
        status=FiveDModelRow.Status.STRUCTURALLY_READY,
    )
    review = FiveDCompletenessService().build_version_review(version)
    assert review["row_count"] == 2
    assert review["missing_counts"]["quantity_basis"] == 1
    assert review["missing_counts"]["quantity_source"] == 1
    assert review["missing_counts"]["classification"] == 1
    assert review["missing_counts"]["package_mapping"] == 1
    assert review["missing_counts"]["work_package"] == 1
    assert review["status_counts"].get("incomplete") == 1
    assert review["status_counts"].get("structurally_ready") == 1
    assert len(review["issue_register"]) == 1
    issue = review["issue_register"][0]
    assert issue["source_row_key"] == "a-wall"
    assert "quantity_basis_unresolved" in issue["issues"]
    assert "selected_quantity_source_missing" in issue["issues"]
    assert "classification_missing" in issue["issues"]
    assert "package_mapping_missing" in issue["issues"]
    assert "work_package_missing" in issue["issues"]
    _assert_no_forbidden(review)


@pytest.mark.django_db
def test_all_clear_structural_rows():
    """All-clear rows: no_open_structural_gaps equals row count; no claim terms."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    for i in range(3):
        FiveDModelRowFactory(
            version=version,
            source_row_key=f"ok-{i}",
            ifc_class="IfcWall",
            quantity_basis="NetVolume",
            basis_unresolved=False,
            missing_quantity_source=False,
            classification_code=f"C-{i}",
            missing_classification=False,
            package_mapping=f"P-{i}",
            missing_package_mapping=False,
            work_package=f"W-{i}",
            missing_work_package=False,
            status=FiveDModelRow.Status.STRUCTURALLY_READY,
        )
    review = FiveDCompletenessService().build_version_review(version)
    assert review["row_count"] == 3
    assert review["categories"]["no_open_structural_gaps"] == 3
    assert review["categories"]["quantity_evidence_complete"] == 3
    assert review["categories"]["mapping_slots_complete"] == 3
    assert review["issue_register"] == []
    assert review["non_claims"]["not_boq"] is True
    _assert_no_forbidden(review)


@pytest.mark.django_db
def test_schema_includes_gating_excludes_slots():
    """Excluded classification/package/work slots are not counted as missing."""
    settings = {
        "schema_includes": {
            "classification_code": False,
            "package_boq_mapping": False,
            "work_package": True,
            "type_name": True,
        },
        "source_mappings": {},
    }
    version = FiveDModelVersionFactory(settings_snapshot=settings)
    FiveDModelRowFactory(
        version=version,
        source_row_key="gated",
        quantity_basis="NetVolume",
        basis_unresolved=False,
        missing_quantity_source=False,
        classification_code="",
        missing_classification=True,
        package_mapping="",
        missing_package_mapping=True,
        work_package="",
        missing_work_package=True,
        status=FiveDModelRow.Status.INCOMPLETE,
    )
    review = FiveDCompletenessService().build_version_review(version)
    assert review["missing_counts"]["classification"] == 0
    assert review["missing_counts"]["package_mapping"] == 0
    assert review["missing_counts"]["work_package"] == 1
    assert review["slot_coverage"]["classification"]["included"] is False
    assert review["slot_coverage"]["package_mapping"]["included"] is False
    assert review["slot_coverage"]["work_package"]["included"] is True
    issues = review["issue_register"][0]["issues"]
    assert "classification_missing" not in issues
    assert "package_mapping_missing" not in issues
    assert "work_package_missing" in issues


@pytest.mark.django_db
def test_manual_session_is_filled_but_weak():
    """manual_session/manual_field counts as filled but weak — not official."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    FiveDModelRowFactory(
        version=version,
        source_row_key="weak",
        quantity_basis="NetVolume",
        basis_unresolved=False,
        missing_quantity_source=False,
        classification_code="CL-WEAK",
        missing_classification=False,
        classification_origin="manual_session",
        classification_source_intent="manual_field",
        package_mapping="PKG",
        missing_package_mapping=False,
        package_mapping_origin="manual_session",
        package_mapping_source_intent="manual_field",
        work_package="WP",
        missing_work_package=False,
        work_package_origin="manual_session",
        work_package_source_intent="manual_field",
        status=FiveDModelRow.Status.STRUCTURALLY_READY,
    )
    review = FiveDCompletenessService().build_version_review(version)
    assert review["missing_counts"]["classification"] == 0
    assert review["slot_coverage"]["classification"]["filled"] == 1
    assert review["slot_coverage"]["classification"]["weak_filled"] == 1
    assert review["provenance_counts"]["manual_session"] >= 1
    assert review["categories"]["no_open_structural_gaps"] == 1
    assert review["non_claims"]["manual_session_is_weak_provenance"] is True
    keys = set(_walk_keys(review))
    assert "official" not in keys
    assert "certified" not in keys
    _assert_no_forbidden(review)


@pytest.mark.django_db
def test_session_review_informational_not_structural():
    """Reviewed/unreviewed counted separately; blank review is not a structural gap."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    FiveDModelRowFactory(
        version=version,
        source_row_key="reviewed",
        quantity_basis="NetVolume",
        basis_unresolved=False,
        missing_quantity_source=False,
        classification_code="C",
        missing_classification=False,
        package_mapping="P",
        missing_package_mapping=False,
        work_package="W",
        missing_work_package=False,
        session_review_status="reviewing",
        status=FiveDModelRow.Status.STRUCTURALLY_READY,
    )
    FiveDModelRowFactory(
        version=version,
        source_row_key="unreviewed",
        quantity_basis="NetVolume",
        basis_unresolved=False,
        missing_quantity_source=False,
        classification_code="C2",
        missing_classification=False,
        package_mapping="P2",
        missing_package_mapping=False,
        work_package="W2",
        missing_work_package=False,
        session_review_status="",
        status=FiveDModelRow.Status.STRUCTURALLY_READY,
    )
    review = FiveDCompletenessService().build_version_review(version)
    assert review["categories"]["session_reviewed"] == 1
    assert review["categories"]["session_unreviewed"] == 1
    assert review["categories"]["no_open_structural_gaps"] == 2
    assert review["issue_register"] == []


@pytest.mark.django_db
def test_no_db_writes_on_review():
    """build_version_review does not create/update/delete snapshot models."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    FiveDModelRowFactory(
        version=version,
        source_row_key="stable",
        quantity_basis="NetVolume",
        basis_unresolved=False,
        missing_quantity_source=False,
        classification_code="C",
        missing_classification=False,
        package_mapping="P",
        missing_package_mapping=False,
        work_package="W",
        missing_work_package=False,
    )
    before = {
        "models": FiveDDataModel.objects.count(),
        "versions": FiveDModelVersion.objects.count(),
        "rows": FiveDModelRow.objects.count(),
        "version_updated": version.updated_at,
        "row_updated": version.rows.get().updated_at,
        "content_hash": version.content_hash,
    }
    FiveDCompletenessService().build_version_review(version)
    version.refresh_from_db()
    row = version.rows.get()
    assert FiveDDataModel.objects.count() == before["models"]
    assert FiveDModelVersion.objects.count() == before["versions"]
    assert FiveDModelRow.objects.count() == before["rows"]
    assert version.updated_at == before["version_updated"]
    assert row.updated_at == before["row_updated"]
    assert version.content_hash == before["content_hash"]


def test_no_forbidden_dependencies_in_module_source():
    """Service module must not import takeoff cache/export/runtime, writeback, or EVM."""
    src = Path(completeness_mod.__file__).read_text(encoding="utf-8")
    import_lines = [
        ln.strip()
        for ln in src.splitlines()
        if ln.strip().startswith("import ") or ln.strip().startswith("from ")
    ]
    joined = "\n".join(import_lines)
    for snip in (
        "build_qty_prep_session_ui",
        "QTOCache",
        "QTOExportView",
        "writeback",
        "ModificationProposal",
        "EarnedValue",
        "takeoff",
    ):
        assert snip not in joined
    sig = inspect.signature(FiveDCompletenessService.build_version_review)
    assert "version" in sig.parameters


@pytest.mark.django_db
def test_settings_ref_and_metadata_present():
    """Version/data_model/project metadata and settings_ref subset are present."""
    version = FiveDModelVersionFactory(
        settings_snapshot={
            **SETTINGS_ALL_INCLUDED,
            "prep_config": {"id": "cfg-1", "name": "Draft"},
        },
        content_hash="abc123",
        version_label="v-meta",
    )
    review = FiveDCompletenessService().build_version_review(version)
    assert review["version"]["version_label"] == "v-meta"
    assert review["version"]["content_hash"] == "abc123"
    assert review["data_model"]["name"]
    assert review["project"]["id"]
    assert "schema_includes" in review["settings_ref"]
    assert "source_mappings" in review["settings_ref"]
    assert "basis_rules" in review["settings_ref"]
    assert review["settings_ref"]["prep_config"]["name"] == "Draft"
    assert review["boundary"].get("not_boq") is True
