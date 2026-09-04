# takeoff/tests/test_model_readiness_phase1.py
"""Model Readiness visual dashboard — summary, charts, findings, inventory evidence."""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from scheduling.models import TaskEntityBinding
from scheduling.tests.factories import TaskFactory
from takeoff.services.model_inventory import ModelInventoryService


def _trusted(task, gid: str) -> TaskEntityBinding:
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
    )


@pytest.mark.django_db
def test_model_readiness_page_identity_and_six_cards(client):
    """Title, summary, charts, filters, Not BOQ, no combined Missing KPI."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="pilot.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-R1",
        properties={
            "Qto_WallBaseQuantities.NetVolume": 2.0,
            "Identity Data.Activity ID": "A-100",
        },
    )
    client.force_login(project.owner)

    response = client.get(reverse("takeoff:model_inventory", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert "Model Readiness" in html
    assert "IFC inventory, linkability, and QTO readiness" in html
    assert 'data-testid="model-readiness-summary"' in html
    assert 'data-testid="model-readiness-overall-status"' in html
    assert 'data-testid="model-readiness-filters"' in html
    assert 'data-testid="model-readiness-filter-class"' in html
    assert 'data-testid="model-readiness-filter-level"' in html
    assert 'data-testid="model-readiness-filter-linked"' in html
    assert 'data-testid="model-readiness-filter-qto"' in html
    assert 'data-testid="model-readiness-filter-issue"' in html
    assert 'data-testid="model-readiness-charts"' in html
    for chart_id in (
        "element",
        "task",
        "spatial",
        "qto",
        "granularity",
        "playback",
    ):
        assert f'data-testid="model-readiness-chart-{chart_id}"' in html
    assert "Element link coverage" in html
    assert "Task link coverage" in html
    assert html.find("Element link coverage") != html.find("Task link coverage")
    assert "Floor/zone sequencing" in html
    assert "QTO readiness, not BOQ" in html
    assert "Schedule–model granularity" in html
    assert "Playback readiness" in html
    assert "Ready" in html or "Warning" in html or "Unavailable" in html
    assert 'data-testid="mi-kpi-missing"' not in html
    assert "Classification not indexed" in html
    assert 'data-testid="mi-classification-unavailable"' in html
    assert "Unavailable" in html
    assert 'data-testid="model-inventory-evidence"' in html
    assert "Inventory evidence" in html
    assert "Use these tables to investigate readiness findings." in html
    assert 'data-testid="model-inventory-drilldown"' in html
    assert 'data-testid="mi-class-table"' in html
    assert 'data-testid="mi-level-table"' in html
    assert 'data-testid="model-inventory-not-boq-badge"' in html
    assert "Not BOQ" in html
    assert 'data-testid="model-readiness-findings"' in html
    assert 'data-testid="model-readiness-denominator"' in html
    assert "Task link coverage uses programme tasks" in html
    evidence_at = html.find('data-testid="model-inventory-evidence"')
    charts_at = html.find('data-testid="model-readiness-charts"')
    assert charts_at != -1 and evidence_at != -1 and charts_at < evidence_at


@pytest.mark.django_db
def test_model_readiness_does_not_duplicate_other_surfaces(client):
    """No Explore dump, Viewer embed, Task Legend Groups feature, or Time View playback."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B9",
        properties={"Pset_BeamCommon.Span": 4.2},
    )
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    ).content.decode()

    assert "Pset_BeamCommon.Span" not in html
    assert "GID-B9" not in html
    assert "viewer_embed" not in html
    assert "Construction Sets" not in html
    assert "timeline-scrubber" not in html
    assert "castor:timeline-applied" not in html
    assert "NetVolume" not in html
    assert "Task Legend Groups need a later saved appearance profile" in html
    assert "Not an IFC-class custom 4D legend" in html


@pytest.mark.django_db
def test_model_readiness_service_scores_activity_id_and_skips_timeline_summary():
    """Activity ID fill is counted; playback uses linked-task dates, not timeline summary."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    e1 = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-AID-1",
        properties={"Identity Data.Activity ID": "W-01"},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-AID-2",
        properties={},
    )
    TaskFactory(
        project=project,
        start_date=datetime.date(2023, 1, 1),
        end_date=datetime.date(2023, 6, 1),
        activity_code="EARLY",
    )
    linked = TaskFactory(
        project=project,
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 3, 1),
        activity_code="W-01",
    )
    _trusted(linked, e1.global_id)

    payload = ModelInventoryService(project).build()
    readiness = payload["readiness"]
    cards = {c["id"]: c for c in readiness["cards"]}

    assert len(readiness["cards"]) == 6
    assert readiness["playback_source"] == "linked_task_dates"
    assert readiness["playback_not_timeline_summary"] is True
    assert readiness["summary"]["status"] in {"ready", "warning", "blocked", "unavailable"}
    assert "linked" in readiness["charts"]["element"]
    assert "linked" in readiness["charts"]["task"]
    assert readiness["charts"]["task"]["scope"] == "project-wide"
    assert "Activity ID filled on 1 of 2" in cards["linkability"]["detail"]
    assert cards["classification"]["status"] == "unavailable"
    assert "Task Legend Groups" in cards["classification"]["detail"]
    assert "not treated as a defect" in cards["granularity"]["detail"]
    assert cards["playback"]["action_id"] == "time_view"
    blob = str(payload)
    assert "GID-AID-1" not in blob


@pytest.mark.django_db
def test_ifc_elements_route_still_works(client):
    """IFC Elements drill-in remains a working grid, not a property dump."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcColumn", global_id="GID-EL", properties={})
    client.force_login(project.owner)

    url = reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk})
    response = client.get(url)
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-testid="mi-entities-full-page"' in html
    assert "Back to Model" in html
    assert "GID-EL" not in html
    assert "does not dump properties" in html.lower()


@pytest.mark.django_db
def test_model_readiness_shortcuts_and_no_combined_missing(client):
    """Links is primary; Quantities present; combined Missing KPI is gone."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcSlab", global_id="GID-S", properties={})
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    ).content.decode()

    apply_pos = html.find('data-testid="model-inventory-open-apply"')
    viewer_pos = html.find('data-testid="model-inventory-open-viewer"')
    assert apply_pos != -1 and viewer_pos != -1
    assert apply_pos < viewer_pos
    assert 'data-testid="model-inventory-open-quantities"' in html
    assert "Spatial gaps" in html
    assert "QTO gaps" in html
    assert 'data-testid="mi-kpi-missing"' not in html
    assert "Inventory evidence" in html
    assert 'data-testid="model-inventory-open-time-view"' in html
