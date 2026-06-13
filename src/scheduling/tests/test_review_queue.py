# scheduling/tests/test_review_queue.py
"""E2-B review queue and evidence contract tests."""

from __future__ import annotations

import json
from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from scheduling.models import TaskEntityBinding
from scheduling.services.governance.evidence_contract import (
    build_binding_evidence,
)
from scheduling.services.governance.review_queue import (
    MAX_PAGE_SIZE,
    LinkReviewQueueService,
)
from scheduling.services.governance.vocabulary import EvidenceLabel, QueueMode
from scheduling.tests.factories import TaskFactory


def _bind(task, gid: str, *, needs_review=False, method=None, confidence=1.0):
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=confidence,
        link_method=method or TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=needs_review,
    )


@pytest.mark.django_db
def test_review_mode_returns_review_only():
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-T", needs_review=False)
    _bind(task, "GID-R", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC)

    filters = LinkReviewQueueService.filters_from_request({"mode": "review"})
    payload = LinkReviewQueueService(project.pk, project_pk=project.pk).build(filters)
    assert payload["pagination"]["total_items"] == 1
    assert payload["items"][0]["governance"]["needs_review"] is True


@pytest.mark.django_db
def test_trusted_mode_returns_accepted_only():
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-T", needs_review=False)
    _bind(task, "GID-R", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC)

    filters = LinkReviewQueueService.filters_from_request({"mode": "trusted"})
    payload = LinkReviewQueueService(project.pk).build(filters)
    assert all(i["governance"]["trusted"] for i in payload["items"])


@pytest.mark.django_db
def test_property_hints_not_trusted():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project)
    IFCEntityFactory(ifc_file=ifc, global_id="GID-H", properties={"Castor.Activity ID": "A1"})
    filters = LinkReviewQueueService.filters_from_request({"mode": "property_hints"})
    payload = LinkReviewQueueService(project.pk).build(filters)
    if payload["items"]:
        assert payload["items"][0]["governance"]["trusted"] is False
        assert payload["items"][0]["governance"]["category"] == "property_hint"


@pytest.mark.django_db
def test_legacy_only_not_trusted():
    project = ProjectFactory()
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-M2M")
    task.ifc_entities.add(entity)
    filters = LinkReviewQueueService.filters_from_request({"mode": "legacy_only"})
    payload = LinkReviewQueueService(project.pk).build(filters)
    assert payload["pagination"]["total_items"] == 1
    assert payload["items"][0]["governance"]["trusted"] is False


@pytest.mark.django_db
def test_exact_evidence_label():
    ev = build_binding_evidence(
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
        confidence=1.0,
    )
    assert ev.evidence_label == EvidenceLabel.EXACT_IDENTIFIER.value


@pytest.mark.django_db
def test_normalized_distinct_from_exact():
    exact = build_binding_evidence(
        link_method=TaskEntityBinding.LinkMethod.EXACT, needs_review=False, confidence=1.0
    )
    norm = build_binding_evidence(
        link_method=TaskEntityBinding.LinkMethod.NORMALIZED, needs_review=True, confidence=0.95
    )
    assert exact.evidence_label != norm.evidence_label
    assert norm.review_required is True


@pytest.mark.django_db
def test_confidence_095_remains_review():
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(
        task,
        "GID-N",
        needs_review=True,
        method=TaskEntityBinding.LinkMethod.NORMALIZED,
        confidence=0.95,
    )
    filters = LinkReviewQueueService.filters_from_request({"mode": "review"})
    payload = LinkReviewQueueService(project.pk).build(filters)
    assert payload["pagination"]["total_items"] == 1
    assert payload["items"][0]["governance"]["trusted"] is False


@pytest.mark.django_db
def test_task_centric_separation():
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-T", needs_review=False)
    _bind(task, "GID-R", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC)
    IFCEntityFactory(ifc_file__project=project, global_id="GID-T")
    IFCEntityFactory(ifc_file__project=project, global_id="GID-R")

    payload = LinkReviewQueueService(project.pk).task_centric(task.pk)
    assert payload["trusted_count"] == 1
    assert payload["review_count"] == 1


@pytest.mark.django_db
def test_entity_centric_multiple_trusted_not_conflict():
    project = ProjectFactory()
    t1 = TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 10))
    t2 = TaskFactory(project=project, start_date=date(2025, 2, 1), end_date=date(2025, 2, 10))
    gid = "GID-MULTI"
    _bind(t1, gid, needs_review=False)
    _bind(t2, gid, needs_review=False)
    IFCEntityFactory(ifc_file__project=project, global_id=gid)

    payload = LinkReviewQueueService(project.pk).entity_centric(gid)
    assert payload["governance"]["category"] == "multiple_trusted"
    assert not payload["conflicts"]


