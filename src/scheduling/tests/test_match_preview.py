# scheduling/tests/test_match_preview.py
"""Tests for read-only exact-match preview (E1-D)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory
from scheduling.models import TaskEntityBinding
from scheduling.services.match_preview import MatchPreviewService
from scheduling.tests.factories import TaskFactory


def _preview_url(project_pk, param_name: str = "Activity ID") -> str:
    return (
        reverse(
            "scheduling:schedule_link_preview_param",
            kwargs={"pk": project_pk},
        )
        + f"?param_name={param_name}&format=json"
    )


@pytest.mark.django_db
def test_preview_exact_match_counts():
    """One activity code matching many IFC entities yields one task and many bindings."""
    project = ProjectFactory()
    TaskFactory(project=project, activity_code="B01Z2-C033000.20")
    for i in range(5):
        IFCEntityFactory(
            ifc_file__project=project,
            global_id=f"GID-MULTI-{i:03d}",
            properties={"Identity Data.Activity ID": "B01Z2-C033000.20"},
        )

    result = MatchPreviewService(project).preview("Activity ID")

    assert result.matched_task_count == 1
    assert result.projected_binding_count == 5
    assert result.matched_ifc_entity_count == 5
    assert result.exact_matched_activity_ids == 1


@pytest.mark.django_db
def test_preview_reports_unmatched_ifc_ids():
    """IFC Activity IDs with no schedule task appear in unmatched samples."""
    project = ProjectFactory()
    TaskFactory(project=project, activity_code="SCHED-ONLY-001")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Identity Data.Activity ID": "IFC-ONLY-999"},
    )

    result = MatchPreviewService(project).preview("Activity ID")

    assert result.unmatched_ifc_activity_ids == 1
    assert "IFC-ONLY-999" in result.unmatched_ifc_id_samples
    assert result.schedule_only_activity_codes == 1
    assert "SCHED-ONLY-001" in result.schedule_only_id_samples


@pytest.mark.django_db
def test_preview_distinguishes_review_and_accepted_bindings():
    """Existing review bindings are counted separately from accepted bindings."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="BIND-STATE-01")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-REVIEW-01",
        properties={"Castor.Activity ID": "BIND-STATE-01"},
    )
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=0.8,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
    )

    result = MatchPreviewService(project).preview("Activity ID")

    assert result.existing_review_bindings == 1
    assert result.existing_accepted_bindings == 0
    assert result.projected_updates == 1
    assert result.projected_inserts == 0


@pytest.mark.django_db
def test_preview_accepted_binding_is_no_op():
    """Accepted exact binding on matched pair projects as no-op."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="NOOP-001")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-NOOP-001",
        properties={"Castor.Activity ID": "NOOP-001"},
    )
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
    )

    result = MatchPreviewService(project).preview("Activity ID")

    assert result.projected_no_ops == 1
    assert result.projected_inserts == 0


@pytest.mark.django_db
def test_preview_fingerprint_stable_for_unchanged_data():
    """Repeated preview on unchanged data yields identical fingerprint."""
    project = ProjectFactory()
    TaskFactory(project=project, activity_code="STABLE-001")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "STABLE-001"},
    )

    svc = MatchPreviewService(project)
    fp1 = svc.preview("Activity ID").preview_fingerprint
    fp2 = svc.preview("Activity ID").preview_fingerprint
    assert fp1 == fp2
    assert len(fp1) == 64


@pytest.mark.django_db
def test_preview_fingerprint_changes_when_activity_code_changes():
    """Fingerprint changes when schedule activity code changes."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="FP-001")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "FP-001"},
    )

    svc = MatchPreviewService(project)
    fp_before = svc.preview("Activity ID").preview_fingerprint

    task.activity_code = "FP-002"
    task.save(update_fields=["activity_code"])

    fp_after = svc.preview("Activity ID").preview_fingerprint
    assert fp_before != fp_after


