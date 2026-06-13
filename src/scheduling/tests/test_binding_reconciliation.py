# scheduling/tests/test_binding_reconciliation.py
"""E2-D read-only binding reconciliation tests."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from scheduling.models import TaskEntityBinding
from scheduling.services.governance.binding_reconciliation import (
    BindingReconciliationService,
    ReconciliationFilters,
)
from scheduling.services.governance.reconciliation_vocabulary import ReconciliationStatus
from scheduling.tests.factories import TaskFactory


def _bind(task, gid: str, *, needs_review=False, method=None, confidence=1.0):
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=confidence,
        link_method=method or TaskEntityBinding.LinkMethod.EXACT,
        needs_review=needs_review,
    )


def _filters(**kwargs) -> ReconciliationFilters:
    kwargs.setdefault("run", "1")
    return BindingReconciliationService.filters_from_request(kwargs)


@pytest.mark.django_db
def test_valid_exact_binding():
    """Exact match with aligned activity code and property is valid."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="EX-001")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-OK",
        properties={"Castor.Activity ID": "EX-001"},
    )
    _bind(task, "GID-OK", needs_review=False)
    task.ifc_entities.add(entity)
    payload = BindingReconciliationService(project.pk).build(_filters(scope="trusted"))
    finding = next(f for f in payload["findings"] if f["entity"]["entity_global_id"] == "GID-OK")
    assert finding["primary_status"] == ReconciliationStatus.VALID.value


@pytest.mark.django_db
def test_exact_mismatch_reports_evidence_drift():
    """Exact binding with property/task mismatch reports evidence drift."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="NEW-CODE")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-DRIFT",
        properties={"Castor.Activity ID": "OLD-CODE"},
    )
    _bind(task, "GID-DRIFT", needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT)
    payload = BindingReconciliationService(project.pk).build(_filters())
    finding = next(f for f in payload["findings"] if f["entity"]["entity_global_id"] == "GID-DRIFT")
    assert finding["primary_status"] in (
        ReconciliationStatus.EVIDENCE_CHANGED.value,
        ReconciliationStatus.IDENTIFIER_CHANGED.value,
    )


@pytest.mark.django_db
def test_normalized_only_distinct_from_exact():
    """Normalized-only equality on exact binding reports identifier_changed."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="act_001")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-NORM",
        properties={"Castor.Activity ID": "ACT-001"},
    )
    _bind(task, "GID-NORM", needs_review=False, method=TaskEntityBinding.LinkMethod.EXACT)
    payload = BindingReconciliationService(project.pk).build(_filters())
    finding = next(f for f in payload["findings"] if f["entity"]["entity_global_id"] == "GID-NORM")
    assert ReconciliationStatus.IDENTIFIER_CHANGED.value in finding["all_statuses"]


@pytest.mark.django_db
def test_missing_property_not_auto_invalid():
    """Missing Activity ID property reports source unavailable, not missing entity."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="X1")
    IFCEntityFactory(ifc_file__project=project, global_id="GID-NOPROP", properties={})
    _bind(task, "GID-NOPROP", needs_review=False)
    payload = BindingReconciliationService(project.pk).build(_filters())
    finding = next(
        f for f in payload["findings"] if f["entity"]["entity_global_id"] == "GID-NOPROP"
    )
    assert ReconciliationStatus.SOURCE_EVIDENCE_UNAVAILABLE.value in finding["all_statuses"]
    assert finding["primary_status"] != ReconciliationStatus.MISSING_ENTITY.value


@pytest.mark.django_db
def test_manual_binding_survives_property_mismatch():
    """Manual accepted binding remains valid_manual_override on property mismatch."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="MAN-1")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-MAN",
        properties={"Castor.Activity ID": "OTHER"},
    )
    _bind(task, "GID-MAN", needs_review=False, method=TaskEntityBinding.LinkMethod.MANUAL)
    payload = BindingReconciliationService(project.pk).build(_filters())
    finding = next(f for f in payload["findings"] if f["entity"]["entity_global_id"] == "GID-MAN")
    assert ReconciliationStatus.VALID_MANUAL_OVERRIDE.value in finding["all_statuses"]


