# fived/tests/test_schema_quantity_insight_report_s3.py
"""5D-S3/S3d/S3e/S3f schema quantity insight report page tests."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.urls import reverse

from fived.models import FiveDDataModel, FiveDModelRow, FiveDModelVersion
from fived.services.schema_insight_screen_presentation import (
    build_schema_insight_screen_summary,
)
from fived.tests.factories import (
    FiveDModelRowFactory,
    FiveDModelVersionFactory,
)
from takeoff.services.quantity_prep_row_mapping import ORIGIN_MANUAL_SESSION_SCHEMA_NODE

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
    "earned_value",
    "payment_ready",
    "procurement_ready",
    "task_cost",
    "schedule_cost",
)

RAW_NON_CLAIM_FLAGS = (
    "not_boq",
    "not_qs_certified",
    "not_cost_estimate",
    "not_evm",
    "not_5d_readiness",
    "manual_session_is_weak_provenance",
    "schema_session_is_not_approved",
    "mixed_bases_are_not_coerced",
)


def _url(project_id, version_id) -> str:
    return reverse(
        "fived:schema_quantity_insight_report",
        kwargs={"pk": project_id, "version_id": version_id},
    )


def _main_without_help(html: str) -> str:
    """Strip help modal body so main-page assertions ignore modal copy."""
    start = html.find('id="schemaQuantityInsightHelpModal"')
    if start < 0:
        return html
    return html[:start]


@pytest.mark.django_db
def test_report_route_200_with_title_contract_and_sections(client):
    """Product screen: summary cards, friendly rollups, no defensive block."""
    version = FiveDModelVersionFactory(
        settings_snapshot={
            "schema_includes": {
                "classification_code": True,
                "package_boq_mapping": True,
                "work_package": True,
            }
        }
    )
    project = version.data_model.project
    FiveDModelRowFactory(
        version=version,
        source_row_key="mapped",
        classification_code="EL-DEMO-WALL",
        classification_origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        package_mapping="PKG-DEMO-STRUCTURE",
        package_mapping_origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        work_package="",
        missing_work_package=True,
        total_quantity=2.5,
        quantity_basis="NetVolume",
        unit_basis="m3",
        quantity_source="Qto_WallBaseQuantities",
        quantity_provenance={
            "mapping": {
                "classification": {
                    "origin": ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
                    "node_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "schema_key": "nbkch-demo-elements",
                    "label": "Demo Wall",
                },
                "package_mapping": {
                    "origin": ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
                    "node_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                    "schema_key": "nbkch-demo-packages",
                    "label": "Structure",
                },
            }
        },
    )
    FiveDModelRowFactory(
        version=version,
        source_row_key="unmapped",
        classification_code="",
        package_mapping="",
        work_package="",
        missing_classification=True,
        missing_package_mapping=True,
        missing_work_package=True,
        total_quantity=None,
        quantity_basis="",
        basis_unresolved=True,
        quantity_source="",
        missing_quantity_source=True,
    )

    client.force_login(project.owner)
    resp = client.get(_url(project.pk, version.pk))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    main = _main_without_help(html)

    assert "5D Schema Quantity Insight" in main
    assert "fived-schema-quantity-insight-s2-v1" in main
    assert "Read-only" in main
    assert "Snapshot-based" in main
    assert 'data-testid="s3-product-badges"' in main

    assert "Schema insight summary" in main
    assert 'data-testid="s3-insight-summary"' in main
    assert 'data-testid="s3-summary-total-rows"' in main
    assert 'data-testid="s3-summary-mapped-classification"' in main
    assert 'data-testid="s3-summary-unmapped-rows"' in main
    assert 'data-testid="s3-summary-basis-gaps"' in main
    assert 'data-testid="s3-schema-insight-scroll-root"' in html
    assert "Mapped" in main
    assert "Unmapped" in main
    assert "Needs review" in main or "rows need mapping" in main
    assert "Quantity basis gaps" in main
    assert 'data-testid="s3-plain-language"' in main
    assert "IFC-derived quantity rows" in main

    assert 'data-testid="s3-classification-rollup"' in main
    assert 'data-testid="s3-package-rollup"' in main
    assert 'data-testid="s3-work-package-rollup"' in main
    assert "Classification rollup" in main
    assert "Package rollup" in main
    assert "Work package rollup" in main
    assert 'data-testid="s3-classification-rollup-scroll"' in main
    assert 'data-testid="s3-classification-rollup-list"' in main
    assert "s3-rollup-scroll" in main
    assert 'data-testid="s3-gaps-summary"' in main
    assert "Gaps" in main
    assert "Basis" in main
    assert 'data-testid="s3-basis-unit-buckets"' in main
    assert 'data-testid="s3-issue-sample"' in main
    assert "Issue sample" in main
    assert 'data-testid="s3-user-value"' in main
    assert 'data-testid="s3-external-input-note"' in main
    assert 'data-testid="s3-help-pill"' in main
    assert "EL-DEMO-WALL" in main or "Demo Wall" in main
    assert "Details" in main

    assert "This report is not" not in main
    assert 'data-testid="s3-non-claims"' not in html
    assert "Not BOQ" not in main
    assert "Not cost estimate" not in main
    assert "No rate calculation" not in main
    for flag in RAW_NON_CLAIM_FLAGS:
        assert flag not in main

    assert 'data-testid="s3-help-boundary-note"' in html
    for flag in RAW_NON_CLAIM_FLAGS:
        assert flag not in html

    lower = main.lower()
    for term in FORBIDDEN_TERMS:
        assert term not in lower


def test_screen_summary_uses_payload_values_only():
    """Summary cards derive mapped/unmapped counts from insight payload."""
    insight = {
        "row_count": 50,
        "quantity_totals_by_classification": [
            {"is_unmapped": False, "row_count": 1, "code": "A"},
            {"is_unmapped": False, "row_count": 1, "code": "B"},
            {"is_unmapped": False, "row_count": 1, "code": "C"},
            {"is_unmapped": True, "row_count": 47, "code": ""},
        ],
        "quantity_totals_by_package": [
            {"is_unmapped": False, "row_count": 2},
            {"is_unmapped": False, "row_count": 1},
            {"is_unmapped": True, "row_count": 47},
        ],
        "quantity_totals_by_work_package": [
            {"is_unmapped": False, "row_count": 3},
            {"is_unmapped": True, "row_count": 47},
        ],
        "unmapped_counts": {"classification": 47, "package_mapping": 47, "work_package": 47},
        "quantity_basis_gap_counts": {
            "basis_unresolved_or_blank": 10,
            "missing_quantity_source": 5,
            "non_numeric_total": 2,
        },
    }
    summary = build_schema_insight_screen_summary(insight)
    assert summary["total_rows"] == 50
    assert summary["mapped_classification_groups"] == 3
    assert summary["mapped_classification_rows"] == 3
    assert summary["unmapped_classification_rows"] == 47
    assert summary["quantity_basis_source_gaps"] == 17
    assert summary["package_groups"] == 2
    assert summary["work_package_groups"] == 1
    assert "47 rows need mapping" in summary["unmapped_label"]


@pytest.mark.django_db
def test_report_does_not_write_db(client):
    """Opening the report does not create/update/delete fived models."""
    version = FiveDModelVersionFactory()
    project = version.data_model.project
    FiveDModelRowFactory(version=version, source_row_key="r1", total_quantity=1.0)
    before = {
        "models": FiveDDataModel.objects.count(),
        "versions": FiveDModelVersion.objects.count(),
        "rows": FiveDModelRow.objects.count(),
        "hash": version.content_hash,
    }
    client.force_login(project.owner)
    assert client.get(_url(project.pk, version.pk)).status_code == 200
    version.refresh_from_db()
    assert FiveDDataModel.objects.count() == before["models"]
    assert FiveDModelVersion.objects.count() == before["versions"]
    assert FiveDModelRow.objects.count() == before["rows"]
    assert version.content_hash == before["hash"]


@pytest.mark.django_db
def test_missing_version_404(client):
    """Unknown version id returns 404 for an accessible project."""
    version = FiveDModelVersionFactory()
    project = version.data_model.project
    client.force_login(project.owner)
    missing = uuid.uuid4()
    resp = client.get(_url(project.pk, missing))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_version_from_other_project_404(client):
    """Version belonging to another project is not accessible via this project URL."""
    version_a = FiveDModelVersionFactory()
    version_b = FiveDModelVersionFactory()
    project_a = version_a.data_model.project
    client.force_login(project_a.owner)
    resp = client.get(_url(project_a.pk, version_b.pk))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_anonymous_redirects_to_login(client):
    """Anonymous users are redirected to login."""
    version = FiveDModelVersionFactory()
    project = version.data_model.project
    resp = client.get(_url(project.pk, version.pk))
    assert resp.status_code in (302, 401)
    if resp.status_code == 302:
        assert "/login" in resp.url


def test_view_source_has_no_forbidden_dependencies():
    """S3 view module must not depend on QTO/export/writeback/runtime."""
    src = Path(__file__).resolve().parents[1] / "views.py"
    text = src.read_text(encoding="utf-8")
    for token in (
        "QTOCache",
        "QTOExportView",
        "build_qty_prep_session_ui",
        "FiveDPrepSnapshotService",
        "from writeback",
        "import writeback",
        "from takeoff",
        "import takeoff",
        "earned_value",
        "schedule_cost",
    ):
        assert token not in text
