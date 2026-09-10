# takeoff/tests/test_link_analysis.py
"""4D Link Analysis — KPIs, charts, table (read-only, 4D-only)."""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from scheduling.models import TaskEntityBinding
from scheduling.tests.factories import TaskFactory
from takeoff.services.link_analysis import LinkAnalysisService, _attention_for_task


def _trusted(task, gid: str) -> TaskEntityBinding:
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
    )


@pytest.mark.django_db
def test_link_analysis_page_identity_and_no_qto(client):
    """Page title, KPIs, charts, table — no QTO/BOQ/cost or validation wording."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="pilot.ifc")
    e1 = IFCEntityFactory(ifc_file=ifc, ifc_type="IfcWall", global_id="GID-LA-1", name="Wall-1")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcSlab", global_id="GID-LA-2", name="Slab-1")
    t1 = TaskFactory(
        project=project,
        activity_code="C-101",
        name="Linked task",
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 1, 10),
    )
    TaskFactory(project=project, activity_code="C-102", name="Unlinked task")
    _trusted(t1, e1.global_id)
    client.force_login(project.owner)

    response = client.get(reverse("takeoff:model_inventory", kwargs={"pk": project.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert "4D Link Analysis" in html
    assert "Schedule Link Coverage" in html
    assert "Model Link Coverage" in html
    assert "Actionable Link Review" in html
    assert "Time View Readiness" in html
    assert "Link Attention Queue" not in html
    assert "Unlinked Model Elements" not in html
    assert "Task Link Coverage" not in html
    assert "5D Foundation" not in html
    assert "QTO readiness" not in html
    assert "CASTOR ENGINE / 4D LINKING" not in html
    assert "Validate Links" not in html
    assert "Validation Status" not in html
    assert "Refresh analysis" not in html
    assert "Export Logs" not in html
    assert "Export table" in html
    assert "All tasks · linked first" in html
    assert "unlinked/non-model" in html
    assert 'data-testid="link-analysis-page"' in html
    assert 'data-testid="la-kpi-schedule-coverage"' in html
    assert 'data-testid="la-kpi-model-coverage"' in html
    assert 'data-testid="la-kpi-actionable-review"' in html
    assert 'data-testid="la-kpi-time-view"' in html
    assert 'data-testid="la-chart-review-breakdown"' in html
    assert "Task Review Breakdown" in html
    assert "Task Attention Distribution" not in html
    assert "linked total" not in html
    assert "Unlinked/non-model tasks are shown for context in Schedule Coverage and Task Review Breakdown" in html
    assert "excluded from Actionable Link Review" in html
    assert "Pending review" in html
    assert "Have dates for playback review" in html
    assert "Attention" in html
    assert 'data-testid="la-attention-header"' in html
    assert ">Risk<" not in html
    assert "Link Review Table" in html
    assert "Unlink All" not in html
    assert "Model Readiness" not in html
    for banned in ("BOQ", "NetVolume", "QS valuation", "company actual cost", "EAC", "VAC"):
        assert banned not in html


@pytest.mark.django_db
def test_link_analysis_service_kpis():
    """KPIs use trusted links, attention queue, and time-view date readiness."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    e1 = IFCEntityFactory(ifc_file=ifc, ifc_type="IfcBeam", global_id="GID-K1")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcColumn", global_id="GID-K2")
    linked = TaskFactory(
        project=project,
        activity_code="A-1",
        name="Beam set",
        start_date=datetime.date(2024, 2, 1),
        end_date=datetime.date(2024, 2, 15),
    )
    TaskFactory(project=project, activity_code="A-2")
    _trusted(linked, e1.global_id)

    payload = LinkAnalysisService(project).build()
    k = payload["kpis"]
    assert k["linked_tasks"] == 1
    assert k["tasks_total"] == 2
    assert k["unlinked_tasks"] == 1
    assert k["task_link_pct"] == 50.0
    assert k["linked_elements"] == 1
    assert k["elements_total"] == 2
    assert k["element_link_pct"] == 50.0
    # Unlinked task is NOT in actionable review; only Review/Broad/Partial.
    assert k["actionable_review"] == 0
    assert k["attention_queue"] == 0
    assert k["attention_breakdown"]["unlinked"] == 1
    assert k["attention_breakdown"]["ok"] == 1
    assert k["time_view_available"] is True
    assert k["time_view_ready"] == 1
    assert k["time_view_linked"] == 1
    assert payload["charts"]["schedule_distribution"]["linked_total"] == 1
    assert payload["charts"]["schedule_distribution"]["rows"]
    assert len(payload["charts"]["fanout_histogram"]) == 5
    assert payload["honesty"]["not_qto"] is True