@pytest.mark.django_db
def test_overlap_triggers_possible_conflict():
    project = ProjectFactory()
    t1 = TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 20))
    t2 = TaskFactory(project=project, start_date=date(2025, 1, 10), end_date=date(2025, 1, 30))
    gid = "GID-OVER"
    _bind(t1, gid, needs_review=False)
    _bind(t2, gid, needs_review=False)
    IFCEntityFactory(ifc_file__project=project, global_id=gid)

    payload = LinkReviewQueueService(project.pk).entity_centric(gid)
    assert payload["governance"]["category"] == "possible_conflict"


@pytest.mark.django_db
def test_cross_project_excluded():
    p1 = ProjectFactory()
    p2 = ProjectFactory()
    _bind(TaskFactory(project=p2), "GID-X", needs_review=True)
    filters = LinkReviewQueueService.filters_from_request({"mode": "review"})
    payload = LinkReviewQueueService(p1.pk).build(filters)
    assert payload["pagination"]["total_items"] == 0


@pytest.mark.django_db
def test_gantt_trusted_highlight_only(client):
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project, is_non_physical=False)
    _bind(task, "GID-TR", needs_review=False)
    _bind(task, "GID-REV", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC)
    client.force_login(user)
    resp = client.get(reverse("scheduling:gantt_data", kwargs={"pk": project.pk}))
    row = next(t for t in resp.json()["tasks"] if t["id"] == str(task.pk))
    assert row["entity_global_ids"] == ["GID-TR"]
    assert row["review_entity_count"] == 1


@pytest.mark.django_db
def test_task_detail_sections(client):
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project)
    _bind(task, "GID-T", needs_review=False)
    _bind(task, "GID-R", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC)
    IFCEntityFactory(ifc_file__project=project, global_id="GID-T")
    IFCEntityFactory(ifc_file__project=project, global_id="GID-R")
    client.force_login(user)
    resp = client.get(
        reverse("scheduling:task_detail", kwargs={"pk": project.pk, "task_pk": task.pk})
    )
    body = resp.content.decode()
    assert "Trusted Links" in body
    assert "Review Suggestions" in body


@pytest.mark.django_db
def test_property_hint_provider_no_writes():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project)
    IFCEntityFactory(ifc_file=ifc, properties={"Castor.Activity ID": "A99"})
    before = TaskEntityBinding.objects.count()
    LinkReviewQueueService(project.pk).build(
        LinkReviewQueueService.filters_from_request({"mode": "property_hints"})
    )
    assert TaskEntityBinding.objects.count() == before


@pytest.mark.django_db
def test_pagination_deterministic():
    project = ProjectFactory()
    for i in range(5):
        _bind(TaskFactory(project=project), f"GID-{i}", needs_review=True)
    f1 = LinkReviewQueueService.filters_from_request({"mode": "review", "page": 1, "page_size": 2})
    f2 = LinkReviewQueueService.filters_from_request({"mode": "review", "page": 1, "page_size": 2})
    s = LinkReviewQueueService(project.pk)
    assert s.build(f1)["items"][0]["item_id"] == s.build(f2)["items"][0]["item_id"]


@pytest.mark.django_db
def test_page_size_cap():
    f = LinkReviewQueueService.filters_from_request({"page_size": "500"})
    assert f.page_size == MAX_PAGE_SIZE


@pytest.mark.django_db
def test_invalid_mode_safe():
    f = LinkReviewQueueService.filters_from_request({"mode": "bogus"})
    assert f.mode == QueueMode.REVIEW.value


@pytest.mark.django_db
def test_unauthorized_rejected(client):
    project = ProjectFactory()
    url = reverse("scheduling:link_governance_review_queue", kwargs={"pk": project.pk})
    assert client.get(url).status_code in (302, 403, 404)


@pytest.mark.django_db
def test_queue_query_count_bounded():
    project = ProjectFactory()
    for i in range(30):
        _bind(
            TaskFactory(project=project),
            f"GID-{i}",
            needs_review=(i % 3 == 0),
            method=TaskEntityBinding.LinkMethod.HEURISTIC,
        )
    filters = LinkReviewQueueService.filters_from_request(
        {"mode": "all_governance", "page_size": "25"}
    )
    with CaptureQueriesContext(connection) as ctx:
        LinkReviewQueueService(project.pk).build(filters)
    assert len(ctx.captured_queries) <= 15


@pytest.mark.django_db
def test_review_queue_endpoint_json(client):
    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)
    url = reverse("scheduling:link_governance_review_queue", kwargs={"pk": project.pk})
    resp = client.get(url, {"mode": "trusted"})
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["policy_id"] == "trusted-binding-v1"


@pytest.mark.django_db
def test_operational_report_untracked():
    import subprocess

    out = subprocess.check_output(
        ["git", "ls-files", "castor_e2b_review_queue_evidence_report.txt"],
        cwd=".",
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert out.strip() == ""
