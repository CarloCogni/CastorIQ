# fived/tests/test_schema_quantity_insight_service_s2.py
"""5D-S2 schema-driven quantity insight service tests.

Read-only insight over frozen F2 rows — no Quantities rebuild, QTOCache,
writeback, rates/cost/BOQ/EVM, or readiness claims.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fived.models import FiveDDataModel, FiveDModelRow, FiveDModelVersion
from fived.services import schema_quantity_insight_service as insight_mod
from fived.services.schema_quantity_insight_service import (
    CONTRACT_VERSION_S2,
    FiveDSchemaQuantityInsightService,
)
from fived.tests.factories import (
    FiveDModelRowFactory,
    FiveDModelVersionFactory,
)
from takeoff.services.quantity_prep_row_mapping import (
    ORIGIN_MANUAL_SESSION,
    ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
)

FORBIDDEN_TERMS = (
    "unit_rate",
    "extended_cost",
    "total_cost",
    "estimated_cost",
    "boq_ready",
    "qs_approved",
    "five_d_ready",
    "estimate_ready",
    "approved_cost",
    "certified",
    "earned_value",
    "payment_ready",
    "procurement_ready",
)

FORBIDDEN_SOURCE_TOKENS = (
    "QTOCache",
    "QTOExportView",
    "build_qty_prep_session_ui",
    "FiveDPrepSnapshotService",
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
        "package_boq_mapping": "manual_field",
        "work_package": "manual_field",
    },
}

SETTINGS_CLASS_ONLY = {
    "schema_includes": {
        "classification_code": True,
        "package_boq_mapping": False,
        "work_package": False,
    },
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
    keys = set(_walk_keys(payload))
    for term in FORBIDDEN_TERMS:
        assert term not in keys
    blob = json.dumps(payload, default=str).lower()
    for term in FORBIDDEN_TERMS:
        # Allow negation phrases that contain substrings carefully:
        # scan whole JSON only for exact forbidden claim tokens as standalone keys
        # already covered by key walk; also reject cost field names as values.
        if term in {
            "unit_rate",
            "extended_cost",
            "total_cost",
            "estimated_cost",
            "boq_ready",
            "qs_approved",
            "five_d_ready",
            "estimate_ready",
            "approved_cost",
            "earned_value",
            "payment_ready",
            "procurement_ready",
        }:
            assert f'"{term}"' not in blob
            assert f"'{term}'" not in blob


def _mapping(
    *,
    origin: str,
    node_id: str = "",
    schema_key: str = "",
    label: str = "",
) -> dict:
    meta: dict[str, str] = {"origin": origin}
    if node_id:
        meta["node_id"] = node_id
    if schema_key:
        meta["schema_key"] = schema_key
    if label:
        meta["label"] = label
    return meta


@pytest.mark.django_db
def test_empty_version_returns_valid_empty_insight():
    """Empty version returns contract, zero rows, empty rollups, non_claims."""
    version = FiveDModelVersionFactory(
        settings_snapshot=SETTINGS_ALL_INCLUDED,
        row_count=0,
    )
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    assert insight["contract_version"] == CONTRACT_VERSION_S2
    assert insight["row_count"] == 0
    assert insight["quantity_totals_by_classification"] == []
    assert insight["quantity_totals_by_package"] == []
    assert insight["quantity_totals_by_work_package"] == []
    assert insight["unmapped_counts"] == {
        "classification": 0,
        "package_mapping": 0,
        "work_package": 0,
    }
    assert insight["issue_sample"] == []
    assert insight["non_claims"]["not_boq"] is True
    assert insight["non_claims"]["not_cost_calculation"] is True
    assert insight["non_claims"]["mixed_bases_are_not_coerced"] is True
    assert insight["non_claims"]["schema_session_is_not_approved"] is True
    assert insight["version"]["id"] == str(version.pk)
    assert insight["data_model"]["name"] == version.data_model.name
    assert insight["project"]["id"] == str(version.data_model.project_id)
    assert "generated_at" in insight
    _assert_no_forbidden(insight)


@pytest.mark.django_db
def test_grouping_with_schema_metadata_by_node_id():
    """Schema provenance groups classification/package by node_id; work emitted."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    class_nid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    pkg_nid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    wp_nid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    FiveDModelRowFactory(
        version=version,
        source_row_key="r1",
        quantity_basis="NetVolume",
        unit_basis="m3",
        quantity_source="Qto_WallBaseQuantities",
        total_quantity=10.0,
        classification_code="EL-DEMO-WALL",
        classification_origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        package_mapping="PKG-DEMO-STRUCTURE",
        package_mapping_origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        work_package="WP-DEMO-BASEMENT-Z1",
        work_package_origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        missing_classification=False,
        missing_package_mapping=False,
        missing_work_package=False,
        quantity_provenance={
            "mapping": {
                "classification": _mapping(
                    origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
                    node_id=class_nid,
                    schema_key="nbkch-demo-elements",
                    label="Demo Wall",
                ),
                "package_mapping": _mapping(
                    origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
                    node_id=pkg_nid,
                    schema_key="nbkch-demo-packages",
                    label="Structure Package",
                ),
                "work_package": _mapping(
                    origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
                    node_id=wp_nid,
                    schema_key="nbkch-demo-work",
                    label="Basement Z1",
                ),
            }
        },
    )
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    assert insight["row_count"] == 1
    class_groups = insight["quantity_totals_by_classification"]
    assert len(class_groups) == 1
    assert class_groups[0]["group_key"] == f"node:{class_nid}"
    assert class_groups[0]["schema_key"] == "nbkch-demo-elements"
    assert class_groups[0]["label"] == "Demo Wall"
    assert class_groups[0]["is_unmapped"] is False
    assert class_groups[0]["quantity_buckets"][0]["total_sum"] == 10.0

    pkg_groups = insight["quantity_totals_by_package"]
    assert pkg_groups[0]["group_key"] == f"node:{pkg_nid}"
    assert pkg_groups[0]["schema_key"] == "nbkch-demo-packages"

    wp_groups = insight["quantity_totals_by_work_package"]
    assert len(wp_groups) == 1
    assert wp_groups[0]["group_key"] == f"node:{wp_nid}"
    assert insight["provenance_counts"]["manual_session_schema_node"] == 3


