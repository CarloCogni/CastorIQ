# scheduling/tests/test_binding_governance.py
"""E2-A trusted link governance domain and read-model tests."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from ifc_viewer.services.colormap import build_colormap
from ifc_viewer.services.gap_analysis import build_gap_analysis
from scheduling.models import TaskEntityBinding
from scheduling.services.governance.classifier import GovernanceStateClassifier
from scheduling.services.governance.evidence import evidence_label_for_binding
from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY_ID
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.governance.summary import GovernanceSummaryService
from scheduling.services.governance.vocabulary import EvidenceLabel, GovernanceCategory
from scheduling.services.health_check import run_health_check
from scheduling.services.link_resolver import (
    entity_gids_by_task,
    linked_entity_gids_for_project,
)
from scheduling.tests.factories import TaskFactory


def _bind(task, gid: str, *, needs_review: bool = False, method=None, confidence=1.0):
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=confidence,
        link_method=method or TaskEntityBinding.LinkMethod.MANUAL,
        needs_review=needs_review,
    )


@pytest.mark.django_db
def test_trusted_query_returns_only_accepted():
    """Trusted query returns only needs_review=False bindings."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-T", needs_review=False)
    _bind(task, "GID-R", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC)

    reader = BindingGovernanceReader(project.pk)
    assert reader.trusted_entity_gids() == {"GID-T"}
    assert reader.review_entity_gids() == {"GID-R"}


@pytest.mark.django_db
def test_colormap_excludes_review_bindings():
    """Colormap schedule_status excludes review bindings."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, global_id="GID-C", properties={})
    _bind(TaskFactory(project=project), entity.global_id, needs_review=True)

    result = build_colormap(ifc_file, "schedule_status", project_id=str(project.pk))
    assert result["colormap"][entity.global_id] == "#94a3b8"


@pytest.mark.django_db
def test_gap_analysis_excludes_review_bindings():
    """Gap analysis trusted linked count excludes review bindings."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall", global_id="GID-G")
    _bind(TaskFactory(project=project), "GID-G", needs_review=True)

    rows = build_gap_analysis(ifc_file, "element_type", project_id=str(project.pk))
    wall = next(r for r in rows if r["group"] == "IfcWall")
    assert wall["linked"] == 0


@pytest.mark.django_db
def test_property_only_not_trusted():
    """Activity ID property without accepted binding is not trusted."""
    project = ProjectFactory()
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(
        ifc_file=ifc_file,
        global_id="GID-P",
        properties={"Castor.Activity ID": "A1"},
    )
    result = GovernanceStateClassifier.classify_entity(
        trusted_task_ids=[],
        review_task_ids=[],
        has_property_hint=True,
    )
    assert result.primary == GovernanceCategory.PROPERTY_HINT
    assert result.trusted is False
    colormap = build_colormap(ifc_file, "schedule_status", project_id=str(project.pk))
    assert colormap["colormap"][entity.global_id] == "#94a3b8"


@pytest.mark.django_db
def test_m2m_only_not_trusted():
    """M2M-only relation without accepted binding is legacy compatibility only."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-M2M")
    task.ifc_entities.add(entity)

    reader = BindingGovernanceReader(project.pk)
    assert reader.trusted_entity_gids() == set()
    assert reader.legacy_m2m_only_relation_count() == 1

    state = GovernanceStateClassifier.classify_task(
        trusted_count=0,
        review_count=0,
        legacy_m2m_count=1,
    )
    assert state.primary == GovernanceCategory.LEGACY_COMPATIBILITY
    assert state.trusted is False


@pytest.mark.django_db
def test_accepted_plus_m2m_one_trusted_relation():
    """Accepted binding with M2M sync remains one trusted relation."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-BOTH")
    _bind(task, entity.global_id, needs_review=False)
    task.ifc_entities.add(entity)

    reader = BindingGovernanceReader(project.pk)
    assert reader.trusted_entity_gids() == {entity.global_id}
    assert reader.legacy_m2m_only_relation_count() == 0