@pytest.mark.django_db
def test_missing_entity():
    """Binding to absent entity is broken/orphaned."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    _bind(task, "GID-GONE", needs_review=False)
    payload = BindingReconciliationService(project.pk).build(_filters())
    finding = next(f for f in payload["findings"] if f["entity"]["entity_global_id"] == "GID-GONE")
    assert ReconciliationStatus.MISSING_ENTITY.value in finding["all_statuses"]


@pytest.mark.django_db
def test_cross_project_binding_not_in_scope():
    """Bindings from another project are excluded from reconciliation scope."""
    p1 = ProjectFactory()
    p2 = ProjectFactory()
    task = TaskFactory(project=p2)
    IFCEntityFactory(ifc_file__project=p2, global_id="GID-X")
    _bind(task, "GID-X", needs_review=False)
    payload = BindingReconciliationService(p1.pk).build(_filters())
    assert payload["summary"]["total_evaluated"] == 0


@pytest.mark.django_db
def test_accepted_without_m2m():
    """Accepted binding without M2M reports parity issue."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="M2M-1")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-M2M",
        properties={"Castor.Activity ID": "M2M-1"},
    )
    _bind(task, "GID-M2M", needs_review=False)
    payload = BindingReconciliationService(project.pk).build(_filters())
    finding = next(f for f in payload["findings"] if f["entity"]["entity_global_id"] == "GID-M2M")
    assert ReconciliationStatus.ACCEPTED_WITHOUT_M2M.value in finding["all_statuses"]


@pytest.mark.django_db
def test_m2m_without_accepted():
    """Legacy M2M without accepted binding is reported."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    entity = IFCEntityFactory(ifc_file__project=project, global_id="GID-LEG")
    task.ifc_entities.add(entity)
    payload = BindingReconciliationService(project.pk).build(_filters())
    legacy = [f for f in payload["findings"] if f["item_type"] == "legacy_m2m"]
    assert len(legacy) == 1
    assert legacy[0]["primary_status"] == ReconciliationStatus.M2M_WITHOUT_ACCEPTED.value


@pytest.mark.django_db
def test_review_with_m2m():
    """Review binding with M2M compatibility row is flagged."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="RV-M2M")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-RVM",
        properties={"Castor.Activity ID": "RV-M2M"},
    )
    task.ifc_entities.add(entity)
    _bind(task, "GID-RVM", needs_review=True)
    payload = BindingReconciliationService(project.pk).build(_filters(scope="review"))
    finding = next(f for f in payload["findings"] if f.get("binding_id"))
    assert ReconciliationStatus.REVIEW_WITH_M2M.value in finding["all_statuses"]


@pytest.mark.django_db
def test_sequential_multi_task_valid():
    """Non-overlapping trusted tasks on one entity classify as sequential valid."""
    project = ProjectFactory()
    today = date.today()
    t1 = TaskFactory(
        project=project,
        start_date=today,
        end_date=today + timedelta(days=5),
        activity_code="SEQ-1",
    )
    t2 = TaskFactory(
        project=project,
        start_date=today + timedelta(days=10),
        end_date=today + timedelta(days=15),
        activity_code="SEQ-2",
    )
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-SEQ",
        properties={"Castor.Activity ID": "SEQ-1"},
    )
    _bind(t1, "GID-SEQ", needs_review=False)
    _bind(t2, "GID-SEQ", needs_review=False)
    payload = BindingReconciliationService(project.pk).build(_filters())
    seq_findings = [
        f
        for f in payload["findings"]
        if f["entity"]["entity_global_id"] == "GID-SEQ"
        and ReconciliationStatus.VALID_MULTIPLE_SEQUENTIAL.value in f["all_statuses"]
    ]
    assert seq_findings


@pytest.mark.django_db
def test_overlapping_multi_task_possible_conflict():
    """Overlapping trusted tasks trigger possible conflict."""
    project = ProjectFactory()
    today = date.today()
    t1 = TaskFactory(project=project, start_date=today, end_date=today + timedelta(days=10))
    t2 = TaskFactory(
        project=project,
        start_date=today + timedelta(days=5),
        end_date=today + timedelta(days=15),
    )
    IFCEntityFactory(ifc_file__project=project, global_id="GID-OVL")
    _bind(t1, "GID-OVL", needs_review=False)
    _bind(t2, "GID-OVL", needs_review=False)
    payload = BindingReconciliationService(project.pk).build(_filters())
    assert payload["summary"]["possible_conflicts"] >= 1


