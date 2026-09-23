# ifc_viewer/tests/test_timeline_payload_slim.py
"""Reliability Patch A″.2 — slim timeline summary + trusted interval detail."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from scheduling.models import TaskEntityBinding
from scheduling.services.timeline_payload import (
    SCHEMA_DETAIL,
    SCHEMA_SUMMARY,
    TimelinePayloadService,
)
from scheduling.tests.factories import TaskFactory


def _trusted_binding(task, global_id: str) -> TaskEntityBinding:
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
        governance_status=TaskEntityBinding.GovernanceStatus.TRUSTED,
        is_active=True,
    )


def _review_binding(task, global_id: str) -> TaskEntityBinding:
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=global_id,
        confidence=0.9,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
        governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
        is_active=True,
    )


@pytest.mark.django_db
def test_timeline_summary_omits_global_id_lists(client):
    """Default timeline endpoint returns stats-only intervals."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    trusted = IFCEntityFactory(ifc_file=ifc, global_id="GID-TRUST")
    IFCEntityFactory(ifc_file=ifc, global_id="GID-UNLINKED")
    task = TaskFactory(
        project=project,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 3, 1),
        is_non_physical=False,
    )
    _trusted_binding(task, trusted.global_id)
    client.force_login(project.owner)

    response = client.get(reverse("ifc_viewer:viewer_timeline", kwargs={"pk": project.pk}))
    data = response.json()

    assert response.status_code == 200
    assert data["schema"] == SCHEMA_SUMMARY
    assert data["has_tasks"] is True
    assert data["trusted_only"] is True
    assert data["detail_required"] is True
    assert data["no_task"] == []
    assert data["no_task_count"] >= 1
    assert data["linked_entity_count"] == 1
    assert len(data["intervals"]) >= 1
    for iv in data["intervals"]:
        ents = iv["entities"]
        assert ents["not_started"] == []
        assert ents["in_progress"] == []
        assert ents["complete"] == []
        assert ents["delayed"] == []
        assert "total" in iv["stats"]
    # Payload must stay tiny vs legacy full-GID embedding.
    assert len(response.content) < 50_000


@pytest.mark.django_db
def test_timeline_interval_detail_trusted_only(client):
    """Interval detail includes trusted GIDs and excludes review proposals."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    trusted_ent = IFCEntityFactory(ifc_file=ifc, global_id="GID-TRUST-D")
    review_ent = IFCEntityFactory(ifc_file=ifc, global_id="GID-REVIEW-D")
    IFCEntityFactory(ifc_file=ifc, global_id="GID-NONE-D")
    task = TaskFactory(
        project=project,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        actual_start=date(2025, 1, 1),
        actual_end=date(2025, 1, 20),
        status="complete",
        is_non_physical=False,
    )
    _trusted_binding(task, trusted_ent.global_id)
    _review_binding(task, review_ent.global_id)
    client.force_login(project.owner)

    snap = date(2025, 1, 15)
    response = client.get(
        reverse("ifc_viewer:viewer_timeline_interval", kwargs={"pk": project.pk}),
        {"date": snap.isoformat()},
    )
    data = response.json()

    assert response.status_code == 200
    assert data["schema"] == SCHEMA_DETAIL
    assert data["trusted_only"] is True
    assert data["date"] == snap.isoformat()
    all_gids = (
        data["entities"]["not_started"]
        + data["entities"]["in_progress"]
        + data["entities"]["complete"]
        + data["entities"]["delayed"]
    )
    assert "GID-TRUST-D" in all_gids
    assert "GID-REVIEW-D" not in all_gids
    # Review-only links are treated as untrusted for coloring → appear in no_task.
    assert "GID-REVIEW-D" in data["no_task"]
    assert "GID-NONE-D" in data["no_task"]


@pytest.mark.django_db
def test_timeline_interval_detail_requires_date(client):
    project = ProjectFactory()
    client.force_login(project.owner)
    response = client.get(reverse("ifc_viewer:viewer_timeline_interval", kwargs={"pk": project.pk}))
    assert response.status_code == 400


@pytest.mark.django_db
def test_timeline_interval_detail_empty_project(client):
    project = ProjectFactory()
    client.force_login(project.owner)
    response = client.get(
        reverse("ifc_viewer:viewer_timeline_interval", kwargs={"pk": project.pk}),
        {"date": "2025-01-01"},
    )
    data = response.json()
    assert response.status_code == 200
    assert data["has_tasks"] is False
    assert data["entities"]["complete"] == []


@pytest.mark.django_db
def test_task_state_respects_snapshot_for_actual_end():
    """Actual finish must not paint complete on dates before actual_end."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    ent = IFCEntityFactory(ifc_file=ifc, global_id="GID-SNAP")
    task = TaskFactory(
        project=project,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        actual_start=date(2025, 1, 1),
        actual_end=date(2025, 1, 20),
        is_non_physical=False,
    )
    _trusted_binding(task, ent.global_id)
    svc = TimelinePayloadService(project)

    before = svc.build_interval_detail(date(2024, 6, 1), mode="actual")
    assert ent.global_id in before["state_entities"]["actual_not_started"]
    assert before["stats"]["actual_complete"] == 0

    mid = svc.build_interval_detail(date(2025, 1, 10), mode="actual")
    assert ent.global_id in mid["state_entities"]["actual_in_progress"]
    assert mid["stats"]["actual_complete"] == 0

    after = svc.build_interval_detail(date(2025, 1, 25), mode="actual")
    assert ent.global_id in after["state_entities"]["actual_complete"]


