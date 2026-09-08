# takeoff/tests/test_quantities_schema_insight_entrypoint_s3b.py
"""5D-S3b Quantities entry point to Schema Quantity Insight report."""

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


@pytest.mark.django_db
def test_quantities_shows_empty_state_without_fived_version(client):
    """No snapshot → disabled entry + honest empty copy; prep export still present."""
    project = _project_with_ifc()
    client.force_login(project.owner)
    resp = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    assert 'data-testid="qty-schema-insight-entry"' in html
    assert 'data-testid="qty-schema-insight-empty"' in html
    assert 'data-testid="qty-schema-insight-disabled"' in html
    assert 'data-testid="qty-schema-insight-open"' not in html
    assert "No 5D snapshot is available yet" in html
    assert 'data-testid="qty-prep-export"' in html
    assert reverse("takeoff:qto_export", kwargs={"pk": project.pk}) in html
    for term in FORBIDDEN:
        assert term not in html.lower()


@pytest.mark.django_db
def test_quantities_links_to_latest_schema_insight_when_version_exists(client):
    """Latest FiveDModelVersion yields Open link resolving to S3 report route."""
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
    assert 'data-testid="qty-schema-insight-open"' in html
    assert expected in html
    assert wrong not in html
    assert 'data-testid="qty-schema-insight-empty"' not in html
    assert "Not BOQ" in html
    assert "Not cost estimate" in html
    assert FiveDModelVersion.objects.filter(data_model__project=project).count() == 2


@pytest.mark.django_db
def test_entrypoint_follow_through_opens_s3_report(client):
    """Following the Quantities entry URL loads the S3 report for that version."""
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