@pytest.mark.django_db
def test_fallback_code_and_unmapped_buckets():
    """Missing metadata falls back to code:<code>; blank code → unmapped."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    FiveDModelRowFactory(
        version=version,
        source_row_key="fallback",
        classification_code="CL-FREE",
        classification_origin=ORIGIN_MANUAL_SESSION,
        package_mapping="",
        work_package="",
        missing_classification=False,
        missing_package_mapping=True,
        missing_work_package=True,
        total_quantity=2.0,
        quantity_provenance={},
    )
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    class_groups = insight["quantity_totals_by_classification"]
    assert class_groups[0]["group_key"] == "code:CL-FREE"
    assert class_groups[0]["is_unmapped"] is False

    pkg_groups = insight["quantity_totals_by_package"]
    assert any(g["group_key"] == "unmapped" and g["is_unmapped"] for g in pkg_groups)
    wp_groups = insight["quantity_totals_by_work_package"]
    assert any(g["group_key"] == "unmapped" and g["is_unmapped"] for g in wp_groups)
    assert insight["unmapped_counts"]["package_mapping"] == 1
    assert insight["unmapped_counts"]["work_package"] == 1
    assert insight["unmapped_counts"]["classification"] == 0


@pytest.mark.django_db
def test_mixed_basis_unit_source_split_not_coerced():
    """Mixed bases/units/sources stay in separate buckets with no coerced total."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    FiveDModelRowFactory(
        version=version,
        source_row_key="vol",
        classification_code="SAME",
        quantity_basis="NetVolume",
        unit_basis="m3",
        quantity_source="Qto_A",
        total_quantity=5.0,
    )
    FiveDModelRowFactory(
        version=version,
        source_row_key="area",
        classification_code="SAME",
        quantity_basis="NetArea",
        unit_basis="m2",
        quantity_source="Qto_B",
        total_quantity=3.0,
    )
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    group = insight["quantity_totals_by_classification"][0]
    assert group["group_key"] == "code:SAME"
    assert group["row_count"] == 2
    assert len(group["quantity_buckets"]) == 2
    sums = sorted(b["total_sum"] for b in group["quantity_buckets"])
    assert sums == [3.0, 5.0]
    assert "total_quantity" not in group
    assert len(insight["basis_unit_buckets"]) == 2