@pytest.mark.django_db
def test_timeline_summary_service_stats_match_detail_counts():
    """Summary counts align with detail bucket lengths for the same snapshot."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    e1 = IFCEntityFactory(ifc_file=ifc, global_id="GID-A")
    e2 = IFCEntityFactory(ifc_file=ifc, global_id="GID-B")
    IFCEntityFactory(ifc_file=ifc, global_id="GID-C")
    start = date(2025, 2, 1)
    end = start + timedelta(days=20)
    t1 = TaskFactory(
        project=project,
        start_date=start,
        end_date=end,
        actual_start=start,
        actual_end=None,
        is_non_physical=False,
    )
    t2 = TaskFactory(
        project=project,
        start_date=start,
        end_date=end,
        actual_start=start,
        actual_end=end,
        is_non_physical=False,
    )
    _trusted_binding(t1, e1.global_id)
    _trusted_binding(t2, e2.global_id)

    svc = TimelinePayloadService(project)
    summary = svc.build_summary(mode="actual")
    snap = date.fromisoformat(summary["intervals"][0]["date"])
    detail = svc.build_interval_detail(snap, mode="actual")
    s_stats = summary["intervals"][0]["stats"]
    d_stats = detail["stats"]
    assert s_stats["complete"] == d_stats["complete"]
    assert s_stats["in_progress"] == d_stats["in_progress"]
    assert s_stats["delayed"] == d_stats["delayed"]
    assert d_stats["complete"] == len(detail["entities"]["complete"])
    assert d_stats["in_progress"] == len(detail["entities"]["in_progress"])
    assert d_stats["delayed"] == len(detail["entities"]["delayed"])


@pytest.mark.django_db
def test_fourd_and_lookahead_reference_timeline_detail_url(client):
    """4D Link / Look-ahead templates wire the lazy detail endpoint."""
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)

    fourd = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=fourD_link"
    ).content.decode()
    la = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=lookahead"
    ).content.decode()

    detail_path = reverse("ifc_viewer:viewer_timeline_interval", kwargs={"pk": project.pk})
    assert detail_path in fourd
    assert "_ensureIntervalDetail" in fourd
    assert detail_path in la
    assert "_ensureIntervalDetail" in la