@pytest.mark.django_db
def test_preview_fingerprint_changes_when_binding_added():
    """Fingerprint changes when binding state changes."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="FP-BIND-01")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-FP-BIND",
        properties={"Castor.Activity ID": "FP-BIND-01"},
    )

    svc = MatchPreviewService(project)
    fp_before = svc.preview("Activity ID").preview_fingerprint

    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        needs_review=False,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
    )

    fp_after = svc.preview("Activity ID").preview_fingerprint
    assert fp_before != fp_after


@pytest.mark.django_db
def test_preview_case_insensitive_only_is_diagnostic_not_match():
    """Case-only differences are reported diagnostically, not as exact matches."""
    project = ProjectFactory()
    TaskFactory(project=project, activity_code="Act-Case-Upper")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "act-case-upper"},
    )

    result = MatchPreviewService(project).preview("Activity ID")

    assert result.matched_task_count == 0
    assert result.case_insensitive_only_count == 1
    assert result.projected_binding_count == 0


@pytest.mark.django_db
def test_preview_malformed_semicolon_not_matched():
    """Malformed Activity ID values with separators are not silently matched."""
    project = ProjectFactory()
    TaskFactory(project=project, activity_code="A;B")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "A;B"},
    )

    result = MatchPreviewService(project).preview("Activity ID")

    assert result.malformed_activity_id_values >= 1
    assert result.projected_binding_count == 0


@pytest.mark.django_db
def test_preview_excludes_cross_project_entities():
    """Entities from another project are not included in preview scope."""
    project = ProjectFactory()
    other = ProjectFactory()
    TaskFactory(project=project, activity_code="SCOPE-001")
    IFCEntityFactory(
        ifc_file__project=other,
        properties={"Castor.Activity ID": "SCOPE-001"},
    )

    result = MatchPreviewService(project).preview("Activity ID")

    assert result.matched_task_count == 0
    assert result.total_ifc_entities == 0


@pytest.mark.django_db
def test_preview_endpoint_makes_zero_writes(client):
    """Preview GET does not create or modify bindings or legacy M2M."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project, activity_code="NOWRITE-001")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-NOWRITE",
        properties={"Castor.Activity ID": "NOWRITE-001"},
    )
    client.force_login(user)

    binding_before = TaskEntityBinding.objects.count()
    m2m_before = task.ifc_entities.count()

    url = _preview_url(project.pk)
    response = client.get(url)

    assert response.status_code == 200
    data = response.json()
    assert data["matched_task_count"] == 1
    assert data["projected_binding_count"] == 1
    assert TaskEntityBinding.objects.count() == binding_before
    assert task.ifc_entities.count() == m2m_before
    assert not TaskEntityBinding.objects.filter(
        task=task, entity_global_id=entity.global_id
    ).exists()


@pytest.mark.django_db
def test_preview_endpoint_does_not_call_persist(client):
    """Preview path never invokes persist_param_matches."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    TaskFactory(project=project, activity_code="NOPERSIST-01")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "NOPERSIST-01"},
    )
    client.force_login(user)

    with patch("scheduling.services.linker.persist_param_matches") as mock_persist:
        response = client.get(_preview_url(project.pk))
        assert response.status_code == 200
        mock_persist.assert_not_called()


@pytest.mark.django_db
def test_preview_requires_project_access(client):
    """Users without project membership cannot preview."""
    owner = UserFactory()
    stranger = UserFactory()
    project = ProjectFactory(owner=owner)
    TaskFactory(project=project, activity_code="AUTH-001")
    client.force_login(stranger)

    response = client.get(_preview_url(project.pk))
    assert response.status_code in (403, 404)


@pytest.mark.django_db
def test_link_param_post_blocked_no_binding_write(client):
    """LinkParamView POST no longer persists bindings — directs to preview."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project, activity_code="GUARD-001")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-GUARD",
        properties={"Castor.Activity ID": "GUARD-001"},
    )
    client.force_login(user)

    url = reverse("scheduling:schedule_link_param", kwargs={"pk": project.pk})
    response = client.post(url, {"param_name": "Activity ID"})

    assert response.status_code == 400
    assert not TaskEntityBinding.objects.filter(task=task).exists()


