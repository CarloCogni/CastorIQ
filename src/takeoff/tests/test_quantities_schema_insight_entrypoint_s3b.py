# takeoff/tests/test_quantities_schema_insight_entrypoint_s3b.py
"""5D-S3b/S3c/S3d Quantities entry point to Schema Quantity Insight report."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from fived.models import FiveDDataModel, FiveDModelRow, FiveDModelVersion
from fived.tests.factories import FiveDModelVersionFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

FORBIDDEN = (
    "boq_ready",
    "qs_approved",
    "five_d_ready",
    "estimate_ready",
    "approved_cost",
    "earned_value",
    "payment_ready",
    "procurement_ready",
    "unit_rate",
    "extended_cost",
    "total_cost",
    "estimated_cost",
    "task_cost",
    "schedule_cost",
)


def _project_with_ifc():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="s3b-entry.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-S3B-W",
        properties={"Qto_WallBaseQuantities.NetVolume": 1.5},
    )
    return project


def _entry_html(html: str) -> str:
    start = html.find('data-testid="qty-schema-insight-entry"')
    assert start >= 0
    end = html.find("</section>", start)
    assert end > start
    return html[start : end + len("</section>")]


@pytest.mark.django_db
def test_quantities_shows_empty_state_without_fived_version(client):
    """No snapshot → disabled entry + clear empty copy; no open link."""
    project = _project_with_ifc()
    client.force_login(project.owner)
    resp = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    entry = _entry_html(html)
    assert 'data-testid="qty-schema-insight-empty"' in entry
    assert 'data-testid="qty-schema-insight-disabled"' in entry
    assert 'data-testid="qty-schema-insight-open"' not in entry
    assert "No frozen 5D snapshot" in entry
    assert "Prepare mappings" in entry
    assert "freeze a snapshot" in entry
    assert 'data-testid="qty-schema-insight-why-snapshot"' in entry
    assert "View schema-based quantity rollups" not in entry
    assert "Review schema-based quantity rollups" in entry
    assert "Read-only" in entry
    assert "Snapshot-based" in entry
    assert "Not BOQ" not in entry
    assert "Not cost estimate" not in entry
    assert "This report is not" not in entry
    assert 'data-testid="qty-prep-export"' in html
    assert reverse("takeoff:qto_export", kwargs={"pk": project.pk}) in html
    for term in FORBIDDEN:
        assert term not in entry.lower()


@pytest.mark.django_db
def test_quantities_links_to_latest_schema_insight_when_version_exists(client):
    """Latest FiveDModelVersion yields Open link, version label/date, S3 route."""
    project = _project_with_ifc()
    older = FiveDModelVersionFactory(
        data_model__project=project,
        data_model__name="Older Prep",
        version_label="v1",
    )
    newer = FiveDModelVersionFactory(
        data_model=older.data_model,
        version_label="v2",
    )
    expected = reverse(
        "fived:schema_quantity_insight_report",
        kwargs={"pk": project.pk, "version_id": newer.pk},
    )
    wrong = reverse(
        "fived:schema_quantity_insight_report",
        kwargs={"pk": project.pk, "version_id": older.pk},
    )
    client.force_login(project.owner)
    resp = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    entry = _entry_html(html)
    assert 'data-testid="qty-schema-insight-open"' in entry
    assert "Open latest Schema Insight" in entry
    assert expected in entry
    assert wrong not in entry
    assert 'data-testid="qty-schema-insight-empty"' not in entry
    assert 'data-testid="qty-schema-insight-version-meta"' in entry
    assert "v2" in entry
    assert newer.data_model.name in entry
    assert 'data-testid="qty-schema-insight-why-snapshot"' in entry
    assert "Read-only" in entry
    assert "Snapshot-based" in entry
    assert "Review schema-based quantity rollups" in entry
    assert "Not BOQ" not in entry
    assert "Not cost estimate" not in entry
    assert FiveDModelVersion.objects.filter(data_model__project=project).count() == 2
    for term in FORBIDDEN:
        assert term not in entry.lower()


@pytest.mark.django_db
def test_entrypoint_follow_through_opens_s3_report(client):
    """Following the Quantities entry URL loads the cleaned S3 report."""
    project = _project_with_ifc()
    version = FiveDModelVersionFactory(data_model__project=project, version_label="v1")
    client.force_login(project.owner)
    qty = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}))
    html = qty.content.decode("utf-8")
    url = reverse(
        "fived:schema_quantity_insight_report",
        kwargs={"pk": project.pk, "version_id": version.pk},
    )
    assert url in html
    report = client.get(url)
    assert report.status_code == 200
    body = report.content.decode("utf-8")
    assert "5D Schema Quantity Insight" in body
    assert "fived-schema-quantity-insight-s2-v1" in body
    assert "This report is not" not in body
    assert "not_boq" not in body
    assert "Read-only" in body
    assert "Snapshot-based" in body


@pytest.mark.django_db
def test_entrypoint_does_not_write_db(client):
    """Loading Quantities with/without versions does not mutate fived tables."""
    project = _project_with_ifc()
    version = FiveDModelVersionFactory(data_model__project=project)
    before = {
        "models": FiveDDataModel.objects.count(),
        "versions": FiveDModelVersion.objects.count(),
        "rows": FiveDModelRow.objects.count(),
        "hash": version.content_hash,
    }
    client.force_login(project.owner)
    assert client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).status_code == 200
    version.refresh_from_db()
    assert FiveDDataModel.objects.count() == before["models"]
    assert FiveDModelVersion.objects.count() == before["versions"]
    assert FiveDModelRow.objects.count() == before["rows"]
    assert version.content_hash == before["hash"]
