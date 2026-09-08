# fived/tests/test_schema_quantity_insight_report_s3.py
"""5D Quantity Review report page tests (S3–S3g)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.urls import reverse

from fived.models import FiveDDataModel, FiveDModelRow, FiveDModelVersion
from fived.services.schema_insight_screen_presentation import (
    build_schema_insight_screen_summary,
    resolve_unit_display,
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

MAIN_FORBIDDEN_STRINGS = (
    "model volume units",
    "blank basis",
    "blank unit",
    "manual_session_schema_node",
    "manual_field",
    "fived-schema-quantity-insight-s2-v1",
    "This report is not",
    "BOQ-ready",
    "schedule cost loading",
)


def _url(project_id, version_id) -> str:
    return reverse(
        "fived:schema_quantity_insight_report",
        kwargs={"pk": project_id, "version_id": version_id},
    )


def _main_without_advanced(html: str) -> str:
    """Strip Advanced details + help modal from main-page assertions."""
    cut = html.find('data-testid="s3g-advanced-details"')
    if cut < 0:
        cut = html.find('id="schemaQuantityInsightHelpModal"')
    if cut < 0:
        return html
    return html[:cut]


@pytest.mark.django_db
def test_report_route_200_with_title_contract_and_sections(client):
    """v4 dashboard: summary, tabs, attention panel, no technical dump on main."""
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
        unit_basis="model volume units",
        quantity_source="Qto_WallBaseQuantities",
        quantity_provenance={
            "mapping": {
                "classification": {
                    "origin": ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
                    "node_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "schema_key": "nbkch-demo-elements",
                    "label": "Wall elements",
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
    main = _main_without_advanced(html)

    assert "5D Quantity Review" in main
    assert 'data-testid="s3-report-title"' in main
    assert "Schema insight summary" in main
    assert 'data-testid="s3-insight-summary"' in main
    assert 'data-testid="s3-summary-schema-coverage"' in main
    assert 'data-testid="s3-summary-quantity-readiness"' in main
    assert 'data-testid="s3-summary-mapped-netvolume"' in main
    assert "Mapped NetVolume" in main
    assert 'data-testid="s3-summary-recommended-action"' in main
    assert 'data-testid="s3-coverage-bar"' in main
    assert 'data-testid="s3g-rollup-tabs"' in main
    assert 'data-testid="s3g-tab-classification"' in main
    assert 'data-testid="s3g-tab-package"' in main
    assert 'data-testid="s3g-tab-work-package"' in main
    assert 'aria-selected="true"' in main
    assert 'data-testid="s3-classification-rollup"' in main
    assert "Classification rollup" in main
    assert "Package rollup" in html  # present in inactive tab pane
    assert "Work package rollup" in html
    assert "Wall elements" in main
    assert "EL-DEMO-WALL" in main
    assert main.find("Wall elements") < main.find("EL-DEMO-WALL")
    assert "Unit not resolved" in main
    assert "Mapped" in main
    assert 'data-testid="s3g-attention-panel"' in main
    assert "What needs attention" in main
    assert 'data-testid="s3g-later-inputs"' in main
    assert "Later inputs" in main
    assert 'data-testid="s3g-advanced-details"' in html
    assert "Advanced details" in html
    assert 'data-testid="s3-schema-insight-scroll-root"' in html
    assert "Snapshot-based" in main
    assert "Read-only" in main

    for bad in MAIN_FORBIDDEN_STRINGS:
        assert bad not in main
    for flag in RAW_NON_CLAIM_FLAGS:
        assert flag not in main
    for term in FORBIDDEN_TERMS:
        assert term not in main.lower()
    assert "EVM" not in main

    # Contract id only in Advanced details, not main chrome.
    assert "fived-schema-quantity-insight-s2-v1" in html
    assert 'data-testid="s3-contract-version"' in html
    assert 'data-testid="s3-help-boundary-note"' in html


def test_screen_summary_uses_payload_values_only():
    """Summary cards derive coverage/readiness/NetVolume from insight payload."""
    insight = {
        "row_count": 50,
        "quantity_totals_by_classification": [
            {
                "is_unmapped": False,
                "row_count": 1,
                "code": "EL-DEMO-WALL",
                "quantity_buckets": [
                    {
                        "quantity_basis": "NetVolume",
                        "unit_basis": "model volume units",
                        "total_sum": 3409.55,
                        "row_count": 1,
                    }
                ],
            },
            {
                "is_unmapped": False,
                "row_count": 1,
                "code": "EL-DEMO-BEAM",
                "quantity_buckets": [
                    {
                        "quantity_basis": "NetVolume",
                        "unit_basis": "model volume units",
                        "total_sum": 960.29,
                        "row_count": 1,
                    }
                ],
            },
            {
                "is_unmapped": False,
                "row_count": 1,
                "code": "EL-DEMO-COLUMN",
                "quantity_buckets": [
                    {
                        "quantity_basis": "NetVolume",
                        "unit_basis": "model volume units",
                        "total_sum": 10.57,
                        "row_count": 1,
                    }
                ],
            },
            {"is_unmapped": True, "row_count": 47, "code": "", "quantity_buckets": []},
        ],
        "quantity_totals_by_package": [
            {"is_unmapped": False, "row_count": 2, "quantity_buckets": []},
            {"is_unmapped": False, "row_count": 1, "quantity_buckets": []},
            {"is_unmapped": True, "row_count": 47, "quantity_buckets": []},
        ],
        "quantity_totals_by_work_package": [
            {"is_unmapped": False, "row_count": 3, "quantity_buckets": []},
            {"is_unmapped": True, "row_count": 47, "quantity_buckets": []},
        ],
        "unmapped_counts": {"classification": 47, "package_mapping": 47, "work_package": 47},
        "basis_unit_buckets": [
            {
                "quantity_basis": "",
                "unit_basis": "",
                "row_count": 11,
                "total_sum": 0,
                "non_numeric_count": 11,
            },
            {
                "quantity_basis": "NetVolume",
                "unit_basis": "model volume units",
                "row_count": 39,
                "total_sum": 9846.38,
                "non_numeric_count": 0,
            },
        ],
        "quantity_basis_gap_counts": {"basis_unresolved_or_blank": 11},
    }
    summary = build_schema_insight_screen_summary(insight)
    assert summary["total_rows"] == 50
    assert summary["mapped_classification_rows"] == 3
    assert summary["unmapped_classification_rows"] == 47
    assert summary["usable_quantity_rows"] == 39
    assert summary["need_basis_source_rows"] == 11
    assert summary["mapped_netvolume_display"] == "4,380.41"
    assert summary["mapped_netvolume_unit_status"] == "Unit not resolved"
    assert summary["recommended_action_value"] == "Map 47"
    assert summary["coverage_percent"] == 6
    assert summary["needs_mapping_percent"] == 94
    assert summary["package_groups"] == 2
    assert summary["work_package_groups"] == 1


def test_resolve_unit_display_hides_model_volume_units():
    """Product unit label never surfaces model volume units."""
    assert resolve_unit_display("model volume units")["label"] == "Unit not resolved"
    assert resolve_unit_display("m3")["label"] == "m³"
    assert resolve_unit_display("m³")["resolved"] is True


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