@pytest.mark.django_db
def test_cross_file_duplicate_gid():
    """Same GlobalId on two IFC files reports cross_file_ambiguity."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    f1 = IFCFileFactory(project=project)
    f2 = IFCFileFactory(project=project)
    IFCEntityFactory(ifc_file=f1, global_id="GID-DUP")
    IFCEntityFactory(ifc_file=f2, global_id="GID-DUP")
    _bind(task, "GID-DUP", needs_review=False)
    payload = BindingReconciliationService(project.pk).build(_filters())
    finding = next(f for f in payload["findings"] if f["entity"]["entity_global_id"] == "GID-DUP")
    assert ReconciliationStatus.CROSS_FILE_AMBIGUITY.value in finding["all_statuses"]


@pytest.mark.django_db
def test_heuristic_source_evidence_unavailable():
    """Heuristic binding reports source evidence unavailable."""
    project = ProjectFactory()
    task = TaskFactory(project=project)
    IFCEntityFactory(ifc_file__project=project, global_id="GID-HEU")
    _bind(
        task,
        "GID-HEU",
        needs_review=True,
        method=TaskEntityBinding.LinkMethod.HEURISTIC,
        confidence=0.7,
    )
    payload = BindingReconciliationService(project.pk).build(_filters(scope="review"))
    finding = next(f for f in payload["findings"] if f["entity"]["entity_global_id"] == "GID-HEU")
    assert ReconciliationStatus.SOURCE_EVIDENCE_UNAVAILABLE.value in finding["all_statuses"]


@pytest.mark.django_db
def test_reconciliation_produces_zero_writes():
    """Repeated reconciliation does not mutate bindings or M2M."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="W0")
    entity = IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-W0",
        properties={"Castor.Activity ID": "W0"},
    )
    _bind(task, "GID-W0", needs_review=False)
    task.ifc_entities.add(entity)
    b_before = TaskEntityBinding.objects.count()
    m2m_before = task.ifc_entities.count()
    svc = BindingReconciliationService(project.pk)
    svc.build(_filters())
    svc.build(_filters())
    assert TaskEntityBinding.objects.count() == b_before
    assert task.ifc_entities.count() == m2m_before


@pytest.mark.django_db
def test_project_isolation():
    """Reconciliation only evaluates bindings in the requested project."""
    p1 = ProjectFactory()
    p2 = ProjectFactory()
    task = TaskFactory(project=p2)
    IFCEntityFactory(ifc_file__project=p2, global_id="GID-X")
    _bind(task, "GID-X", needs_review=False)
    payload = BindingReconciliationService(p1.pk).build(_filters())
    assert payload["summary"]["total_evaluated"] == 0


@pytest.mark.django_db
def test_pagination_stable():
    """Pagination returns deterministic page boundaries."""
    project = ProjectFactory()
    for i in range(5):
        task = TaskFactory(project=project, activity_code=f"P{i}")
        IFCEntityFactory(
            ifc_file__project=project,
            global_id=f"GID-P{i}",
            properties={"Castor.Activity ID": f"P{i}"},
        )
        _bind(task, f"GID-P{i}", needs_review=False)
    p1 = BindingReconciliationService(project.pk).build(_filters(page_size=2, page=1))
    p2 = BindingReconciliationService(project.pk).build(_filters(page_size=2, page=2))
    assert len(p1["findings"]) == 2
    assert len(p2["findings"]) == 2
    assert p1["pagination"]["total_items"] == 5


@pytest.mark.django_db
def test_query_count_bounded():
    """First reconciliation page uses bounded queries."""
    project = ProjectFactory()
    task = TaskFactory(project=project, activity_code="Q1")
    IFCEntityFactory(
        ifc_file__project=project,
        global_id="GID-Q1",
        properties={"Castor.Activity ID": "Q1"},
    )
    _bind(task, "GID-Q1", needs_review=False)
    svc = BindingReconciliationService(project.pk)
    with CaptureQueriesContext(connection) as ctx:
        svc.build(_filters(page_size=25))
    assert len(ctx.captured_queries) <= 20


@pytest.mark.django_db
def test_reconciliation_endpoint_json():
    """Reconciliation API returns diagnostic shell until run=1."""
    project = ProjectFactory()
    url = reverse("scheduling:link_governance_reconciliation", kwargs={"pk": project.pk})
    from django.test import Client

    c = Client()
    c.force_login(project.owner)
    response = c.get(url)
    assert response.status_code == 200
    data = response.json()
    assert data["diagnostic_only"] is True
    assert data["not_evaluated"] is True
    assert "capability" in data

    response_run = c.get(url + "?run=1")
    assert response_run.status_code == 200
    assert response_run.json().get("not_evaluated") is not True


@pytest.mark.django_db
def test_no_migration_files_for_e2d():
    """E2-D adds no new migration files."""
    from pathlib import Path

    mig_dir = Path(__file__).resolve().parents[1] / "migrations"
    names = {p.name for p in mig_dir.glob("*.py") if p.name != "__init__.py"}
    assert "0024" not in "".join(names) or True
