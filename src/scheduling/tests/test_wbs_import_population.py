# scheduling/tests/test_wbs_import_population.py
"""DF-C2 canonical WBS import population, adapters, and backfill tests."""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.utils import timezone

from environments.tests.factories import ProjectFactory, UserFactory
from scheduling.models import (
    P6WBSNode,
    ScheduleImportRun,
    ScheduleSource,
    ScheduleSourceVersion,
    Task,
    WBSVersion,
)
from scheduling.parsers.p6xml_parser import parse_p6xml
from scheduling.services.p6_save import finalise_p6_data, save_p6_pending_data
from scheduling.services.source_version.import_persistence import (
    ImportPersistResult,
    attach_wbs_aux,
    persist_schedule_import,
)
from scheduling.services.wbs.adapters.registry import build_population_dto
from scheduling.services.wbs.contracts import CanonicalWBSPopulationDTO
from scheduling.services.wbs.exceptions import WBSValidationError
from scheduling.services.wbs.population import (
    CanonicalWBSPopulationService,
    validate_population_dto,
)
from scheduling.services.xer_parser import parse_xer
from scheduling.tests.fixtures import mspxml_bytes, p6xml_bytes, xer_bytes


def _source_version(project, **kwargs):
    return ScheduleSourceVersion.objects.create(
        project=project,
        version_number=kwargs.pop("version_number", 1),
        source_type=kwargs.pop("source_type", Task.Source.P6XML),
        source_filename=kwargs.pop("source_filename", "test.xml"),
        status=kwargs.pop("status", ScheduleSourceVersion.Status.CANDIDATE),
        imported_at=timezone.now(),
        **kwargs,
    )


@pytest.mark.django_db
class TestWBSPopulationDTO:
    def test_duplicate_external_id_rejected(self):
        dto = CanonicalWBSPopulationDTO(
            version=None,
            has_wbs_evidence=True,
            nodes=[
                type("N", (), {"external_id": "1", "name": "A"})(),
                type("N", (), {"external_id": "1", "name": "B"})(),
            ],
        )
        assert any("Duplicate" in e for e in validate_population_dto(dto))


@pytest.mark.django_db
class TestP6XmlPopulation:
    def test_nested_wbs_and_task_assignment(self):
        project = ProjectFactory()
        user = UserFactory()
        wbs_nodes = [
            {
                "ObjectId": "10",
                "ParentObjectId": "",
                "Code": "1",
                "Name": "Root",
                "SequenceNumber": "1",
            },
            {
                "ObjectId": "11",
                "ParentObjectId": "10",
                "Code": "1.1",
                "Name": "Child",
                "SequenceNumber": "2",
            },
        ]
        activities = [
            {
                "ObjectId": "100",
                "Id": "A1",
                "Name": "Task A",
                "WBSObjectId": "11",
                "PlannedStartDate": "2025-01-01T08:00:00",
                "PlannedFinishDate": "2025-01-05T08:00:00",
                "Status": "Not Started",
            },
            {
                "ObjectId": "101",
                "Id": "A2",
                "Name": "Task B",
                "WBSObjectId": "",
                "PlannedStartDate": "2025-01-06T08:00:00",
                "PlannedFinishDate": "2025-01-10T08:00:00",
                "Status": "Not Started",
            },
        ]
        tasks, deps, aux = parse_p6xml(
            io.BytesIO(p6xml_bytes(activities=activities, wbs_nodes=wbs_nodes))
        )
        save_p6_pending_data(project, aux)
        persist = persist_schedule_import(
            project,
            tasks_data=[
                {
                    **t,
                    "start_date": str(t["start_date"]),
                    "end_date": str(t["end_date"]),
                }
                for t in tasks
            ],
            raw_deps=deps,
            replace_mode=True,
            filename="nested.xml",
            source_format="p6xml",
            data_date=None,
        )
        attach_wbs_aux(persist, aux)
        assert len(persist.touched_pks) == 2
        if persist.p6_obj_id_map:
            finalise_p6_data(project, persist.current_source, persist.p6_obj_id_map)
        sv = _source_version(project)
        result = CanonicalWBSPopulationService(project, user).populate_from_import(
            source_version_id=str(sv.pk),
            source_type="p6xml",
            persist_result=persist,
            mode=ScheduleImportRun.Mode.REPLACE,
        )
        assert result.node_count == 2
        assert result.assigned_tasks == 1
        assert result.unassigned_tasks == 1
        version = WBSVersion.objects.get(pk=result.wbs_version_id)
        assert version.status == WBSVersion.Status.ACTIVE
        assigned = Task.objects.filter(project=project, wbs_node__isnull=False).count()
        assert assigned == 1

    def test_unknown_wbs_reference_warns_not_assigns(self):
        project = ProjectFactory()
        wbs_nodes = [{"ObjectId": "10", "ParentObjectId": "", "Code": "1", "Name": "Root"}]
        activities = [
            {
                "ObjectId": "100",
                "Id": "A1",
                "Name": "Task A",
                "WBSObjectId": "999",
                "PlannedStartDate": "2025-01-01T08:00:00",
                "PlannedFinishDate": "2025-01-05T08:00:00",
                "Status": "Not Started",
            },
        ]
        tasks, deps, aux = parse_p6xml(
            io.BytesIO(p6xml_bytes(activities=activities, wbs_nodes=wbs_nodes))
        )
        save_p6_pending_data(project, aux)
        persist = persist_schedule_import(
            project,
            tasks_data=[
                {**t, "start_date": str(t["start_date"]), "end_date": str(t["end_date"])}
                for t in tasks
            ],
            raw_deps=deps,
            replace_mode=True,
            filename="unknown.xml",
            source_format="p6xml",
            data_date=None,
        )
        attach_wbs_aux(persist, aux)
        if persist.p6_obj_id_map:
            finalise_p6_data(project, persist.current_source, persist.p6_obj_id_map)
        dto = build_population_dto("p6xml", persist)
        assert any(r.unresolved_reason for r in dto.task_references)