@pytest.mark.django_db
def test_preview_reports_stale_accepted_bindings(client):
    """Accepted bindings outside projected match set are reported as stale."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="MATCH-001")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "MATCH-001"},
    )
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id="GID-STALE-OUT",
        needs_review=False,
        link_method=TaskEntityBinding.LinkMethod.MANUAL,
    )

    result = MatchPreviewService(project).preview("Activity ID")
    assert result.projected_stale_bindings == 1


@pytest.mark.django_db
def test_preview_duplicate_schedule_codes_warned():
    """Duplicate physical task activity codes produce a warning."""
    project = ProjectFactory()
    TaskFactory(project=project, activity_code="DUP-CODE")
    TaskFactory(project=project, activity_code="DUP-CODE")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "DUP-CODE"},
    )

    result = MatchPreviewService(project).preview("Activity ID")
    assert result.duplicate_schedule_codes == 1
    assert any("duplicate" in w.lower() for w in result.warnings)


@pytest.mark.django_db
def test_preview_htmx_returns_html_partial(client):
    """HTMX request returns HTML preview partial, not JSON."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    TaskFactory(project=project, activity_code="HTML-001")
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "HTML-001"},
    )
    client.force_login(user)

    url = reverse("scheduling:schedule_link_preview_param", kwargs={"pk": project.pk})
    response = client.get(
        url,
        {"param_name": "Activity ID"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert "Read-only preview" in html
    assert "projected trusted binding" in html.lower() or "Projected bindings" in html


@pytest.mark.django_db
def test_preview_excludes_non_physical_tasks():
    """Non-physical tasks are excluded from matching scope."""
    project = ProjectFactory()
    TaskFactory(project=project, activity_code="PHYS-001", is_non_physical=False)
    TaskFactory(project=project, activity_code="NONPHYS-001", is_non_physical=True)
    IFCEntityFactory(
        ifc_file__project=project,
        properties={"Castor.Activity ID": "NONPHYS-001"},
    )

    result = MatchPreviewService(project).preview("Activity ID")
    assert result.matched_task_count == 0
    assert result.unmatched_ifc_activity_ids == 1


@pytest.mark.django_db
def test_preview_query_count_bounded(client):
    """Preview uses bulk queries — not O(tasks × entities) round-trips."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    for i in range(20):
        TaskFactory(project=project, activity_code=f"BULK-T{i:03d}")
        IFCEntityFactory(
            ifc_file__project=project,
            properties={"Castor.Activity ID": f"BULK-T{i:03d}"},
        )
    client.force_login(user)

    with CaptureQueriesContext(connection) as ctx:
        response = client.get(_preview_url(project.pk))
    assert response.status_code == 200
    assert len(ctx.captured_queries) <= 12


def _htmx_preview_url(project_pk, **params: str) -> str:
    base = reverse("scheduling:schedule_link_preview_param", kwargs={"pk": project_pk})
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}" if qs else base


@pytest.mark.django_db
def test_preview_summary_already_applied_hides_approval_language(client):
    """Compact summary shows active trusted state when write plan is all no-ops."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project, activity_code="ACTIVE-001")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-ACTIVE-001",
        properties={"Castor.Activity ID": "ACTIVE-001"},
    )
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
    )
    client.force_login(user)

    response = client.get(
        _htmx_preview_url(project.pk, param_name="Activity ID", summary_only="1"),
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert "Trusted bindings active" in html
    assert "No pending writes" in html
    assert "Apply approved bindings" not in html
    assert "for distribution and approval" not in html


@pytest.mark.django_db
def test_preview_governance_pending_writes_shows_approval_gate(client):
    """Governance exact preview shows approval gate when inserts are pending."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    TaskFactory(project=project, activity_code="PEND-001")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-PEND-001",
        properties={"Castor.Activity ID": "PEND-001"},
    )
    client.force_login(user)

    response = client.get(
        _htmx_preview_url(project.pk, param_name="Activity ID", show_approval="1"),
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert "param-match-approval-panel" in html
    assert "Apply approved bindings" in html


@pytest.mark.django_db
def test_preview_governance_already_applied_hides_approval_gate(client):
    """Governance exact preview hides approval when bindings are already active."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    task = TaskFactory(project=project, activity_code="DONE-001")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-DONE-001",
        properties={"Castor.Activity ID": "DONE-001"},
    )
    TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=entity.global_id,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
    )
    client.force_login(user)

    response = client.get(
        _htmx_preview_url(project.pk, param_name="Activity ID", show_approval="1"),
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert "already approved and active" in html
    assert "param-match-approval-panel" not in html
    assert "Apply approved bindings" not in html
    assert "Activity distribution" in html