@pytest.mark.django_db
def test_accepted_and_review_same_task_entity_unique():
    """Unique constraint prevents duplicate task/entity pairs."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-1", needs_review=False)
    assert BindingGovernanceReader(project.pk).trusted_counts()["trusted_bindings"] == 1


@pytest.mark.django_db
def test_cross_project_binding_excluded():
    """Bindings from other projects are excluded from reader scope."""
    p1 = ProjectFactory()
    p2 = ProjectFactory()
    t1 = TaskFactory(project=p1)
    t2 = TaskFactory(project=p2)
    _bind(t1, "GID-1", needs_review=False)
    _bind(t2, "GID-2", needs_review=False)

    assert BindingGovernanceReader(p1.pk).trusted_entity_gids() == {"GID-1"}


@pytest.mark.django_db
def test_task_scoped_trusted_review_counts():
    """Task-scoped trusted and review lists are accurate."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-T1", needs_review=False)
    _bind(task, "GID-T2", needs_review=False)
    _bind(task, "GID-R1", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC)

    reader = BindingGovernanceReader(project.pk)
    assert reader.trusted_entity_gids_for_task(task.pk) == ["GID-T1", "GID-T2"]
    assert reader.review_entity_gids_for_task(task.pk) == ["GID-R1"]


@pytest.mark.django_db
def test_entity_scoped_trusted_tasks():
    """Entity-scoped trusted task lookup is accurate."""
    project = ProjectFactory()
    t1 = TaskFactory(project=project)
    t2 = TaskFactory(project=project)
    gid = "GID-SHARED"
    _bind(t1, gid, needs_review=False)
    _bind(t2, gid, needs_review=False)

    reader = BindingGovernanceReader(project.pk)
    tids = reader.trusted_tasks_for_entity(gid)
    assert len(tids) == 2
    result = GovernanceStateClassifier.classify_entity(
        trusted_task_ids=tids,
        review_task_ids=[],
    )
    assert result.primary == GovernanceCategory.MULTIPLE_TRUSTED


@pytest.mark.django_db
def test_multiple_trusted_not_conflict_without_overlap():
    """Multiple accepted tasks on one entity are not conflict without overlap evidence."""
    project = ProjectFactory()
    t1 = TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 10))
    t2 = TaskFactory(project=project, start_date=date(2025, 2, 1), end_date=date(2025, 2, 10))
    gid = "GID-MULTI"
    _bind(t1, gid, needs_review=False)
    _bind(t2, gid, needs_review=False)

    result = GovernanceStateClassifier.classify_entity(
        trusted_task_ids=[str(t1.pk), str(t2.pk)],
        review_task_ids=[],
        task_date_ranges={
            str(t1.pk): (t1.start_date, t1.end_date),
            str(t2.pk): (t2.start_date, t2.end_date),
        },
    )
    assert result.primary == GovernanceCategory.MULTIPLE_TRUSTED
    assert result.primary != GovernanceCategory.POSSIBLE_CONFLICT


@pytest.mark.django_db
def test_overlap_classifies_possible_conflict():
    """Overlapping accepted task dates classify possible_conflict."""
    project = ProjectFactory()
    t1 = TaskFactory(project=project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 20))
    t2 = TaskFactory(
        project=project,
        start_date=date(2025, 1, 10),
        end_date=date(2025, 1, 30),
    )
    gid = "GID-OVER"
    _bind(t1, gid, needs_review=False)
    _bind(t2, gid, needs_review=False)

    result = GovernanceStateClassifier.classify_entity(
        trusted_task_ids=[str(t1.pk), str(t2.pk)],
        review_task_ids=[],
        task_date_ranges={
            str(t1.pk): (t1.start_date, t1.end_date),
            str(t2.pk): (t2.start_date, t2.end_date),
        },
    )
    assert result.primary == GovernanceCategory.POSSIBLE_CONFLICT


@pytest.mark.django_db
def test_no_evidence_not_conflict():
    """Single trusted task is not classified as conflict."""
    result = GovernanceStateClassifier.classify_entity(
        trusted_task_ids=["one"],
        review_task_ids=[],
    )
    assert result.primary == GovernanceCategory.TRUSTED


@pytest.mark.django_db
def test_exact_evidence_distinct_from_normalized():
    """Exact and normalized evidence labels differ."""
    exact = evidence_label_for_binding(TaskEntityBinding.LinkMethod.EXACT, needs_review=False)
    norm = evidence_label_for_binding(TaskEntityBinding.LinkMethod.NORMALIZED, needs_review=True)
    assert exact == EvidenceLabel.EXACT_IDENTIFIER
    assert norm == EvidenceLabel.NORMALIZED_IDENTIFIER
    assert exact != norm