@pytest.mark.django_db
def test_actionable_review_excludes_unlinked_includes_review_and_broad(client):
    """Actionable Link Review = Review + Broad + Partial only."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    entities = [
        IFCEntityFactory(ifc_file=ifc, ifc_type="IfcWall", global_id=f"GID-AR-{i}")
        for i in range(100)
    ]
    ok_task = TaskFactory(
        project=project,
        activity_code="OK-1",
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 1, 2),
    )
    _trusted(ok_task, entities[0].global_id)
    TaskFactory(project=project, activity_code="UL-1")  # unlinked — not actionable
    review_task = TaskFactory(project=project, activity_code="RV-1")
    TaskEntityBinding.objects.create(
        task=review_task,
        entity_global_id=entities[1].global_id,
        confidence=0.5,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=True,
    )
    broad_task = TaskFactory(
        project=project,
        activity_code="BR-1",
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 1, 5),
    )
    for ent in entities:
        _trusted(broad_task, ent.global_id)

    payload = LinkAnalysisService(project).build()
    k = payload["kpis"]
    assert k["actionable_review"] == 2
    assert k["actionable_review_breakdown"]["review"] == 1
    assert k["actionable_review_breakdown"]["broad_link"] == 1
    assert k["actionable_review_breakdown"]["partial"] == 0
    assert k["attention_breakdown"]["unlinked"] == 1
    assert k["attention_breakdown"]["ok"] == 1
    labels = {row["label"]: row["n"] for row in payload["charts"]["schedule_distribution"]["rows"]}
    assert labels["OK linked"] == 1
    assert labels["Broad linked"] == 1
    assert labels["Review pending"] == 1
    assert labels["Unlinked/non-model"] == 1
    assert payload["charts"]["schedule_distribution"]["linked_total"] == 2
    order = [row["label"] for row in payload["charts"]["schedule_distribution"]["rows"]]
    assert order == [
        "Review pending",
        "Broad linked",
        "Partial",
        "OK linked",
        "Unlinked/non-model",
    ]


@pytest.mark.django_db
def test_time_view_readiness_unavailable_without_links():
    """No confirmed links → Time View Readiness unavailable."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcWall", global_id="GID-TV-1")
    TaskFactory(project=project, activity_code="U-1")
    payload = LinkAnalysisService(project).build()
    k = payload["kpis"]
    assert k["time_view_available"] is False
    assert k["time_view_ready"] is None
    assert "Schedule dates or confirmed links required" in k["time_view_reason"]


def test_attention_rules_match_service_thresholds():
    """Attention flags follow documented link-count / review-binding rules."""
    assert _attention_for_task(linked_count=0, pending_review=0) == "unlinked"
    assert _attention_for_task(linked_count=0, pending_review=2) == "review"
    assert _attention_for_task(linked_count=100, pending_review=0) == "broad_link"
    assert _attention_for_task(linked_count=12, pending_review=1) == "partial"
    assert _attention_for_task(linked_count=12, pending_review=0) == "ok"


@pytest.mark.django_db
def test_link_analysis_task_row_expansion_fields(client):
    """Task rows expose expansion preview without quantity fields."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    e1 = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-EX-1",
        name="Wall A",
        properties={"Qto_WallBaseQuantities.NetVolume": 9.9},
    )
    task = TaskFactory(
        project=project,
        activity_code="C-103",
        name="Pour slab",
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 2, 1),
    )
    _trusted(task, e1.global_id)
    client.force_login(project.owner)

    html = client.get(
        reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
    ).content.decode()

    assert "C-103" in html
    assert "Linked Elements Detail" in html
    assert "Open in Links" in html
    assert "Preview in Time View" in html
    assert "Review Link" in html
    assert "la-btn-secondary" in html
    assert "tasks checked" in html
    assert "linked tasks found" in html
    assert "Validation Status" not in html
    assert "NetVolume" not in html
    assert "Unlink All" not in html


@pytest.mark.django_db
def test_refresh_endpoint_remains_readonly(client):
    """Hidden refresh endpoint still recomputes without mutating bindings."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(ifc_file=ifc, ifc_type="IfcWall", global_id="GID-V1")
    TaskFactory(project=project)
    client.force_login(project.owner)

    before = TaskEntityBinding.objects.filter(task__project=project).count()
    url = reverse("takeoff:link_analysis_refresh", kwargs={"pk": project.pk})
    response = client.post(url)
    assert response.status_code == 302
    assert TaskEntityBinding.objects.filter(task__project=project).count() == before

    page = client.get(reverse("takeoff:model_inventory", kwargs={"pk": project.pk}))
    html = page.content.decode()
    assert "tasks checked" in html
    assert "Refresh analysis" not in html
    assert "Validate Links" not in html


@pytest.mark.django_db
def test_hub_model_tab_still_points_at_inventory_route(client):
    """4D/5D Model hub entry still resolves to inventory URL (content replaced)."""
    project = ProjectFactory()
    client.force_login(project.owner)
    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=data_sources"
    ).content.decode()
    assert reverse("takeoff:model_inventory", kwargs={"pk": project.pk}) in html
    assert 'data-testid="hub-model"' in html