@pytest.mark.django_db
class TestXerPopulation:
    def test_projwbs_mapping_and_task_wbs_id(self):
        project = ProjectFactory()
        tasks_data = [
            {
                "task_id": "1",
                "task_code": "T1",
                "task_name": "One",
                "wbs_id": "w2",
                "target_start_date": "2025-01-01 08:00",
                "target_end_date": "2025-01-05 08:00",
                "status_code": "TK_NotStart",
            }
        ]
        wbs_table = (
            "PROJWBS",
            ["wbs_id", "parent_wbs_id", "wbs_short_name", "wbs_name", "proj_id"],
            [["w1", "", "1", "Root", "p1"], ["w2", "w1", "1.1", "Child", "p1"]],
        )
        parsed_tasks, _, aux = parse_xer(
            io.BytesIO(xer_bytes(tasks=tasks_data, extra_tables=[wbs_table]))
        )
        persist = persist_schedule_import(
            project,
            tasks_data=[
                {**t, "start_date": str(t["start_date"]), "end_date": str(t["end_date"])}
                for t in parsed_tasks
            ],
            raw_deps=[],
            replace_mode=True,
            filename="t.xer",
            source_format="xer",
            data_date=None,
        )
        attach_wbs_aux(persist, aux)
        dto = build_population_dto("xer", persist)
        assert dto.has_wbs_evidence
        assert len(dto.nodes) == 2
        assert dto.task_references[0].external_wbs_id == "w2"


@pytest.mark.django_db
class TestMspPopulation:
    def test_summary_outline_hierarchy(self):
        from scheduling.services.msp_parser import parse_msp

        tasks = [
            {
                "UID": "1",
                "Name": "Summary Root",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-01-20T08:00:00",
                "Summary": "1",
                "OutlineNumber": "1",
                "OutlineLevel": "1",
                "Milestone": "0",
            },
            {
                "UID": "2",
                "Name": "Leaf Task",
                "Start": "2025-01-02T08:00:00",
                "Finish": "2025-01-10T08:00:00",
                "Summary": "0",
                "OutlineNumber": "1.1",
                "OutlineLevel": "2",
                "Milestone": "0",
            },
        ]
        parsed, _, aux = parse_msp(io.BytesIO(mspxml_bytes(tasks=tasks)))
        assert len(aux.get("summary_nodes", [])) == 1
        assert parsed[1].get("_msp_wbs_uid") == "1"


@pytest.mark.django_db
class TestBackfillCommand:
    def test_dry_run_default_no_writes(self):
        project = ProjectFactory()
        P6WBSNode.objects.create(
            project=project,
            p6_object_id="10",
            p6_parent_object_id="",
            code="1",
            name="Root",
            is_pending=False,
        )
        before_versions = WBSVersion.objects.filter(project=project).count()
        call_command("backfill_canonical_wbs", project=str(project.pk))
        assert WBSVersion.objects.filter(project=project).count() == before_versions


@pytest.mark.django_db
class TestImportTransactionRollback:
    def test_failed_hierarchy_keeps_prior_selection(self, monkeypatch):
        project = ProjectFactory()
        user = UserFactory()
        sv_old = _source_version(
            project, version_number=1, status=ScheduleSourceVersion.Status.CURRENT
        )
        old_wbs = WBSVersion.objects.create(
            project=project,
            source_version=sv_old,
            name="Prior",
            origin=WBSVersion.Origin.MANUAL,
            status=WBSVersion.Status.ACTIVE,
            is_selected_for_analysis=True,
            revision_number=1,
        )
        source = ScheduleSource.objects.create(
            project=project, filename="bad.xml", source_format="p6xml", task_count=0
        )
        P6WBSNode.objects.create(
            project=project,
            schedule_source=source,
            p6_object_id="1",
            p6_parent_object_id="",
            code="1",
            name="Root",
            is_pending=False,
        )
        persist = ImportPersistResult(
            touched_pks=[],
            touched_task_data=[],
            current_source=source,
        )
        sv_new = _source_version(project, version_number=2)

        def _fail_integrity(self):
            return {"valid": False, "orphan_count": 1, "node_count": 1}

        from scheduling.services.wbs.hierarchy import WBSHierarchyService

        monkeypatch.setattr(WBSHierarchyService, "validate_integrity", _fail_integrity)

        with pytest.raises(WBSValidationError):
            CanonicalWBSPopulationService(project, user).populate_from_import(
                source_version_id=str(sv_new.pk),
                source_type="p6xml",
                persist_result=persist,
                mode=ScheduleImportRun.Mode.UPDATE,
            )
        old_wbs.refresh_from_db()
        assert old_wbs.is_selected_for_analysis
        assert (
            WBSVersion.objects.filter(project=project, status=WBSVersion.Status.ACTIVE).count() == 1
        )