@pytest.mark.django_db
def test_normalized_confidence_does_not_imply_trust():
    """High-confidence normalized binding remains review-only in reader."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(
        task,
        "GID-N",
        needs_review=True,
        method=TaskEntityBinding.LinkMethod.NORMALIZED,
        confidence=0.95,
    )
    assert "GID-N" not in linked_entity_gids_for_project(project.pk)


@pytest.mark.django_db
def test_governance_summary_reconciles_counts():
    """Summary trusted/review counts match reader."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-A", needs_review=False)
    _bind(task, "GID-B", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC)

    summary = GovernanceSummaryService(str(project.pk)).build()
    assert summary["trusted_bindings"] == 1
    assert summary["review_bindings"] == 1
    assert summary["policy_id"] == TRUSTED_BINDING_POLICY_ID


@pytest.mark.django_db
def test_governance_summary_endpoint(client):
    """Summary endpoint is read-only and exposes policy version."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)
    url = reverse("scheduling:link_governance_summary", kwargs={"pk": project.pk})
    resp = client.get(url)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["policy_id"] == TRUSTED_BINDING_POLICY_ID


@pytest.mark.django_db
def test_repeated_reads_zero_writes():
    """Governance reads do not mutate bindings."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-W", needs_review=False)
    before = TaskEntityBinding.objects.count()
    reader = BindingGovernanceReader(project.pk)
    reader.trusted_entity_gids()
    GovernanceSummaryService(str(project.pk)).build()
    assert TaskEntityBinding.objects.count() == before


@pytest.mark.django_db
def test_summary_query_count_bounded():
    """Governance summary uses bounded queries on synthetic scale."""
    project = ProjectFactory()
    anchor = date(2025, 1, 1)
    for i in range(40):
        task = TaskFactory(
            project=project,
            start_date=anchor + timedelta(days=i % 10),
            end_date=anchor + timedelta(days=i % 10 + 3),
        )
        _bind(task, f"GID-{i}", needs_review=(i % 5 == 0))

    with CaptureQueriesContext(connection) as ctx:
        GovernanceSummaryService(str(project.pk)).build()
    assert len(ctx.captured_queries) <= 12


@pytest.mark.django_db
def test_health_check_review_only_task_is_unlinked():
    """Review-only physical tasks count as unlinked for trusted health check."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="R1")
    _bind(task, "GID-H", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC)

    result = run_health_check(project)
    codes = [issue["code"] for issue in result["issues"]]
    assert "unlinked_physical" in codes


@pytest.mark.django_db
def test_link_resolver_accepted_only_batch():
    """entity_gids_by_task accepted_only excludes review rows."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-A", needs_review=False)
    _bind(task, "GID-R", needs_review=True, method=TaskEntityBinding.LinkMethod.HEURISTIC)

    all_map = entity_gids_by_task(project.pk, [task.pk], accepted_only=False)
    trusted_map = entity_gids_by_task(project.pk, [task.pk], accepted_only=True)
    assert "GID-R" in all_map[str(task.pk)]
    assert trusted_map[str(task.pk)] == ["GID-A"]


@pytest.mark.django_db
def test_cross_project_entity_not_in_trusted_gids():
    """Entity GlobalIds from another project's IFC file are not trusted here."""
    p1 = ProjectFactory()
    p2 = ProjectFactory()
    ifc2 = IFCFileFactory(project=p2)
    entity = IFCEntityFactory(ifc_file=ifc2, global_id="GID-OTHER")
    task = TaskFactory(project=p1)
    _bind(task, entity.global_id, needs_review=False)

    assert entity.global_id not in BindingGovernanceReader(p1.pk).trusted_entity_gids(
        ifc_scope=True
    )


@pytest.mark.django_db
def test_legacy_evidence_label_is_compatibility_only():
    """Legacy M2M without binding maps to legacy compatibility, not trusted."""
    state = GovernanceStateClassifier.classify_task(
        trusted_count=0,
        review_count=0,
        legacy_m2m_count=2,
    )
    assert state.primary == GovernanceCategory.LEGACY_COMPATIBILITY
    assert state.trusted is False


@pytest.mark.django_db
def test_e2e_governance_migration_present():
    """E2-E adds the binding governance events migration."""
    from pathlib import Path

    mig_dir = Path(__file__).resolve().parents[1] / "migrations"
    assert (mig_dir / "0024_binding_governance_events.py").exists()