@pytest.mark.django_db
def test_numeric_totals_and_non_numeric_gaps():
    """Numeric totals sum; null totals count as non_numeric_total."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    FiveDModelRowFactory(
        version=version,
        source_row_key="ok",
        classification_code="A",
        total_quantity=4.5,
        quantity_basis="NetVolume",
        unit_basis="m3",
        quantity_source="src",
    )
    FiveDModelRowFactory(
        version=version,
        source_row_key="null-total",
        classification_code="A",
        total_quantity=None,
        quantity_basis="NetVolume",
        unit_basis="m3",
        quantity_source="src",
    )
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    group = insight["quantity_totals_by_classification"][0]
    bucket = group["quantity_buckets"][0]
    assert bucket["total_sum"] == 4.5
    assert bucket["non_numeric_count"] == 1
    assert bucket["row_count"] == 2
    assert insight["quantity_basis_gap_counts"]["non_numeric_total"] == 1
    assert _numeric_total_helper(True) is None


def _numeric_total_helper(value: object) -> float | None:
    return insight_mod._numeric_total(value)


@pytest.mark.django_db
def test_bool_total_treated_as_non_numeric():
    """Bool must not be summed as 1.0/0.0."""
    assert insight_mod._numeric_total(True) is None
    assert insight_mod._numeric_total(False) is None
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    # ORM FloatField won't store bool as bool after save; exercise helper + null path
    row = FiveDModelRowFactory(
        version=version,
        source_row_key="blank-basis",
        classification_code="B",
        total_quantity=None,
        quantity_basis="",
        basis_unresolved=True,
        quantity_source="",
        missing_quantity_source=True,
    )
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    assert insight["quantity_basis_gap_counts"]["basis_unresolved_or_blank"] == 1
    assert insight["quantity_basis_gap_counts"]["missing_quantity_source"] == 1
    assert insight["quantity_basis_gap_counts"]["non_numeric_total"] == 1
    assert any("quantity_basis_gap" in item["issues"] for item in insight["issue_sample"])
    assert row.pk is not None


@pytest.mark.django_db
def test_missing_mapping_counts_respect_schema_includes():
    """Excluded slots via schema_includes are not counted as missing."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_CLASS_ONLY)
    FiveDModelRowFactory(
        version=version,
        source_row_key="gaps",
        classification_code="",
        package_mapping="",
        work_package="",
        missing_classification=True,
        missing_package_mapping=True,
        missing_work_package=True,
        total_quantity=1.0,
        quantity_basis="NetVolume",
        quantity_source="src",
    )
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    assert insight["missing_mapping_counts"]["classification"] == 1
    assert insight["missing_mapping_counts"]["package_mapping"] == 0
    assert insight["missing_mapping_counts"]["work_package"] == 0
    assert insight["unmapped_counts"]["classification"] == 1
    assert insight["unmapped_counts"]["package_mapping"] == 1


@pytest.mark.django_db
def test_provenance_counts_schema_and_free_text():
    """manual_session_schema_node and manual_session buckets are counted."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    FiveDModelRowFactory(
        version=version,
        source_row_key="schema",
        classification_code="EL-1",
        classification_origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        package_mapping="PKG-1",
        package_mapping_origin=ORIGIN_MANUAL_SESSION,
        work_package="",
        work_package_origin="",
        quantity_provenance={
            "mapping": {
                "classification": _mapping(
                    origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
                    node_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
                    schema_key="demo-el",
                    label="El",
                ),
                "package_mapping": _mapping(origin=ORIGIN_MANUAL_SESSION),
            }
        },
        total_quantity=1.0,
        quantity_source="src",
    )
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    assert insight["provenance_counts"]["manual_session_schema_node"] >= 1
    assert insight["provenance_counts"]["manual_session"] >= 1
    assert insight["provenance_counts"]["empty"] >= 1
    assert insight["non_claims"]["schema_session_is_not_approved"] is True
    assert insight["non_claims"]["manual_session_is_weak_provenance"] is True


@pytest.mark.django_db
def test_no_db_writes():
    """Insight computation does not create/update/delete fived models."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    FiveDModelRowFactory(version=version, source_row_key="stable", total_quantity=1.0)
    before = {
        "models": FiveDDataModel.objects.count(),
        "versions": FiveDModelVersion.objects.count(),
        "rows": FiveDModelRow.objects.count(),
    }
    content_hash = version.content_hash
    row_count = version.row_count
    FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    version.refresh_from_db()
    assert FiveDDataModel.objects.count() == before["models"]
    assert FiveDModelVersion.objects.count() == before["versions"]
    assert FiveDModelRow.objects.count() == before["rows"]
    assert version.content_hash == content_hash
    assert version.row_count == row_count


def test_service_source_has_no_forbidden_dependencies():
    """S2 module source must not reference forbidden runtimes."""
    src = Path(insight_mod.__file__).read_text(encoding="utf-8")
    for token in FORBIDDEN_SOURCE_TOKENS:
        assert token not in src
    assert "from takeoff" not in src
    assert "import takeoff" not in src
    assert "from writeback" not in src
    assert "import writeback" not in src
    assert "from chat" not in src
    assert "import chat" not in src
    assert "earned_value" not in src
    assert "schedule_cost" not in src
    assert "scheduling" not in src.lower()


@pytest.mark.django_db
def test_forbidden_keys_absent_in_json_payload():
    """Serialized insight must not contain forbidden cost/BOQ/EVM claim keys."""
    version = FiveDModelVersionFactory(settings_snapshot=SETTINGS_ALL_INCLUDED)
    FiveDModelRowFactory(
        version=version,
        source_row_key="ok",
        classification_code="X",
        total_quantity=1.0,
        quantity_source="src",
    )
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    _assert_no_forbidden(insight)
    blob = json.dumps(insight, default=str)
    for term in (
        "unit_rate",
        "extended_cost",
        "total_cost",
        "estimated_cost",
        "boq_ready",
        "qs_approved",
        "five_d_ready",
        "estimate_ready",
        "approved_cost",
        "earned_value",
        "payment_ready",
        "procurement_ready",
    ):
        assert term not in blob
